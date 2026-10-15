"""A repaired-environment confirmation is recorded, and the official verdict is not moved."""
import json

import pytest

from evals.swt_bench.io import read_json, seal, verify_seal, write_json


CASE = 'sphinx-doc__sphinx-8627'


def round_with(tmp_path, **outcome):
    """A full diagnostic bound to a round with an unchanged official failure."""
    from tests.unit.test_sphinx_receipt import executed_receipt
    root, path, receipt = executed_receipt(tmp_path)
    data = read_json(root / 'round.json')
    data['outcomes'] = {CASE: {'status':'DONE','preparation':{'status':'ready'},
        'evidence_level':'DIFFERENTIAL_VALIDATED','fix_validation_status':'passed',
        'official_status':'verified','official_resolved':False,'human_judgement':None,
        'duration':10, **outcome}}
    data=seal({key:value for key,value in data.items() if key!='round_hash'},'round_hash')
    write_json(root/'round.json',data)
    receipt['source_round_hash']=data['round_hash']
    write_json(path,receipt)
    return root,data


def diagnostic(tmp_path,source_round_hash,**overrides):
    path=tmp_path/'diagnostic/receipt.json'
    receipt=read_json(path)
    receipt.update(source_round_hash=source_round_hash,**overrides)
    write_json(path,receipt)
    return path


def test_attach_records_the_diagnostic_and_leaves_every_official_figure_alone(tmp_path):
    from evals.repairs.attach import attach
    root, data = round_with(tmp_path)
    updated = attach(root, diagnostic(tmp_path, data['round_hash']))
    record = updated['outcomes'][CASE]['environment_repair']
    assert record['kind'] == 'reproagent-repaired-environment-diagnostic-v1'
    assert record['result'] == 'candidate_differential_confirmed'
    # The whole point of the field: this is not a score, and the record says which official
    # figures it left untouched rather than letting a reader infer it.
    assert record['official_effect'] == 'none'
    assert record['source_round_hash'] == data['round_hash']
    assert record['image_id'] == 'sha256:' + 'e' * 64
    assert set(record['profile_sha256']) == {'tox','constraints.txt'}
    assert all(len(value)==64 for value in record['profile_sha256'].values())
    outcome = updated['outcomes'][CASE]
    assert outcome['official_status'] == 'verified' and outcome['official_resolved'] is False
    assert updated['round_hash'] != data['round_hash']
    verify_seal(read_json(root / 'round.json'), 'round_hash')


def test_attach_reports_the_confirmation_on_its_own_line(tmp_path):
    from evals.repairs.attach import attach
    root, data = round_with(tmp_path)
    attach(root, diagnostic(tmp_path, data['round_hash']))
    summary = read_json(root / 'summary.json')
    assert summary['environment_repair_confirmed'] == 1
    # The environment result is not the candidate's to spend: the official figures are the
    # ones the pinned harness produced, and the repair line is beside them, not among them.
    assert summary['official_successes'] == 0 and summary['official_rate'] == 0.0
    assert summary['official_graded'] == 1 and summary['official_grade_status'] == 'final'
    report = (root / 'report.md').read_text(encoding='utf-8')
    assert '环境修复差分确认：1' in report
    assert '不计入官方判分' in report


@pytest.mark.parametrize('fault', ['round_hash', 'kind', 'result', 'case_id'])
def test_only_a_confirmed_diagnostic_of_this_round_can_be_attached(tmp_path, fault):
    from evals.repairs.attach import attach
    root, data = round_with(tmp_path)
    bound = '0' * 64 if fault == 'round_hash' else data['round_hash']
    overrides = {'kind': {'kind': 'some-other-diagnostic'}, 'result': {'result': 'inconclusive'},
        'case_id': {'case_id': 'pytest-dev__pytest-5227'}}.get(fault, {})
    with pytest.raises(ValueError):
        attach(root, diagnostic(tmp_path, bound, **overrides))
    # A refused attachment leaves the round exactly as it was graded.
    assert read_json(root / 'round.json') == data


def test_a_second_diagnostic_cannot_overwrite_the_first(tmp_path):
    from evals.repairs.attach import attach
    root, data = round_with(tmp_path)
    first = attach(root, diagnostic(tmp_path, data['round_hash']))
    recorded = first['outcomes'][CASE]['environment_repair']
    with pytest.raises(ValueError):
        attach(root, diagnostic(tmp_path, first['round_hash']))
    assert read_json(root / 'round.json')['outcomes'][CASE]['environment_repair'] == recorded


def test_attach_rejects_a_claim_without_frozen_execution_artifacts(tmp_path):
    from evals.repairs.attach import attach
    root,data=round_with(tmp_path)
    path=diagnostic(tmp_path,data['round_hash'],image={},profile={})
    before=read_json(root/'round.json')
    with pytest.raises(ValueError): attach(root,path)
    assert read_json(root/'round.json')==before
