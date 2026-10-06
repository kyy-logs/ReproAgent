import importlib
import pytest
from reproagent.core.models import CandidateDraft, DraftFile, ProjectView
from tests.integration.test_runner import setup_runner


def build(tmp_path, projects, facts, text=None):
    _, _, workspace, snapshot, candidate, _ = setup_runner(tmp_path, projects, facts, text=text)
    return importlib.import_module('reproagent.core.tools').Tools(ProjectView(snapshot, (candidate,)), workspace, facts.context()), workspace, snapshot


def test_read_cannot_escape_snapshot_or_access_credentials(tmp_path, projects, facts):
    tools, _, _ = build(tmp_path, projects, facts)
    for path in ('../secret', '.env', 'C:/secret', 'tests/../../secret'):
        with pytest.raises((ValueError, FileNotFoundError)): tools.read_file(path, 1, 10)


def test_search_and_read_respect_32768_byte_response_cap(tmp_path, projects, facts):
    tools, _, snapshot = build(tmp_path, projects, facts, text='def test_x():\n    assert False\n' + '#中文' * 20000)
    result = tools.read_file('tests/test_repro.py', 1, 10)
    assert len(result.text.encode()) <= 32768 and result.evidence_refs
    result.text.encode().decode()
    result = tools.search_code('parse', 'snapshot')
    assert result.evidence_refs and 'example/parser.py' in result.text


def test_write_creates_new_candidate_without_overwriting_original(tmp_path, projects, facts):
    tools, workspace, snapshot = build(tmp_path, projects, facts)
    old = (snapshot.root / 'tests/test_existing.py').read_bytes()
    with pytest.raises(ValueError): tools.write_candidate(CandidateDraft((DraftFile('tests/test_existing.py', b'changed'),), snapshot.snapshot_id, 'c', 1, 'bad'))
    result = tools.write_candidate(CandidateDraft((DraftFile('tests/test_new.py', b'def test_new(): assert True\n'),), snapshot.snapshot_id, 'c', 1, 'new'))
    assert result.candidate_id and result.evidence_refs
    assert (snapshot.root / 'tests/test_existing.py').read_bytes() == old


def test_unknown_action_and_extra_execution_arguments_are_rejected():
    validate = importlib.import_module('reproagent.core.tools').validate_action
    for data in ({'name':'shell','parameters':{'command':'echo'}}, {'name':'run_candidate','parameters':{'candidate_id':'c','argv':['x']}}, {'name':'read_file','parameters':{'path':'x','start':True,'end':5}}):
        with pytest.raises(ValueError): validate(data)
    assert validate({'name':'run_candidate','parameters':{'candidate_id':'c'}}).name == 'run_candidate'
