import json

import pytest

from reproagent.experience_drafts import ExperienceDraftSession
from reproagent.store import TaskStore
from tests.unit.test_experience_drafts import raw_draft


def test_registered_failure_has_a_persistent_source_and_verified_receipt(tmp_path):
    store = TaskStore(tmp_path / 'task')
    session = ExperienceDraftSession(store)
    identity = session.register_source({'kind': 'tool_error', 'tool': 'Read', 'tool_call_id': 'sdk-call',
        'arguments': {'file_path': 'src/_pytest/logging.py'},
        'result': 'Error: file_path must be an absolute path, got: src/_pytest/logging.py'})
    assert identity and identity.startswith('L')
    assert session.offer(raw_draft(source_ids=[identity]), call_id='publish', arguments_hash='hash') == 'received'
    saved = ExperienceDraftSession(store)
    assert saved.latest.draft.source_ids == (identity,)
    events, errors = store.read_events()
    assert not errors
    receipt = next(e for e in events if e.kind == 'experience.draft')
    assert receipt.payload['format_state'] == 'valid'
    assert receipt.payload['source_state'] == 'verified'


@pytest.mark.parametrize('source_id', ['src/_pytest/logging.py', '4aff12' + 'a' * 58, 'L999999', 'sdk-call'])
def test_unknown_source_is_not_received_or_saved(tmp_path, source_id):
    store = TaskStore(tmp_path / 'task')
    session = ExperienceDraftSession(store)
    code = session.offer(raw_draft(source_ids=[source_id]), call_id='publish', arguments_hash='hash')
    assert code == 'invalid_sources'
    assert session.latest is None
    assert not (store.root / 'learning/draft.json').exists()
    events, errors = store.read_events()
    assert not errors
    assert events[-1].payload['source_state'] == 'unknown_source'
    assert events[-1].payload['format_state'] == 'valid'


def test_unknown_source_does_not_replace_a_previously_verified_draft(tmp_path):
    store = TaskStore(tmp_path / 'task')
    session = ExperienceDraftSession(store)
    identity = session.register_source({'kind': 'protocol_error', 'code': 'MULTIPLE_TOOL_CALLS'})
    assert session.offer(raw_draft(source_ids=[identity]), call_id='first', arguments_hash='hash') == 'received'
    previous = (store.root / 'learning/draft.json').read_bytes()
    assert session.offer(raw_draft(source_ids=['file.py']), call_id='second', arguments_hash='hash') == 'invalid_sources'
    assert session.latest.call_id == 'first'
    assert (store.root / 'learning/draft.json').read_bytes() == previous


def test_real_recorded_path_and_hash_drafts_are_rejected_before_learning(tmp_path):
    # Literal sources from the independent real runs, without depending on local artifacts.
    for name, source in [('path', 'src/_pytest/logging.py'),
                         ('hash', '4aff12aea1695dc90ee08f84c9e5f06c2ffc2ead33d32c86360dd126476638d8')]:
        session = ExperienceDraftSession(TaskStore(tmp_path / name))
        assert session.offer(raw_draft(source_ids=[source]), call_id='publish', arguments_hash='hash') == 'invalid_sources'


def test_source_registration_redacts_and_never_promotes_unknown_fact_kinds(tmp_path):
    session = ExperienceDraftSession(TaskStore(tmp_path / 'task'), secrets=('FAKE_SECRET',))
    identity = session.register_source({'kind': 'tool_error', 'tool': 'Read', 'tool_call_id': 'sdk-call',
        'arguments': {'file_path': 'x.py'}, 'result': 'missing FAKE_SECRET'})
    assert identity
    events = (session.store.root / 'events.jsonl').read_text(encoding='utf-8')
    assert 'FAKE_SECRET' not in events and '[REDACTED]' in events
    assert session.register_source({'kind': 'assistant_claim', 'result': 'it works'}) is None
    assert session.register_source({'kind': 'tool_error', 'tool': 'read_experience', 'result': 'old advice'}) is None


def test_original_execution_source_is_visible_and_binds_to_an_attempt(tmp_path, projects, facts):
    from reproagent.learning_material import prepare_review_input
    from tests.unit.test_learning_review_material import run_task, exploration
    result, store, _, context = run_task(tmp_path, projects, facts)
    session = ExperienceDraftSession(store)
    notices = session.sources.original_run_notices()
    assert notices and all(n['kind'] == 'original_run' for n in notices)
    before = len(store.read_events()[0])
    assert session.sources.original_run_notices() == notices  # no repeated registration
    assert len(store.read_events()[0]) == before
    identity = notices[-1]['learning_source_id']
    assert session.offer(raw_draft(source_ids=[identity]), call_id='draft-call', arguments_hash='arguments-hash') == 'received'
    built = prepare_review_input(store, result, context, draft=session.latest, exploration=exploration(offset=1))
    assert built.code == 'ready'
    entry = next(e for e in built.material.payload['evidence'] if e['id'] == built.material.payload['draft_evidence_ids'][0])
    assert entry['kind'] == 'attempt' and notices[-1]['run_id'] in entry['text']


def test_protocol_error_can_support_learning_without_an_original_execution(tmp_path, projects, facts):
    from dataclasses import replace
    from reproagent.core.models import TaskState
    from reproagent.learning_material import prepare_review_input
    from tests.unit.test_learning_review_material import run_task, exploration
    from tests.unit.test_controller import nothing
    result, store, run_ids, context = run_task(tmp_path, projects, facts, plan=[nothing()])
    assert run_ids == []
    session = ExperienceDraftSession(store)
    identity = session.register_source({'kind': 'protocol_error', 'code': 'MULTIPLE_TOOL_CALLS'})
    session.offer(raw_draft(source_ids=[identity]), call_id='draft-call', arguments_hash='arguments-hash')
    built = prepare_review_input(store, replace(result, status=TaskState.EXHAUSTED), context,
        draft=session.latest, exploration=exploration(offset=1))
    assert built.code == 'ready'
    selected = next(e for e in built.material.payload['evidence'] if e['id'] == built.material.payload['draft_evidence_ids'][0])
    from reproagent.experience_triggers import evidence_triggers
    assert [trigger.code for trigger in evidence_triggers(selected)] == ['MULTIPLE_TOOL_CALLS']
    assert json.loads(selected['text'])['source_id'] == identity
