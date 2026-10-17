"""First failures do not validate later output-format constraints."""
import asyncio
import json
from dataclasses import replace

import pytest

from reproagent.core.models import CandidateClass
from reproagent.core.oracle_audit import oracle_checks
from reproagent.core.review_context import build_review_context
from reproagent.store import safe_child
from tests.unit.test_verifier import EvidenceModel, prepare


@pytest.mark.parametrize('expression,operator,literal', [
    ('"orphan:table-1" in output', 'in', 'orphan:table-1'),
    ('output == "Table 1"', '==', 'Table 1'),
    ('"missing table" not in output', 'not in', 'missing table'),
    ('output != "legacy"', '!=', 'legacy'),
    ('("prefix" + "-suffix") in output', 'in', 'prefix-suffix'),
    ('expected in output', 'in', 'orphan:table-1'),
])
def test_literal_output_constraints_require_their_own_review(expression, operator, literal):
    content = ('def test_output(output):\n    expected = "orphan:table-1"\n'
               '    assert ' + expression + '\n')
    rows = oracle_checks([{'path':'tests/test_output.py', 'role':'test', 'content':content}])
    precision = [row for row in rows if row['kind'] == 'literal_comparison']
    assert len(precision) == 1
    assert precision[0]['assertion_id'] == 'A1'
    assert precision[0]['details']['operator'] == operator
    assert precision[0]['details']['text'] == literal


def test_output_channel_filename_is_not_mistaken_for_an_expected_literal():
    rows = oracle_checks([{'path':'tests/test_output.py', 'role':'test', 'content':
        'def test_output(root):\n    output = (root / "output.tex").read_text()\n'
        '    assert "reference" in output\n'}])
    precision = [row for row in rows if row['kind'] == 'literal_comparison']
    assert [row['details']['text'] for row in precision] == ['reference']


def test_long_literal_comparison_is_bounded_and_explicitly_incomplete():
    rows = oracle_checks([{'path':'tests/test_output.py', 'role':'test', 'content':
        'def test_output(output):\n    assert ' + repr('x' * 2000) + ' in output\n'}])
    assert any(row['kind'] == 'literal_comparison' for row in rows)
    assert any(row['kind'] == 'analysis_unknown' for row in rows)
    assert all(len(value) <= 240 for row in rows for value in row['details'].values() if isinstance(value, str))


TEXT = ('from example.parser import parse\n'
        'def test_empty():\n'
        '    assert parse([]) == []\n'
        '    assert "orphan:table-1" in "rendered"\n')


def review(verifier, contract, candidate, run):
    return build_review_context(contract, candidate, run, read_ref=verifier.resolve,
        read_file=lambda entry:safe_child(candidate.storage_root, entry.path).read_bytes(), max_bytes=65536)


def test_review_distinguishes_failure_frames_from_unestablished_assertions(tmp_path, projects, facts):
    contract, candidate, run, verifier, *_ = prepare(tmp_path, projects, facts, text=TEXT)
    payload = review(verifier, contract, candidate, run).payload
    sites = payload['assertion_sites']
    assert sites[0]['execution_observation']['state'] == 'failure_location_overlap'
    assert sites[0]['execution_observation']['phase_indexes']
    assert sites[1]['execution_observation'] == {'state':'not_established', 'phase_indexes':[]}
    assert any(row['assertion_id'] == 'A2' and row['kind'] == 'literal_comparison'
               for row in payload['oracle_checks'])


def test_missing_frames_do_not_invent_assertion_execution(tmp_path, projects, facts):
    contract, candidate, run, verifier, *_ = prepare(tmp_path, projects, facts, text=TEXT)
    tests = tuple(dict(phase, exception_frames=[], crash={}) for phase in run.observation.tests)
    changed = replace(run, observation=replace(run.observation, tests=tests))
    sites = review(verifier, contract, candidate, changed).payload['assertion_sites']
    assert all(site['execution_observation']['state'] == 'not_established' for site in sites)


class PrecisionReviewer(EvidenceModel):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode

    async def complete(self, request, context):
        response = await super().complete(request, context)
        payload = json.loads(request.messages[-1]['content'])
        answer = json.loads(response.text)
        precision_ids = {row['id'] for row in payload.get('oracle_checks', [])
                         if row['kind'] == 'literal_comparison'}
        if self.mode == 'omit' and 'oracle_reviews' in answer:
            answer['oracle_reviews'] = [row for row in answer.get('oracle_reviews', [])
                                        if row['check_id'] not in precision_ids]
        else:
            for row in answer.get('oracle_reviews', []):
                if row['check_id'] in precision_ids:
                    row.update(supported=False, basis='unknown', evidence_refs=[], source_quote='',
                               reason='The exact representation is incidental and not required by the report.')
        return replace(response, text=json.dumps(answer))


@pytest.mark.parametrize('mode,classification,attempts', [
    ('omit', None, 3), ('reject', CandidateClass.INVALID_CANDIDATE, 1),
])
def test_global_approval_cannot_bypass_literal_constraint_review(tmp_path, projects, facts, mode, classification, attempts):
    model = PrecisionReviewer(mode)
    contract, candidate, run, verifier, *_ = prepare(tmp_path, projects, facts, text=TEXT, model=model)
    result = asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context()))
    assert result.classification == classification
    assert len(model.calls) == attempts
    assert result.issues[0].code == ('MODEL_PROTOCOL_ERROR' if mode == 'omit' else 'CANDIDATE_ORACLES_UNCONFIRMED')


def test_literal_constraints_are_review_questions_not_automatic_rejections(tmp_path, projects, facts):
    # The scripted reviewer supplies explicit source quotes for every generated
    # check. This verifies the protocol route, not real semantic correctness.
    supported = 'from example.parser import parse\ndef test_empty(): assert str(parse([])) == "[]"\n'
    contract, candidate, run, verifier, *_ = prepare(tmp_path, projects, facts, text=supported)
    assert asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context())).classification == CandidateClass.REPRODUCED



def test_same_line_assertions_do_not_claim_which_one_was_entered():
    from reproagent.core.assertion_audit import assertion_sites, with_execution_observations
    sites = assertion_sites([{'path':'tests/test_same.py', 'role':'test', 'content':
        'def test_case(): assert False; assert "later" == "unreached"\n'}])
    result = with_execution_observations(sites, [{'outcome':'failed',
        'exception_frames':[{'path':'<run>/tests/test_same.py', 'lineno':1}]}])
    assert all(site['execution_observation']['state'] == 'not_established' for site in result)


@pytest.mark.parametrize('path,line', [('other/test_output.py',3), ('tests/test_output.py',True)])
def test_failure_location_must_match_candidate_path_and_real_line(path, line):
    from reproagent.core.assertion_audit import assertion_sites, with_execution_observations
    sites = assertion_sites([{'path':'tests/test_output.py', 'role':'test', 'content':
        'def test_case():\n    value = 1\n    assert value == 2\n'}])
    result = with_execution_observations(sites, [{'outcome':'failed',
        'exception_frames':[{'path':path, 'lineno':line}]}])
    assert result[0]['execution_observation']['state'] == 'not_established'


def test_literal_expansion_is_bounded_before_building_review_questions():
    import ast
    from reproagent.core.oracle_audit import _Audit
    content = 'seed = ' + repr('x' * 512) + '\n'
    previous = 'seed'
    for index in range(10):
        name = 'part' + str(index)
        content += name + ' = ' + previous + ' + ' + previous + '\n'
        previous = name
    content += 'def test_output(output):\n    assert ' + previous + ' in output\n'
    tree = ast.parse(content)
    compare = next(node for node in ast.walk(tree) if isinstance(node, ast.Compare))
    audit = _Audit({'path':'tests/test_output.py', 'role':'test', 'content':content}, tree)
    values = audit.literal_strings(compare.left)
    assert values and all(len(value) <= 241 for value in values)


def test_literal_resolution_depth_limit_is_exposed_as_unknown():
    content = 'part0 = "seed"\n'
    for index in range(1, 46):
        content += 'part' + str(index) + ' = part' + str(index - 1) + '\n'
    content += 'def test_output(output):\n    assert part45 in output\n'
    rows = oracle_checks([{'path':'tests/test_output.py', 'role':'test', 'content':content}])
    assert any(row['kind'] == 'analysis_unknown' for row in rows)


def test_a_failure_before_an_assert_on_the_same_line_is_only_a_location_overlap():
    from reproagent.core.assertion_audit import assertion_sites, with_execution_observations
    sites = assertion_sites([{'path':'tests/test_same.py', 'role':'test', 'content':
        'def test_case(): raise RuntimeError(); assert True\n'}])
    result = with_execution_observations(sites, [{'outcome':'failed',
        'exception_frames':[{'path':'<run>/tests/test_same.py', 'lineno':1}]}])
    # The frame does not say the assertion operand/comparison was entered.
    assert result[0]['execution_observation']['state'] == 'failure_location_overlap'