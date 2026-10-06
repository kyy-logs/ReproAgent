"""The reviewer payload must stay bounded and citable without weakening hard checks."""

import asyncio
import json
from dataclasses import asdict, replace

import pytest

from reproagent.core.models import (CandidateClass, CandidateDraft, DraftFile, EvidenceRef, IssueContract,
    ModelResponse, SourceRef)
from reproagent.core.review_context import ReviewContextTooLarge, build_review_context
from reproagent.core.serialization import bytes_hash
from reproagent.core.verifier import Verifier
from reproagent.store import safe_child
from tests.integration.test_runner import execute, setup_runner
from tests.unit.test_verifier import prepare

# A real target failure that also produces a complete assertion comparison.
ASSERTION_CANDIDATE = 'from example.parser import parse\ndef test_empty():\n    assert parse([0]) == []\n'
ISSUE_LINE = 'Empty input must return an empty list; parse([]) raises IndexError.'
# The recorded Sphinx observation was 35079 bytes and 132 module-path mappings were
# 26309 of them. This synthetic mapping reproduces that scale.
ORIGIN_COUNT = 132


class CitingModel:
    """Cites one expectation ref and one failure ref, as REPRODUCED requires."""

    def __init__(self, mutate=None):
        self.calls, self.payloads, self.texts, self.mutate = [], [], [], mutate

    async def complete(self, request, context):
        self.calls.append(request)
        data = json.loads(request.messages[-1]['content'])
        self.payloads.append(data)
        self.texts.append(request.messages[-1]['content'])
        refs = data['available_refs']
        chosen = [next(ref for ref in refs if ref['path'].endswith('issue.md')),
                  next(ref for ref in refs if 'probe' in ref['path'])]
        if self.mutate:
            chosen = self.mutate(data, chosen)
        return ModelResponse(json.dumps({'classification':'REPRODUCED', 'reason':'empty input raises the reported IndexError',
            'evidence_refs':chosen, 'expected_assertion':True, 'target_triggered':True, 'failure_matches_issue':True}))


def candidate_reader(candidate):
    def read(entry):
        data = safe_child(candidate.storage_root, entry.path).read_bytes()
        if bytes_hash(data) != entry.content_hash:
            raise ValueError(f'candidate file hash changed: {entry.path}')
        return data
    return read


def sphinx_scale_origins(run_root):
    return {f'sphinx.ext.autodoc._generated.mock_{index:03d}':
            f'{run_root}/code/sphinx/ext/autodoc/_generated/mock_{index:03d}/handlers.py'
            for index in range(ORIGIN_COUNT)}


def sphinx_scale_calls(origins):
    return {name: index % 5 + 1 for index, name in enumerate(origins)}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def test_132_verified_origins_fit_32768_without_mutating_execution(tmp_path, projects, facts):
    model = CitingModel()
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, ASSERTION_CANDIDATE, model)
    run_root = execution.observation.framework_details['run_root']
    origins = sphinx_scale_origins(run_root)
    calls = sphinx_scale_calls(origins)
    large = replace(execution, observation=replace(execution.observation, target_origins=origins,
        framework_details={**execution.observation.framework_details, 'target_calls':calls}))
    before = asdict(large.observation)
    assert len(json.dumps(origins, ensure_ascii=False)) > 20000

    context = build_review_context(contract, candidate, large, read_ref=verifier.resolve,
        read_file=candidate_reader(candidate), max_bytes=32768)

    assert context.input_bytes > 32768 > context.output_bytes
    assert 'observations.target_origins' in context.omitted_fields
    assert context.payload['observation']['target_origins']['count'] == ORIGIN_COUNT
    assert len(context.payload['observation']['target_origins']['digest']) == 64
    assert context.payload['observation']['target_calls']['total'] == sum(calls.values())
    assert [segment['text'] for segment in context.payload['expectation_sources']] == [ISSUE_LINE]
    assert [segment['content'] for segment in context.payload['candidate']] == [ASSERTION_CANDIDATE]

    verdict = asyncio.run(verifier.evaluate(contract, candidate, (large,), facts.context()))

    assert verdict.classification == CandidateClass.REPRODUCED
    sent = model.payloads[-1]
    assert sent == context.payload and len(model.texts[-1].encode()) <= 32768
    assert sent['observation']['target_origins']['count'] == ORIGIN_COUNT
    assert sent['observation']['target_calls']['total'] == sum(calls.values())
    # Neither the plain nor the JSON-escaped spelling of the run root is exported.
    assert not any(run_root in text or run_root.replace('\\', '\\\\') in text for text in strings(sent))
    assert '<run>' in sent['failure_evidence'][0]['text']
    # Core evidence survives the projection: the cited source lines, the complete
    # candidate test and the complete assertion comparison of the real failure.
    assert [segment['text'] for segment in sent['expectation_sources']] == [ISSUE_LINE]
    assert [segment['content'] for segment in sent['candidate']] == [ASSERTION_CANDIDATE]
    comparison = next(phase['assertion_comparison'] for phase in sent['observation']['tests'] if phase.get('assertion_comparison'))
    assert (comparison['left'], comparison['right']) == ('[0]', '[]')
    assert sent['failure_evidence'][0]['text']
    assert [ref['path'] for ref in sent['available_refs']] == [segment['path'] for segment in sent['expectation_sources']] + \
        [segment['path'] for segment in sent['failure_evidence']]
    # The projection never rewrites the execution record it was built from.
    assert asdict(large.observation) == before
    assert large.observation.target_origins == origins
    assert large.observation.framework_details['target_calls'] == calls


@pytest.mark.parametrize('change', ['origin', 'failure'])
def test_wrong_origin_or_changed_failure_cannot_be_hidden_by_projection(tmp_path, projects, facts, change):
    model = CitingModel()
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, ASSERTION_CANDIDATE, model)
    if change == 'origin':
        # Exactly what the runner records when a loaded module resolves outside the
        # protected run copy: no projection may compress that away.
        execution = replace(execution, observation=replace(execution.observation,
            target_origins={**execution.observation.target_origins, 'example.parser':str(tmp_path / 'old/example/parser.py')},
            framework_details={**execution.observation.framework_details, 'source_binding_ok':False}))
        expected = CandidateClass.INVALID_CANDIDATE
    else:
        for ref in execution.observation.failure_refs:
            path = verifier.store.root / ref.path
            path.write_bytes(path.read_bytes() + b'#')
        expected = None

    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))

    assert verdict.classification == expected and verdict.classification != CandidateClass.REPRODUCED
    assert not model.calls


def test_citation_outside_sent_line_range_is_rejected(tmp_path, projects, facts):
    def mutate(data, chosen):
        failure = chosen[-1]
        assert failure['start_line'] > 1 and failure['path'].endswith('probe.jsonl')
        assert (failure['path'], 1, 1) not in {(ref['path'], ref['start_line'], ref['end_line']) for ref in data['available_refs']}
        return [chosen[0], {**failure, 'start_line':1, 'end_line':1}]

    model = CitingModel(mutate)
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, ASSERTION_CANDIDATE, model)
    failure = execution.observation.failure_refs[0]
    # The cited range exists in the stored probe, so only "was it sent" can reject it.
    assert verifier.resolve(replace(failure, start_line=1, end_line=1))

    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))

    assert verdict.classification is None
    assert len(model.calls) == 3
    assert all(not (ref['path'].endswith('probe.jsonl') and ref['start_line'] == 1)
               for payload in model.payloads for ref in payload['available_refs'])


def test_core_evidence_too_large_stays_uncertain(tmp_path, projects, facts):
    model = CitingModel()
    padding = '# ' + 'padding ' * 6000 + '\n'
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts,
        'from example.parser import parse\n' + padding + 'def test_empty():\n    assert parse([]) == []\n', model)

    with pytest.raises(ReviewContextTooLarge):
        build_review_context(contract, candidate, execution, read_ref=verifier.resolve,
            read_file=candidate_reader(candidate), max_bytes=32768)

    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))

    assert verdict.classification is None
    assert not model.calls
    assert verdict.uncertainties == ('evidence omitted rather than silently truncated',)


def test_out_of_range_expectation_source_is_rejected_before_any_model_call(tmp_path, projects, facts):
    model = CitingModel()
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, ASSERTION_CANDIDATE, model)
    contract = replace(contract, sources=(replace(contract.sources[0], end_line=99999),))

    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))

    assert verdict.classification is None and not model.calls


def test_real_collection_error_aborts_before_any_review(tmp_path, projects, facts):
    """A candidate file that cannot be imported leaves an incomplete probe.

    Its collection failure does carry the absolute run path, so this records both that
    the runner stops such a run before the projection and that the raw text is what the
    projection must normalize.
    """
    _, request, workspace, snapshot, _, runner = setup_runner(tmp_path, projects, facts)
    candidate = workspace.publish(CandidateDraft((
        DraftFile('tests/test_repro.py', ASSERTION_CANDIDATE.encode()),
        DraftFile('tests/test_broken.py', b'import dependency_does_not_exist_123\n')), snapshot.snapshot_id, 'contract-1', 1, 'empty input'), snapshot)
    execution = execute(request, snapshot, candidate, runner, facts.context())
    run_root = execution.observation.framework_details['run_root']
    failures = execution.observation.framework_details['collection_failures']
    assert failures and run_root.replace('\\', '\\\\') in json.dumps(failures)

    source_path = snapshot.root / 'issue.md'
    contract = IssueContract('contract-1', expected='parse([]) returns []', reported_actual='ModuleNotFoundError on import',
        trigger='empty list', sources=(SourceRef(source_path.relative_to(workspace.root).as_posix(), bytes_hash(source_path.read_bytes())),))
    model = CitingModel()
    verdict = asyncio.run(Verifier(workspace.store, runner.adapter, model).evaluate(contract, candidate, (execution,), facts.context()))

    assert not execution.observation.probe_complete and verdict.classification == CandidateClass.INVALID_CANDIDATE
    assert not model.calls


def test_collection_and_nodeid_paths_are_relative_to_the_run_root(tmp_path, projects, facts):
    """Raw collection text, nodeid lists and preconditions carry no absolute run path."""
    model = CitingModel()
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, ASSERTION_CANDIDATE, model)
    run_root = execution.observation.framework_details['run_root']
    # Verbatim shape of the real pytest collection error for an unimportable test file
    # (measured with this interpreter): it embeds the absolute module path.
    longrepr = (f"ImportError while importing test module '{run_root}\\tests\\test_broken.py'.\n"
        "Hint: make sure your test modules/packages have valid Python names.\nTraceback:\n"
        "tests\\test_broken.py:1: in <module>\n    import dependency_does_not_exist_123\n"
        "E   ModuleNotFoundError: No module named 'dependency_does_not_exist_123'")
    crowded = replace(execution, observation=replace(execution.observation, framework_details={
        **execution.observation.framework_details,
        'errors': [f'probe {run_root}\\probe.jsonl incomplete'],
        'collection_failures': [{'nodeid': f'{run_root}\\tests\\test_broken.py', 'outcome': 'failed', 'longrepr': longrepr}],
        'collected_nodeids': [f'{run_root}\\tests\\test_repro.py::test_empty'],
        'completed_nodeids': [f'{run_root}/tests/test_repro.py::test_empty'],
        'deselected_nodeids': [f'{run_root}\\tests\\test_other.py::test_other'],
        'preconditions': [f'reset state under {run_root}\\tmp']}))

    context = build_review_context(contract, candidate, crowded, read_ref=verifier.resolve,
        read_file=candidate_reader(candidate), max_bytes=1 << 20)

    view = context.payload['observation']
    assert not any(run_root in text or run_root.replace('\\', '\\\\') in text for text in strings(context.payload))
    assert '<run>' in view['errors'][0] and '<run>' in view['preconditions'][0]
    assert view['collected_nodeids'] == ['<run>/tests/test_repro.py::test_empty']
    assert view['completed_nodeids'] == ['<run>/tests/test_repro.py::test_empty']
    assert view['deselected_nodeids'] == ['<run>/tests/test_other.py::test_other']
    failure = view['collection_failures'][0]
    assert failure['nodeid'] == '<run>/tests/test_broken.py' and failure['outcome'] == 'failed'
    assert "'<run>\\tests\\test_broken.py'" in failure['longrepr']
    assert "No module named 'dependency_does_not_exist_123'" in failure['longrepr']


def test_sent_references_are_exactly_the_citable_ones(tmp_path, projects, facts):
    """Every reference the reviewer may cite is one whose lines were actually sent."""
    model = CitingModel()
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, ASSERTION_CANDIDATE, model)
    asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    sent = model.payloads[-1]
    assert [EvidenceRef(**ref) for ref in sent['available_refs']] == \
        [EvidenceRef(segment['path'], segment['content_hash'], segment['start_line'], segment['end_line'])
         for segment in sent['expectation_sources'] + sent['failure_evidence']]
