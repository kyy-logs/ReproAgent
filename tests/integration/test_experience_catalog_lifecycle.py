import json

from reproagent.core.models import TaskState
from reproagent.experience import load_experience_snapshot
from tests.integration.test_experience_lifecycle import run_sdk_task


def test_directory_browse_load_publish_and_independent_learning(tmp_path, projects, facts):
    request, controller, result, seen, _, learning = run_sdk_task(tmp_path, projects, facts, directory=True)
    assert result.status == TaskState.DONE
    assert controller.learning_result.code == 'written'
    assert len(learning) == 1
    assert controller.experience_service.view.read_ids
    old = controller.experience_service.snapshot
    current = load_experience_snapshot(request.experience_file)
    assert len(old.cards) == 1 and len(current.cards) == 2
    added = next(c for c in current.cards if c.id not in {c.id for c in old.cards})
    assert added.name and added.description and added.evidence_refs
    assert controller.experience_service.view.read(added.id)['code'] in ('unknown_id', 'read_limit')
    index = json.loads((request.experience_file / 'model/index.json').read_text())
    assert any(c['id'] == added.id and set(c) == {'id', 'name', 'description'} for c in index['items'])
    exploration = [p for p in seen if p.get('tools')]
    assert 'HISTORICAL_HINT' not in json.dumps(exploration[0]['messages'])
    assert 'HISTORICAL_HINT' not in json.dumps(exploration[1]['messages'])
    assert 'HISTORICAL_HINT' in json.dumps(exploration[2]['messages'])
    assert all('evidence_refs' not in str(p.get('items', [])) for p in index['items'])
