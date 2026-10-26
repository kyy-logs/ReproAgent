"""Bounded original-only pitfall episodes, independent of the verdict projection."""
from __future__ import annotations

import ast
from collections import Counter, OrderedDict, deque
from dataclasses import asdict, dataclass

from .core.models import CandidateClass, EvidenceRef, TaskState
from .core.protocol import learning_schema
from .core.serialization import bytes_hash, canonical_bytes, canonical_hash
from .paths import is_within, relative_name, workspace_path
from .store import atomic_write, safe_child
from .workspace import candidate_hash
from .experience import INPUT_BYTES, CATEGORIES, LearningInput, _check, _redact
from .experience_triggers import ERROR_METADATA, trigger_from_fact, TOOLS

ATOM_BYTES = 2048
MAX_ENTRIES = 24
MAX_OMISSIONS = 16
MAX_VERDICT_DIAGNOSTICS = 3
PROTOCOL_DIAGNOSTICS = frozenset({'invalid_negative_response', 'MODEL_PROTOCOL_ERROR',
    'SEMANTIC_CITATIONS_INCOMPLETE', 'ObjectRootError', 'DuplicateJSONFieldError',
    'NonFiniteJSONValueError', 'JSONDecodeError', 'ValueError'})
CONTROLLED = frozenset(('UNKNOWN_TOOL', 'MULTIPLE_TOOL_CALLS', 'INVALID_TOOL_INPUT',
    'OUTPUT_TRUNCATED', 'OUTPUT_FILTERED', 'EMPTY_OUTPUT', 'RESPONSE_TOO_LARGE', 'INVALID_PROTOCOL',
    'MODEL_OUTPUT_ERROR', 'MODEL_PROTOCOL_ERROR', 'EXHAUSTED', 'DENIED', 'RESERVED_FOR_PUBLISHING',
    'REVIEW_CONTEXT_TOO_LARGE', 'DUPLICATE_CANDIDATE', 'INVALID_ARGUMENT', 'MISSING_FILE',
    'SOURCE_RANGE_NOT_DISPLAYED', 'SOURCE_HASH_MISMATCH', 'GROUNDED_CONTRACT_REQUIRED'))


@dataclass(frozen=True, slots=True)
class LearningBuildResult:
    material: LearningInput | None
    code: str


def _lines(data):
    lines = data.decode('utf-8').splitlines()
    if len(lines) != len(data.splitlines()):
        raise ValueError('ambiguous source line boundaries')
    return lines


def _ref(store, path):
    data = path.read_bytes()
    return EvidenceRef(relative_name(path, store.root), bytes_hash(data), 1, max(1, len(data.splitlines())))


def _candidate_fragment(data, failed_names):
    """Whole source when small; otherwise one complete relevant AST function."""
    lines = _lines(data)
    text = data.decode('utf-8')
    if len(canonical_bytes({'content': text})) <= ATOM_BYTES - 300:
        return text, 1, max(1, len(data.splitlines())), False
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in failed_names:
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            end = node.end_lineno
            if end is not None:
                fragment = '\n'.join(lines[start - 1:end])
                if len(canonical_bytes({'content': fragment})) <= ATOM_BYTES - 300:
                    return fragment, start, end, True
    return None


def capture_exploration(messages, source_refs, *, secrets=(), draft=None):
    """Copy a few complete SDK tool failures; never copy chat or memory content.

    Association is chronological: a repeated tool-call ID cannot attach a later
    call's arguments to an earlier result. Window/entry omissions are explicit.
    This capture is independent of diagnostic tracing.
    """
    import json
    from .core.serialization import canonical_hash
    from agentscope.message import ToolCallBlock, ToolResultBlock, TextBlock
    allowed = frozenset(('Read','Grep','Glob','write_candidate','revise_contract','request_information'))
    errors, issues, pending, calls = deque(maxlen=8), [], OrderedDict(), []
    call_counts = Counter()
    def issue(code):
        if code not in issues and len(issues) < 8:
            issues.append(code)
    if len(messages) > 512:
        issue('MESSAGE_WINDOW_LIMIT')
    if len(source_refs) > 16:
        issue('SOURCE_REF_LIMIT')
    for message_index, message in enumerate(messages[-512:]):
        blocks = message.content
        if len(blocks) > 64:
            issue('MESSAGE_BLOCK_LIMIT')
        for block_index, block in enumerate(blocks[:64]):
            if isinstance(block, ToolCallBlock):
                call_counts[block.id] += 1
                if block.name in allowed:
                    pending[block.id] = block
                    calls.append((max(0, len(messages) - 512) + message_index, block_index, block))
                    if len(pending) > 64:
                        pending.popitem(last=False)
                        issue('TOOL_LINK_WINDOW_LIMIT')
                continue
            if not isinstance(block, ToolResultBlock) or block.name not in allowed:
                continue
            call = pending.pop(block.id, None)
            state = getattr(block.state, 'value', block.state)
            if state not in ('error','denied','interrupted'):
                continue
            output = block.output
            if isinstance(output, list):
                if not all(isinstance(part, TextBlock) for part in output):
                    issue('NON_TEXT_TOOL_RESULT')
                    continue
                output = '\n'.join(part.text for part in output)
            record = {'tool':block.name, 'tool_call_id':block.id, 'state':state,
                'message_offset':max(0,len(messages)-512)+message_index,
                'block_index':block_index, 'arguments':None, 'result':output}
            if call is None or call.name != block.name:
                record['link_state'] = 'missing'
                issue('MISSING_TOOL_CALL')
            else:
                try:
                    arguments = json.loads(call.input)
                    if not isinstance(arguments, dict):
                        raise ValueError('arguments are not an object')
                    record['arguments'] = arguments
                    record['link_state'] = 'matched'
                except (ValueError, TypeError):
                    record['link_state'] = 'invalid_arguments'
                    issue('INVALID_TOOL_INPUT')
            metadata = getattr(block, 'metadata', {})
            code = metadata.get(ERROR_METADATA) if isinstance(metadata, dict) else None
            if state == 'error' and trigger_from_fact({'kind':'tool_error','tool':block.name,'code':code}):
                record['error_code'] = code
            record = json.loads(_redact(canonical_bytes(record).decode('utf-8'), secrets))
            if len(canonical_bytes(record)) > ATOM_BYTES:
                issue('TOOL_ERROR_TOO_LARGE')
                continue
            if len(errors) == errors.maxlen:
                issue('TOOL_ERROR_LIMIT')
            errors.append(record)
    refs = tuple(ref for ref in source_refs[-16:] if isinstance(ref, EvidenceRef)
        and not any(secret and secret in ref.path for secret in secrets))
    normalized_counts = Counter()
    for key, count in call_counts.items():
        normalized_counts[_redact(key, secrets)] += count
    result = {'source_refs':refs, 'tool_errors':tuple(errors),
              'ambiguous_call_ids': tuple(key for key, count in normalized_counts.items() if count > 1)}
    if draft is not None:
        matches = [item for item in calls if item[2].id == draft.call_id]
        if len(matches) != 1 or call_counts[draft.call_id] != 1:
            issue('DRAFT_CALL_AMBIGUOUS' if matches or call_counts[draft.call_id] else 'DRAFT_CALL_NOT_FOUND')
        else:
            offset, block_index, call = matches[0]
            try:
                arguments = json.loads(call.input)
                if not isinstance(arguments, dict):
                    raise ValueError
                if canonical_hash(arguments) != draft.arguments_hash:
                    issue('DRAFT_ARGUMENTS_MISMATCH')
                else:
                    result['draft_origin'] = {'call_id': draft.call_id, 'message_offset': offset,
                                              'block_index': block_index,
                                              'arguments_hash': draft.arguments_hash}
            except (ValueError, TypeError):
                issue('DRAFT_ARGUMENTS_MISMATCH')
    result['capture_issues'] = tuple(issues)
    return result


def prepare_learning_input(store, result, context, *, secrets=(), exploration=None):
    """Select factual atoms, freeze them once, and explain skipped build outcomes."""
    try:
        return _prepare(store, result, context, secrets=secrets, exploration=exploration or {})
    except (OSError, ValueError, TypeError, UnicodeError):
        return LearningBuildResult(None, 'skipped_invalid_evidence')


def prepare_review_input(store, result, context, *, draft, exploration, secrets=()):
    """Reserve the review envelope and required sources before optional context."""
    origin = (exploration or {}).get('draft_origin')
    if (not isinstance(origin, dict) or origin.get('call_id') != draft.call_id
            or origin.get('arguments_hash') != draft.arguments_hash or _position(origin) is None):
        return LearningBuildResult(None, 'skipped_draft_evidence_unavailable')
    try:
        return _prepare(store, result, context, secrets=secrets, exploration=exploration or {}, review=draft)
    except (OSError, ValueError, TypeError, UnicodeError):
        return LearningBuildResult(None, 'skipped_invalid_evidence')


def _position(record):
    values = (record.get('message_offset'), record.get('block_index'))
    return values if all(type(value) is int and value >= 0 for value in values) else None


def _review_selection(payload, entries, draft, exploration, original_seqs, registered=None):
    """Select whole atoms; never freeze unrelated atoms at the expense of a draft source."""
    import copy
    import json
    origin = _position(exploration['draft_origin'])
    mapping = {}
    registered = registered or {}
    records = [(entry, json.loads(entry['text'])) for entry in entries if entry['kind'] in ('tool_error', 'attempt')]
    for source_id in draft.draft.source_ids:
        if source_id in registered:
            fact = registered[source_id]
            if fact['kind'] == 'original_run':
                run_id = fact['run_id']
                if run_id not in original_seqs or original_seqs[run_id] >= draft.event_seq:
                    return None
                matches = [entry['id'] for entry, record in records if entry['kind'] == 'attempt'
                    and (record.get('run_id') == run_id or run_id in record.get('run_ids', ()))]
            else:
                matches = [entry['id'] for entry, record in records if entry['kind'] == 'tool_error'
                    and record.get('source_id') == source_id]
            if len(matches) != 1:
                return None
            mapping[source_id] = matches[0]
            continue
        if source_id == draft.call_id or source_id in exploration.get('ambiguous_call_ids', ()):
            return None
        # Check ambiguity before selection, not only among atoms that happened to fit.
        raw_errors = [e for e in exploration.get('tool_errors', ()) if e.get('tool_call_id') == source_id]
        if raw_errors:
            if len(raw_errors) != 1 or raw_errors[0].get('link_state') != 'matched':
                return None
            position = _position(raw_errors[0])
            if position is None or position >= origin:
                return None
        elif source_id not in original_seqs or original_seqs[source_id] >= draft.event_seq:
            return None
        matches = []
        for entry, record in records:
            if entry.get('kind') == 'tool_error':
                if record.get('tool_call_id') == source_id and raw_errors:
                    matches.append(entry['id'])
            elif entry.get('kind') == 'attempt' and (source_id in record.get('run_ids', ())
                    or record.get('run_id') == source_id):
                matches.append(entry['id'])
        if len(matches) != 1:
            return None
        mapping[source_id] = matches[0]
    payload['draft_evidence_ids'] = list(mapping.values())
    original = copy.deepcopy(payload)
    def project(selected):
        ids = {entry['id'] for entry in selected}
        payload['evidence'] = [{k: v for k, v in entry.items() if k != 'origin_refs'} for entry in selected]
        payload['category_materials'] = {category: [i for i in values if i in ids]
            for category, values in original['category_materials'].items()}
        payload['attempts'] = [{**attempt, 'evidence_ids': [i for i in attempt['evidence_ids'] if i in ids]}
            for attempt in original['attempts'] if ids.intersection(attempt['evidence_ids'])]

    mandatory_ids = set(mapping.values()) | {e['id'] for e in entries if e['kind'] == 'issue'}
    selected = [e for e in entries if e['id'] in mandatory_ids]
    payload['omissions'] = []
    project(selected)
    if len(canonical_bytes(payload)) > INPUT_BYTES:
        return None
    omitted = list(original['omissions'])
    for entry in entries:
        if entry['id'] in mandatory_ids:
            continue
        proposed = [*selected, entry]
        project(proposed)
        if len(proposed) > MAX_ENTRIES or len(canonical_bytes(payload)) > INPUT_BYTES - 256:
            omitted.append({'kind': entry['kind'], 'code': 'INPUT_BUDGET'})
            project(selected)
        else:
            selected = proposed
    for item in omitted:
        if len(payload['omissions']) >= MAX_OMISSIONS:
            break
        payload['omissions'].append(item)
        if len(canonical_bytes(payload)) > INPUT_BYTES:
            payload['omissions'].pop()
            break
    return selected


def _prepare(store, result, context, *, secrets, exploration, review=None):
    _check(context)
    if result.status == TaskState.CANCELLED:
        return LearningBuildResult(None, 'skipped_no_evidence')
    events, errors = store.read_events()
    if errors:
        return LearningBuildResult(None, 'skipped_invalid_evidence')
    learning_refs = {}
    if review is not None:
        from .experience_sources import learning_sources
        learning_refs = learning_sources(events, before=review.event_seq)
    capsule = safe_child(store.root, 'learning/evidence.jsonl')
    if capsule.exists():
        return LearningBuildResult(None, 'skipped_existing_evidence')
    entries = []
    payload = {'task_status': result.status.value, 'evidence': [], 'attempts': [],
        'category_materials': {name: [] for name in CATEGORIES}, 'omissions': [],
        'response_schema': learning_schema()}
    if review is not None:
        from .experience_review import review_schema
        payload.update(draft={**asdict(review.draft), 'source_ids': list(review.draft.source_ids)},
                       draft_evidence_ids=[], response_schema=review_schema())

    def omit(kind, code, needed_bytes=0):
        if len(payload['omissions']) < MAX_OMISSIONS:
            item = {'kind':kind, 'code':code}
            if needed_bytes:
                item['needed_bytes'] = needed_bytes
            # Diagnostics themselves must never violate the same budget.
            payload['omissions'].append(item)
            if len(canonical_bytes(payload)) > INPUT_BYTES:
                payload['omissions'].pop()

    def add(kind, value, origins=(), categories=CATEGORIES, *, mandatory=False):
        _check(context)
        text = value if isinstance(value, str) else canonical_bytes(value).decode('utf-8')
        text = _redact(text, secrets)
        if any(secret and secret in ref.path for ref in origins for secret in secrets):
            omit(kind, 'SECRET_IN_SOURCE_PATH')
            return None
        entry = {'id':'E' + str(len(entries) + 1), 'kind':kind, 'text':text}
        size = len(canonical_bytes(entry))
        if not mandatory and (size > ATOM_BYTES or review is None and len(entries) >= MAX_ENTRIES):
            omit(kind, 'ATOM_TOO_LARGE' if size > ATOM_BYTES else 'ENTRY_LIMIT', size)
            return None
        payload['evidence'].append(entry)
        for category in categories:
            payload['category_materials'][category].append(entry['id'])
        if review is None and len(canonical_bytes(payload)) > (INPUT_BYTES if mandatory else INPUT_BYTES - 256):
            payload['evidence'].pop()
            for category in categories:
                payload['category_materials'][category].pop()
            omit(kind, 'INPUT_BUDGET', size)
            return None
        entries.append({**entry, 'origin_refs':[asdict(ref) for ref in origins]})
        return entry['id']

    issue = safe_child(store.root, 'input/issue.md')
    if issue.is_file():
        data = issue.read_bytes()
        if len(data) > INPUT_BYTES or add('issue', data.decode('utf-8'), (_ref(store, issue),), mandatory=True) is None:
            return LearningBuildResult(None, 'skipped_input_too_large')

    originals, original_seqs = [], {}
    seen = set()
    for event in events:
        if event.kind != 'run.completed':
            continue
        for source in event.refs:
            parts = source.split('/')
            if len(parts) != 3 or parts[0] != 'runs' or parts[-1] != 'execution.json' or parts[1] in seen:
                continue
            seen.add(parts[1])
            run = store.load_record('runs', parts[1])
            if run.execution_role == 'original':
                originals.append(run)
                original_seqs[run.run_id] = event.seq
    notes = [{'seq':event.seq, 'kind':event.kind, 'code':event.payload.get('result_code'),
        'tool':event.payload.get('action') if event.payload.get('action') in TOOLS else '',
        **({'action':'run_candidate'} if event.kind == 'action.rejected'
            and event.payload.get('action') == 'run_candidate'
            and event.payload.get('result_code') == 'REVIEW_CONTEXT_TOO_LARGE' else {})}
        for event in events if event.kind in ('exploration.protocol_error', 'exploration.action', 'exploration.tool_error',
            'exploration.phase_error', 'phase.error', 'phase.result', 'action.rejected')
        and event.payload.get('result_code') in CONTROLLED]
    steps = sum(event.kind == 'exploration.step' for event in events)
    reads = sum(event.kind == 'exploration.action' and event.payload.get('action') in ('Read','Grep','Glob')
        and event.payload.get('result_code') == 'ALLOWED' for event in events)
    tool_errors = exploration.get('tool_errors', ())
    if not originals and not notes and not steps and not tool_errors:
        return LearningBuildResult(None, 'skipped_no_evidence')
    add('workflow', {'steps':steps, 'read_calls':reads, 'original_executions':len(originals),
        'task_status':result.status.value, 'stop_reason':result.stop_reason}, categories=('workflow',))

    groups = {}
    for run in originals:
        groups.setdefault(run.candidate_id, []).append(run)
    # IDs/hypotheses change even when the executed code and settings do not.
    # Retain the nearest actual adjustment, rather than two identical retries.
    candidates = {key:store.load_record('candidates', key) for key in groups}
    def fingerprint(candidate):
        return canonical_hash({name:getattr(candidate, name) for name in (
            'snapshot_id', 'contract_id', 'contract_version', 'files', 'selectors',
            'run_options', 'fixture_refs', 'preconditions', 'expectation_sources')})
    selected = []
    if groups:
        latest = next(reversed(groups))
        selected = [latest]
        previous = next((key for key in reversed(groups) if key != latest
            and fingerprint(candidates[key]) != fingerprint(candidates[latest])), None)
        if previous is None and len(groups) > 1:
            previous = list(groups)[-2]
        if previous is not None:
            selected.insert(0, previous)
    if review is not None:
        required_runs = {learning_refs[s]['run_id'] for s in review.draft.source_ids
                         if s in learning_refs and learning_refs[s]['kind'] == 'original_run'}
        required_candidates = [run.candidate_id for run in originals
                              if run.run_id in review.draft.source_ids or run.run_id in required_runs]
        selected = list(dict.fromkeys([*required_candidates, *selected]))
    registered = {}
    # Reading source may precede the first candidate; the freeze event is the
    # original registration boundary even when no test could be published.
    # Controller freezes the task original first, and may freeze the supplied
    # fixed version later. Only the first freeze can supply exploration facts;
    # original executions must independently bind to that same snapshot.
    first_freeze = next((event for event in events if event.kind == 'snapshot.created'), None)
    original_ids = {run.snapshot_id for run in originals}
    primary_id = first_freeze.payload.get('snapshot_id') if first_freeze is not None else next(iter(original_ids), None)
    if (len(original_ids) > 1 or first_freeze is not None and not isinstance(primary_id, str)
        or original_ids and primary_id not in original_ids):
        raise ValueError('ambiguous original snapshot registration')
    if primary_id is not None:
        snapshot = store.load_record('snapshots', primary_id)
        if not is_within(snapshot.root, safe_child(store.root, 'snapshots')):
            raise ValueError('snapshot escapes original collection')
        registered.update({str(workspace_path(snapshot.root / f.path).resolve()):f.content_hash for f in snapshot.files})
    contracts = []
    files = []
    run_details = []
    hypotheses = []
    for candidate_id in selected:
        _check(context)
        runs = groups[candidate_id]
        run = runs[-1]
        candidate = candidates[candidate_id]
        contract = store.load_contract(candidate.contract_id, candidate.contract_version)
        snapshot = store.load_record('snapshots', candidate.snapshot_id)
        if (candidate_hash(candidate) != candidate.manifest_hash or candidate.snapshot_id != primary_id
            or not is_within(snapshot.root, store.root)
            or any(r.candidate_id != candidate_id or r.snapshot_id != candidate.snapshot_id
                or r.manifest_hash != candidate.manifest_hash or r.contract_id != candidate.contract_id
                or r.contract_version != candidate.contract_version for r in runs)):
            raise ValueError('learning binding invalid')
        for source in contract.sources:
            path = safe_child(store.root, source.path)
            if source.path != 'input/issue.md' and registered.get(str(path.resolve())) != source.content_hash:
                raise ValueError('learning expectation is not original')
            data = path.read_bytes()
            if bytes_hash(data) != source.content_hash:
                raise ValueError('learning source hash mismatch')
            lines = _lines(data)
            if not 1 <= source.start_line <= source.end_line <= len(lines):
                raise ValueError('learning source range invalid')
            contracts.append((source, '\n'.join(lines[source.start_line - 1:source.end_line])))
        for file in candidate.files:
            path = safe_child(candidate.storage_root, file.path)
            if not is_within(path, store.root):
                raise ValueError('candidate escapes task')
            data = path.read_bytes()
            if bytes_hash(data) != file.content_hash:
                raise ValueError('candidate source changed')
            failed_names = {phase.get('nodeid','').split('::')[-1].split('[')[0]
                for phase in run.observation.tests if phase.get('outcome') == 'failed'}
            files.append((candidate_id, file, path, data, failed_names))
        verdicts = []
        latest_verdict = latest_verdict_ref = None
        for execution in runs:
            path = store.record_path('verdicts', 'verdict-' + execution.run_id)
            if path.exists():
                verdict = store.load_record('verdicts', 'verdict-' + execution.run_id)
                if (verdict.candidate_id != candidate_id or verdict.manifest_hash != candidate.manifest_hash
                    or verdict.contract_id != candidate.contract_id or verdict.contract_version != candidate.contract_version
                    or execution.run_id not in verdict.run_ids):
                    raise ValueError('verdict binding invalid')
                verdicts.append(verdict)
                if execution.run_id == run.run_id:
                    latest_verdict, latest_verdict_ref = verdict, _ref(store, path)
        classification = verdicts[-1].classification.value if verdicts and verdicts[-1].classification else None
        confirmed = (result.status == TaskState.DONE and result.accepted_candidate_id == candidate_id
            and len(runs) >= 2 and len(verdicts) >= 2
            and all(v.classification == CandidateClass.REPRODUCED for v in verdicts[-2:]))
        attempt = {'candidate_id':candidate_id, 'contract_version':candidate.contract_version,
            'run_ids':[r.run_id for r in runs], 'classification':classification, 'confirmed':bool(confirmed), 'evidence_ids':[]}
        payload['attempts'].append(attempt)
        # Long test lists stay in the original execution record; this is a measured summary.
        phases = run.observation.tests
        failed = [phase for phase in phases if phase.get('outcome') == 'failed']
        summary = {'candidate_id':candidate_id, 'run_id':run.run_id, 'exit_code':run.raw.exit_code,
            'execution_stop':run.raw.stop_reason, 'probe_complete':run.observation.probe_complete,
            'phase_outcomes':dict(Counter(str(p.get('outcome')) for p in phases)),
            'failed_nodes':[p.get('nodeid','') for p in failed[:3]], 'classification':classification,
            'confirmed':bool(confirmed)}
        if review is not None:
            summary['run_ids'] = [r.run_id for r in runs]
        origins = tuple(_ref(store, store.record_path('runs', r.run_id))
                        for r in (runs if review is not None else [run]))
        if latest_verdict is not None and latest_verdict.issues:
            # Keep program/provider diagnostic identities, not arbitrary reason text,
            # traces or measurements. The saved verdict remains the source of truth.
            diagnostics = []
            for issue in latest_verdict.issues[:MAX_VERDICT_DIAGNOSTICS]:
                diagnostic = {'code': issue.code, 'origin': issue.origin}
                if issue.code == 'MODEL_PROTOCOL_ERROR' and 'protocol_error' in issue.measurements:
                    protocol_error = issue.measurements['protocol_error']
                    diagnostic['protocol_error'] = (protocol_error if protocol_error in PROTOCOL_DIAGNOSTICS
                                                     else 'unrecognized')
                diagnostics.append(diagnostic)
            summary['verdict_diagnostics'] = diagnostics
            omitted = len(latest_verdict.issues) - len(diagnostics)
            if omitted:
                summary['verdict_diagnostics_omitted'] = omitted
            origins += (latest_verdict_ref,)
        identity = add('attempt', summary, origins)
        if identity:
            attempt['evidence_ids'].append(identity)
        else:
            # Keep even the attempt identity bounded, without pretending its detail was sent.
            attempt['detail_omitted'] = True
        run_details.append((attempt, run, failed))
        hypotheses.append({'candidate_id':candidate_id, 'statement':candidate.hypothesis,
            'authority':'unverified agent claim'})

    for note in notes[-8:]:
        add('tool_error', note, categories=('model','workflow'))
    for error in tool_errors[-8:]:
        add('tool_error', error, categories=('model','workflow'))
    if review is not None:
        for identity in review.draft.source_ids:
            fact = learning_refs.get(identity)
            if fact is not None and fact['kind'] in ('tool_error', 'protocol_error'):
                atom = {'source_id': identity, **fact}
                if fact['kind'] == 'protocol_error':
                    atom['kind'] = 'exploration.protocol_error'
                else:
                    atom.update(state='error', link_state='matched')
                add('tool_error', atom, categories=('model', 'workflow'))
    for issue_code in exploration.get('capture_issues', ())[:4]:
        omit('exploration', str(issue_code))

    # Attempt outcomes are already present. Preserve concrete test code before
    # secondary crash details can consume the remaining input budget.
    for candidate_id, file, path, data, failed_names in files:
        fragment = _candidate_fragment(data, failed_names)
        if fragment is None:
            omit('candidate', 'NO_COMPLETE_FRAGMENT_FITS', len(data))
            continue
        text, start, end, partial = fragment
        ref = EvidenceRef(relative_name(path,store.root), file.content_hash, start, end)
        identity = add('candidate', {'candidate_id':candidate_id, 'path':file.path, 'content':text,
            'start_line':start, 'end_line':end, 'partial_context':partial}, (ref,), ('model',))
        if identity:
            next(a for a in payload['attempts'] if a['candidate_id']==candidate_id)['evidence_ids'].append(identity)
        if partial:
            omit('candidate_context', 'OTHER_CODE_RETAINED_IN_ORIGINAL_ARTIFACT')
    # Additional failure details are optional: one oversized field cannot erase the episode.
    for attempt, run, failed in run_details:
        origin = (_ref(store, store.record_path('runs', run.run_id)),)
        for phase in failed[:2]:
            detail = {'run_id':run.run_id, 'nodeid':phase.get('nodeid'), 'when':phase.get('when'),
                'exception_type':phase.get('exception_type'), 'crash':phase.get('crash'),
                'assertion_comparison':phase.get('assertion_comparison')}
            identity = add('failure', detail, origin, categories=('framework','model'))
            if identity:
                attempt['evidence_ids'].append(identity)
            if phase.get('longrepr') and len(phase['longrepr'].encode('utf-8')) > ATOM_BYTES:
                omit('longrepr', 'RETAINED_IN_ORIGINAL_ARTIFACT', len(phase['longrepr'].encode('utf-8')))
        details = run.observation.framework_details
        add('framework', {'run_id':run.run_id, 'errors':details.get('errors',()),
            'collection_failures':details.get('collection_failures',()), 'exitstatus':details.get('exitstatus'),
            'source_binding_ok':details.get('source_binding_ok')}, origin, categories=('framework',))

    refs_seen = set()
    source_atoms = list(contracts)
    for ref in exploration.get('source_refs', ())[:16]:
        path = safe_child(store.root, ref.path)
        if registered.get(str(path.resolve())) != ref.content_hash:
            raise ValueError('exploration source is not original')
        data = path.read_bytes()
        if bytes_hash(data) != ref.content_hash:
            raise ValueError('exploration source changed')
        lines = _lines(data)
        if not 1 <= ref.start_line <= ref.end_line <= len(lines):
            raise ValueError('exploration source range invalid')
        # An exact bounded line fragment, explicitly a subset of the visible citation.
        end = min(ref.end_line, ref.start_line + 15)
        fragment_ref = EvidenceRef(ref.path, ref.content_hash, ref.start_line, end)
        source_atoms.append((fragment_ref, '\n'.join(lines[ref.start_line - 1:end])))
    for source, text in source_atoms[:16]:
        key = (source.path, source.content_hash, source.start_line, source.end_line)
        if key in refs_seen:
            continue
        refs_seen.add(key)
        if source.path == 'input/issue.md':
            # The complete frozen Issue is already available once as E1.
            continue
        ref = EvidenceRef(*key)
        add('source', {'path':_redact(source.path,secrets), 'start_line':source.start_line,
            'end_line':source.end_line, 'content':text, 'scope':'cited original fragment'}, (ref,), ('framework','model'))

    for hypothesis in hypotheses:
        add('agent_hypothesis', hypothesis, categories=('model','workflow'))
    if review is not None:
        entries = _review_selection(payload, entries, review, exploration, original_seqs, learning_refs)
        if entries is None:
            return LearningBuildResult(None, 'skipped_draft_evidence_unavailable')
    # References appended to attempts have a cost too.
    while len(canonical_bytes(payload)) > INPUT_BYTES and payload['omissions']:
        payload['omissions'].pop()
    if len(canonical_bytes(payload)) > INPUT_BYTES:
        return LearningBuildResult(None, 'skipped_input_too_large')
    content = b'\n'.join(canonical_bytes(entry) for entry in entries) + b'\n'
    if len(content) > 4 * INPUT_BYTES:
        return LearningBuildResult(None, 'skipped_input_too_large')
    _check(context)
    atomic_write(capsule, content)
    digest = bytes_hash(content)
    evidence_map = {entry['id']:EvidenceRef('learning/evidence.jsonl',digest,index+1,index+1)
        for index,entry in enumerate(entries)}
    source_id = result.task_id + '@' + bytes_hash(str(store.root.resolve()).encode('utf-8'))
    return LearningBuildResult(LearningInput(payload,evidence_map,source_id,events[-1].seq if events else -1), 'ready')
