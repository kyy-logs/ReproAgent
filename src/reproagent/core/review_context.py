"""Bounded, citable projection of a fully checked execution result.

The verifier runs every hard check against the complete ExecutionResult first; only
then does this module build what the reviewer sees. Cited line segments are read from
the real files, candidate tests and actual failures are sent whole, and per-module
absolute path mappings are replaced by validated count/hash summaries so a large
observation still fits the configured context budget. Core evidence that cannot fit is
never truncated: the caller gets ReviewContextTooLarge and must stay uncertain.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .models import Candidate, EvidenceRef, ExecutionResult, FileEntry, IssueContract
from .protocol import verdict_schema
from .serialization import bytes_hash, canonical_bytes

# Stable marker for the run root: necessary paths stay readable without exporting the
# machine-specific absolute prefix that made the recorded observation overflow.
RUN_ROOT = '<run>'
# Only these two observations reach the reviewer as a count/digest/package summary rather
# than in full. Everything else the projection names is either sent whole (expectation_sources)
# or is not a field of the payload at all, so it is not listed here.
SUMMARIZED_FIELDS = ('observations.target_origins', 'observations.target_calls')


class ReviewContextTooLarge(Exception):
    """The core evidence does not fit the reviewer budget and must not be truncated."""


@dataclass(frozen=True, slots=True)
class ReviewContext:
    payload: dict[str, Any]
    available_refs: tuple[EvidenceRef, ...]
    input_bytes: int
    output_bytes: int
    omitted_fields: tuple[str, ...]


def _root_spellings(run_root):
    """Every spelling of the run root a recorded path may use, longest first.

    Windows records carry the native backslash form; a serialized probe line escapes those
    separators, and a pytest longrepr, error or nodeid may already be forward-slashed.
    Replacing the longest spelling first keeps the shorter ones from splitting it.
    """
    return sorted({run_root, run_root.replace('\\', '/'), run_root.replace('\\', '\\\\')},
                  key=len, reverse=True)


def _path_text(value, run_root):
    """A path with the run root replaced by a stable marker and separators normalized."""
    text = str(value if value is not None else '')
    if run_root:
        for spelling in _root_spellings(run_root):
            text = text.replace(spelling, RUN_ROOT)
    return text.replace('\\', '/')


def _message_text(value, run_root):
    """Free text (or a JSON-encoded record) with absolute run paths replaced.

    Every spelling of the run root is replaced: the native form, the escaped form a
    serialized probe line uses, and the forward-slash form a recorded report may carry.
    Other backslashes are left untouched.
    """
    text = str(value if value is not None else '')
    if run_root:
        for spelling in _root_spellings(run_root):
            text = text.replace(spelling, RUN_ROOT)
    return text


def _unique(refs):
    result = []
    for ref in refs:
        if ref not in result:
            result.append(ref)
    return result


def _digest(items):
    return hashlib.sha256('\n'.join(f'{key}\t{value}' for key, value in items).encode('utf-8')).hexdigest()


def _packages(items, counts):
    totals: dict[str, int] = {}
    for key, value in items:
        package = key.split('.', 1)[0]
        totals[package] = totals.get(package, 0) + (value if counts else 1)
    return dict(sorted(totals.items()))


def _mapping_summary(mapping, run_root, *, calls=False, observed_modules=()):
    items = sorted((str(key), value) for key, value in mapping.items())
    resolved = [(key, _path_text(value, run_root)) if isinstance(value, str) else (key, int(value)) for key, value in items]
    summary = {'count': len(resolved), 'digest': _digest(resolved),
               'package_calls' if calls else 'package_modules': _packages(resolved, calls)}
    if calls:
        summary['total'] = sum(value for _, value in resolved)
        observed = dict((key, value) for key, value in resolved if key in set(observed_modules))
        if observed:
            summary['failure_modules'] = observed
    return summary


def _segment(text, ref, run_root):
    lines = text.splitlines()
    if type(ref.start_line) is not int or type(ref.end_line) is not int or not 1 <= ref.start_line <= ref.end_line <= max(1, len(lines)):
        raise ValueError('evidence line range invalid')
    return {'path': ref.path, 'content_hash': ref.content_hash, 'start_line': ref.start_line, 'end_line': ref.end_line,
            'text': _message_text('\n'.join(lines[ref.start_line - 1:ref.end_line]), run_root)}


def _phase(phase, run_root):
    projected = {'nodeid': str(phase.get('nodeid', '')), 'when': phase.get('when'), 'outcome': phase.get('outcome')}
    if phase.get('wasxfail'):
        projected['wasxfail'] = str(phase['wasxfail'])
    if phase.get('exception_type'):
        projected['exception_type'] = str(phase['exception_type'])
    frames = [{'path': _path_text(frame.get('path', ''), run_root), 'lineno': frame.get('lineno'),
               'module': str(frame.get('module', ''))} for frame in phase.get('exception_frames', ())]
    if frames:
        projected['exception_frames'] = frames
    crash = phase.get('crash') or {}
    if crash:
        projected['crash'] = {'path': _path_text(crash.get('path', ''), run_root), 'lineno': crash.get('lineno'),
                              'message': _message_text(crash.get('message', ''), run_root)}
    if phase.get('assertion_comparison'):
        # Complete observed comparison values, not pytest's abbreviated display.
        projected['assertion_comparison'] = phase['assertion_comparison']
    if phase.get('longrepr'):
        projected['longrepr'] = _message_text(phase['longrepr'], run_root)
    return projected


def _collection_failure(item, run_root):
    """One collection failure with its nodeid and report text path-normalized."""
    projected = {}
    for key, value in item.items():
        if not isinstance(value, str):
            projected[key] = value
        elif key == 'nodeid':
            projected[key] = _path_text(value, run_root)
        else:
            projected[key] = _message_text(value, run_root)
    return projected


def _contract_view(contract):
    return {'contract_id': contract.contract_id, 'version': contract.version, 'trigger': contract.trigger,
            'expected': contract.expected, 'reported_actual': contract.reported_actual,
            'observable_checks': list(contract.observable_checks), 'assumptions': list(contract.assumptions),
            'missing_information': list(contract.missing_information), 'revision_reason': contract.revision_reason}


def _observation_view(run, run_root, phases):
    details = run.observation.framework_details
    failed_modules = {frame['module'] for phase in phases if phase.get('outcome') == 'failed'
                      for frame in phase.get('exception_frames', ()) if frame.get('module')}
    return {
        'tests': phases,
        'target_origins': _mapping_summary(run.observation.target_origins, run_root),
        'target_calls': _mapping_summary(details.get('target_calls', {}), run_root, calls=True, observed_modules=failed_modules),
        'errors': [_message_text(error, run_root) for error in details.get('errors', ())],
        'collection_failures': [_collection_failure(item, run_root) for item in details.get('collection_failures', ())],
        'collected_nodeids': [_path_text(node, run_root) for node in details.get('collected_nodeids', ())],
        'completed_nodeids': [_path_text(node, run_root) for node in details.get('completed_nodeids', ())],
        'deselected_nodeids': [_path_text(node, run_root) for node in details.get('deselected_nodeids', ())],
        'exitstatus': details.get('exitstatus'),
        'exitstatus_matches_process': details.get('exitstatus_matches_process'),
        'source_binding_ok': details.get('source_binding_ok'),
        'preconditions': [_message_text(precondition, run_root) for precondition in details.get('preconditions', ())],
        'collected': run.observation.collected,
        'executed': run.observation.executed,
        'probe_complete': run.observation.probe_complete,
    }


def build_review_context(contract: IssueContract, candidate: Candidate, run: ExecutionResult, *,
        read_ref: Callable[[EvidenceRef], str], read_file: Callable[[FileEntry], bytes],
        max_bytes: int, protocol_error: str = '') -> ReviewContext:
    """Project one fully checked execution into the reviewer's bounded payload.

    read_ref validates the whole-file hash of a cited source; read_file validates a
    candidate file hash. Neither may return content for altered evidence.
    """
    run_root = str(run.observation.framework_details.get('run_root') or '')
    sources = _unique(EvidenceRef(s.path, s.content_hash, s.start_line, s.end_line) for s in contract.sources)
    failures = _unique(run.observation.failure_refs)
    available = _unique([*sources, *failures])
    texts: dict[str, str] = {}
    for ref in available:
        text = texts.get(ref.path)
        if text is None:
            text = texts[ref.path] = read_ref(ref)
        elif bytes_hash(text.encode('utf-8')) != ref.content_hash:
            # Every cited reference is checked, even when another reference already
            # read the same file.
            raise ValueError('evidence hash mismatch')
    candidate_files = []
    for entry in candidate.files:
        data = read_file(entry)
        candidate_files.append({'path': entry.path, 'role': entry.role, 'content_hash': entry.content_hash,
                                'content': data.decode('utf-8')})
    input_bytes = len(canonical_bytes({'contract': asdict(contract), 'source_text': [texts[ref.path] for ref in sources],
        'candidate': [{'path': entry['path'], 'content': entry['content']} for entry in candidate_files],
        'observations': asdict(run.observation), 'available_refs': [asdict(ref) for ref in available],
        'response_schema': verdict_schema()}))
    phases = [_phase(phase, run_root) for phase in run.observation.tests]
    payload = {'contract': _contract_view(contract),
        'expectation_sources': [_segment(texts[ref.path], ref, run_root) for ref in sources],
        'candidate': candidate_files,
        'failure_evidence': [_segment(texts[ref.path], ref, run_root) for ref in failures],
        'observation': _observation_view(run, run_root, phases),
        'available_refs': [asdict(ref) for ref in available],
        'projection': {'paths': f'absolute run paths are shown as {RUN_ROOT}', 'summarized_fields': list(SUMMARIZED_FIELDS)},
        'response_schema': verdict_schema()}
    if protocol_error:
        payload['protocol_error'] = protocol_error
    output_bytes = len(canonical_bytes(payload))
    if output_bytes > max_bytes:
        raise ReviewContextTooLarge(f'review evidence needs {output_bytes} bytes; budget is {max_bytes}')
    return ReviewContext(payload, tuple(available), input_bytes, output_bytes, SUMMARIZED_FIELDS)
