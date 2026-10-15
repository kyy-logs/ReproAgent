import re
import asyncio
import os
import importlib
from dataclasses import replace

import pytest
from reproagent.core.models import CandidateClass, EvidenceRef, IssueContract, ModelResponse, SourceRef
from reproagent.core.serialization import bytes_hash
from tests.integration.test_runner import setup_runner, execute
from tests.assertion_support import approved_assertions, approved_oracles


class EvidenceModel:
    def __init__(self, classification='REPRODUCED', references=True):
        self.calls = []; self.classification = classification; self.references = references
    async def complete(self, request, context):
        import json
        self.calls.append(request)
        data = json.loads(request.messages[-1]['content'])
        refs = data['available_refs'] if self.references else [{'path':'unknown','content_hash':'bad'}]
        return ModelResponse(json.dumps({'classification':self.classification, 'reason':'Empty input raises the reported IndexError', 'evidence_refs':refs,
            'expected_assertion':True, 'target_triggered':True, 'failure_matches_issue':True,
            'assertion_reviews': approved_assertions(data), **approved_oracles(data)}))


def prepare(tmp_path, projects, facts, text=None, model=None):
    _, request, workspace, snapshot, candidate, runner = setup_runner(tmp_path, projects, facts, text=text)
    source_path = snapshot.root / 'issue.md'
    source = SourceRef(source_path.relative_to(workspace.root).as_posix(), bytes_hash(source_path.read_bytes()))
    contract = IssueContract('contract-1', expected='parse([]) returns []', reported_actual='IndexError', trigger='empty list', sources=(source,))
    execution = execute(request, snapshot, candidate, runner, facts.context())
    model = model or EvidenceModel()
    verifier = importlib.import_module('reproagent.core.verifier').Verifier(workspace.store, runner.adapter, model)
    return contract, candidate, execution, verifier, model, runner, snapshot, request


@pytest.mark.parametrize('change', ['hash','origin','xfail','probe'])
def test_wrong_hash_origin_xfail_or_incomplete_probe_blocks_success_without_model_call(tmp_path, projects, facts, change):
    contract, candidate, execution, verifier, model, *_ = prepare(tmp_path, projects, facts)
    if change == 'hash': execution = replace(execution, manifest_hash='wrong')
    elif change == 'probe': execution = replace(execution, observation=replace(execution.observation, probe_complete=False))
    elif change == 'origin': execution = replace(execution, observation=replace(execution.observation, framework_details={'source_binding_ok':False}))
    else: execution = replace(execution, observation=replace(execution.observation, tests=({'when':'call','outcome':'skipped','wasxfail':'known'},)))
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    assert verdict.classification == (None if change == 'probe' else CandidateClass.INVALID_CANDIDATE)
    assert not model.calls
    if change == 'probe':
        assert verdict.execution_diagnostic.code == 'PROBE_INCOMPLETE'


def test_expected_bug_exception_probe_is_not_final_regression_test(tmp_path, projects, facts):
    text = 'import pytest\nfrom example.parser import parse\ndef test_empty():\n    with pytest.raises(IndexError): parse([])\n'
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, text)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    assert verdict.classification == CandidateClass.NOT_REPRODUCED


@pytest.mark.parametrize('text,expected', [
    ('def test_bad(unknown_fixture): assert False\n', CandidateClass.INVALID_CANDIDATE),
    ('def test_bad(): import dependency_does_not_exist_123\n', CandidateClass.INVALID_CANDIDATE),
    ('from example.parser import parse\ndef test_bad():\n    parse([1])\n    assert False\n', CandidateClass.NOT_REPRODUCED)])
def test_unknown_fixture_unrelated_failure_and_dependency_block_are_distinct(tmp_path, projects, facts, text, expected):
    model = EvidenceModel('NOT_REPRODUCED')
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, text, model)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    assert verdict.classification == expected


def test_semantic_verdict_requires_resolvable_evidence_refs(tmp_path, projects, facts):
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, model=EvidenceModel(references=False))
    assert asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context())).classification is None


def test_replay_compares_target_failure_not_entire_stdout(tmp_path, projects, facts):
    contract, candidate, first, verifier, _, runner, snapshot, request = prepare(tmp_path, projects, facts)
    second = execute(request, snapshot, candidate, runner, facts.context())
    verdicts = tuple(asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context())) for run in (first, second))
    assert verifier.confirm(contract, candidate, first, second, verdicts)
    assert not verifier.confirm(contract, candidate, first, replace(second, execution_role='fixed'), verdicts)
    changed = replace(second, observation=replace(second.observation, tests=tuple(dict(t, crash={'path':'other.py','lineno':1,'message':'ValueError'}) if t['outcome']=='failed' else t for t in second.observation.tests)))
    assert not verifier.confirm(contract, candidate, first, changed, verdicts)


def test_replay_ignores_the_run_beside_the_code_copy_too(tmp_path, projects, facts):
    """A failure naming the run's own workspace must still compare equal.

    The run root is the code copy, but the run also writes beside it: pytest puts its
    basetemp under the run directory, so a tmpdir failure reports
    ``<run>/tmp/pytest-of-.../...``.  Only the run id makes two such messages different,
    and normalizing the code copy alone left them unequal, so the independent replay was
    refused for a failure that was identical -- which is the whole of what it is for.
    """
    contract, candidate, first, verifier, _, runner, snapshot, request = prepare(tmp_path, projects, facts)
    second = execute(request, snapshot, candidate, runner, facts.context())

    def naming_its_own_workspace(run):
        code_root = run.observation.framework_details['run_root']
        reported = os.path.join(os.path.dirname(code_root), 'tmp', 'pytest-of-contoso', 'john_doe')
        tests = tuple(dict(test, crash={'path': 'pathlib.py', 'lineno': 1,
                                        'message': f"FileNotFoundError: [WinError 3]: '{reported}'"})
                      if test['outcome'] == 'failed' else test for test in run.observation.tests)
        return replace(run, observation=replace(run.observation, tests=tests))

    verdicts = tuple(asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context())) for run in (first, second))
    assert verifier.confirm(contract, candidate, naming_its_own_workspace(first),
                            naming_its_own_workspace(second), verdicts)


def test_a_review_that_cannot_fit_reports_how_far_over_budget_it_is(tmp_path, projects, facts):
    """The recovery this design prescribes is to narrow the candidate.

    A refusal that says only "exceeds bounded context" cannot be acted on -- not by the
    model, which is told to narrow without being told by how much, and not by a reader,
    who cannot tell a candidate that missed by a kilobyte from one that missed by a
    hundred.  The magnitude is computed on the spot and then discarded, which is the
    whole of the defect: the observation in ``diag-8365`` shows the same unquantified
    sentence refusing eight program-verified candidates in a row.
    """
    from reproagent.core.models import BudgetLimits
    contract, candidate, execution, verifier, model, *_ = prepare(tmp_path, projects, facts)
    limits = BudgetLimits(tool_response_bytes=1024)

    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context(limits=limits)))

    assert verdict.classification is None and not model.calls
    assert 'narrow candidate' in verdict.reason
    measured = re.search(r'needs (\d+) bytes; budget is (\d+) bytes', verdict.reason)
    assert measured, verdict.reason
    assert int(measured.group(2)) == 1024
    assert int(measured.group(1)) > 1024
    assert verdict.uncertainties == ('evidence omitted rather than silently truncated',)


def test_correction_only_overflow_does_not_mark_evidence_permanently_blocked(tmp_path, projects, facts):
    from reproagent.core.models import BudgetLimits, EvidenceRef
    from reproagent.core.review_context import build_review_context
    from reproagent.core.serialization import canonical_bytes
    from reproagent.store import safe_child
    class InvalidAnswer:
        def __init__(self): self.calls = []
        async def complete(self, request, context):
            self.calls.append(request)
            return ModelResponse('{}')
    model = InvalidAnswer()
    contract, candidate, execution, verifier, *_ = prepare(tmp_path, projects, facts, model=model)
    # A valid, hash-checked text citation cannot be factored as a probe JSON record.
    evidence = verifier.store.root / 'failure.txt'
    evidence.write_text('IndexError: list index out of range\n', encoding='utf-8')
    execution = replace(execution, observation=replace(execution.observation,
        failure_refs=(EvidenceRef('failure.txt', bytes_hash(evidence.read_bytes()), 1, 1),)))
    initial = build_review_context(contract, candidate, execution, read_ref=verifier.resolve,
        read_file=lambda entry: safe_child(candidate.storage_root, entry.path).read_bytes(), max_bytes=1000000)
    cap = len(canonical_bytes(initial.payload)) + 1
    result = asyncio.run(verifier.evaluate(contract, candidate, (execution,),
        facts.context(limits=BudgetLimits(tool_response_bytes=cap))))
    assert len(model.calls) == 1  # Initial evidence fit; only its correction overflowed.
    assert result.classification is None
    assert result.next_action == 'explore'
    assert 'MODEL_PROTOCOL_ERROR' in result.uncertainties


@pytest.mark.parametrize('change,confirmed', [
    ('duration', True), ('count', False), ('target_message', False),
    ('expected_timing', False), ('expected_numeric', False), ('no_pytester', False),
    ('business_timing', False), ('business_summary', False), ('no_session_header', False),
])
def test_replay_ignores_only_pytester_actual_summary_duration(tmp_path, projects, facts, change, confirmed):
    contract, candidate, first, verifier, _, runner, snapshot, request = prepare(tmp_path, projects, facts)
    second = execute(request, snapshot, candidate, runner, facts.context())
    verdicts = tuple(asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context())) for run in (first, second))

    def transcript(run, duration, second_run=False):
        expected = '*required_option=True*'
        if change == 'expected_timing':
            expected = '*1 error in 0.67s*'
        elif change == 'expected_numeric':
            expected = '*0.10*'
        count = '2 errors' if second_run and change == 'count' else '1 error'
        target = 'different target failure' if second_run and change == 'target_message' else 'required option missing'
        summary = f"================== {count} in {duration}s =================="
        message = (f"Failed: nomatch: '{expected}'\n"
                   f"    and: '================== test session starts =================='\n"
                   f"    and: '{target}'\n"
                   f"    and: '================== short test summary info =================='\n"
                   f"    and: '{summary}'\n"
                   f"remains unmatched: '{expected}'")
        if change == 'business_summary':
            message = message.replace("    and: 'required option missing'", f"    and: '================== 1 error in {duration}s =================='")
        if change == 'no_session_header':
            message = message.replace("    and: '================== test session starts =================='\n", '')
        if change == 'business_timing':
            message = f"Failed: business operation finished in {duration}s"
        frames = [] if change == 'no_pytester' else [
            {'module': '_pytest.pytester', 'path': 'src/_pytest/pytester.py', 'lineno': 1908},
            {'module': '_pytest.outcomes', 'path': 'src/_pytest/outcomes.py', 'lineno': 158}]
        tests = tuple(dict(test, exception_type='builtins.Failed', assertion_comparison=None,
                           exception_frames=frames, crash={'path': 'tests/test_repro.py', 'lineno': 20, 'message': message})
                      if test['outcome'] == 'failed' else test for test in run.observation.tests)
        return replace(run, observation=replace(run.observation, tests=tests))

    assert verifier.confirm(contract, candidate, transcript(first, '0.67'),
                            transcript(second, '0.65', True), verdicts) is confirmed
