import asyncio
import json
from dataclasses import replace

import pytest

from reproagent.core.serialization import canonical_bytes
from reproagent.experience_drafts import CapturedDraft, LessonDraft
from reproagent.learning_material import prepare_review_input
from reproagent.core.models import VerificationIssue
from reproagent.core.serialization import encode_record, bytes_hash
from reproagent.store import atomic_write
from tests.unit.test_controller import PASSING, publish, setup


def review_draft(source_ids):
    return CapturedDraft(LessonDraft("situation", "observation", "lesson", tuple(source_ids)),
                         "draft-call", 99, "arguments-hash")


def exploration(*, offset=5):
    return {
        "source_refs": (),
        "tool_errors": (
            {"tool": "Read", "tool_call_id": "read-1", "state": "error", "message_offset": offset,
             "block_index": 0, "arguments": {"file_path": "absent.py"}, "result": "absent",
             "link_state": "matched"},
        ),
        "capture_issues": (),
        "draft_origin": {"call_id": "draft-call", "message_offset": 5, "block_index": 0,
                         "arguments_hash": "arguments-hash"},
    }


def run_task(tmp_path, projects, facts, plan=None):
    request, model, controller, context = setup(tmp_path, projects, facts, plan=plan or [publish(content=PASSING)])
    result = asyncio.run(controller.run(request, context))
    events, errors = controller.store.read_events()
    assert not errors
    run_ids = [event.refs[0].split("/")[1] for event in events if event.kind == "run.completed"]
    return result, controller.store, run_ids, context


def test_draft_sources_bind_to_real_facts_and_add_review_envelope(tmp_path, projects, facts):
    result, store, run_ids, context = run_task(tmp_path, projects, facts)
    assert run_ids
    draft = review_draft(("read-1",))
    built = prepare_review_input(store, result, context, draft=draft, exploration=exploration(offset=1))
    assert built.code == "ready", built
    payload = built.material.payload
    assert payload["draft"] == {"situation": "situation", "observation": "observation",
                                "lesson": "lesson", "source_ids": ["read-1"]}
    assert payload["draft_evidence_ids"]
    assert set(payload["response_schema"]["properties"]) == {"decision", "reason_code", "experience"}
    assert len(canonical_bytes(payload)) <= 8192


def test_original_run_id_binds_to_the_attempt_fact(tmp_path, projects, facts):
    result, store, run_ids, context = run_task(tmp_path, projects, facts)
    originals = [run_id for run_id in run_ids
                 if store.load_record("runs", run_id).execution_role == "original"]
    assert originals
    built = prepare_review_input(store, result, context, draft=review_draft((originals[0],)),
                                 exploration=exploration(offset=1))
    assert built.code == "ready", built
    assert built.material.payload["draft_evidence_ids"]


@pytest.mark.parametrize('protocol_error,expected', [
    ('invalid_negative_response', 'invalid_negative_response'),
    ('private-unrecognized-diagnostic', 'unrecognized'),
])
def test_review_retains_bound_protocol_diagnostics_without_provider_prose(
        tmp_path, projects, facts, protocol_error, expected):
    result, store, run_ids, context = run_task(tmp_path, projects, facts)
    run_id = run_ids[-1]
    path = store.record_path('verdicts', 'verdict-' + run_id)
    verdict = store.load_record('verdicts', 'verdict-' + run_id)
    issue = VerificationIssue('MODEL_PROTOCOL_ERROR', 'semantic_response_valid',
        'program_check', 'PRIVATE_PROVIDER_EXPLANATION',
        measurements={'attempts': 1, 'protocol_error': protocol_error})
    changed = replace(verdict, classification=None, issues=(issue,))
    atomic_write(path, canonical_bytes(encode_record(changed)))
    captured = exploration(offset=1)
    prototype = captured['tool_errors'][0]
    captured['tool_errors'] = tuple({**prototype, 'tool_call_id': f'optional-{i}',
                                    'result': 'unrelated-' + 'x' * 1200} for i in range(7))
    built = prepare_review_input(store, result, context, draft=review_draft((run_id,)),
                                 exploration=captured)
    assert built.code == 'ready'
    payload = built.material.payload
    atom = next(e for e in payload['evidence'] if e['id'] == payload['draft_evidence_ids'][0])
    summary = json.loads(atom['text'])
    assert summary['classification'] is None
    assert summary['confirmed'] is False
    assert summary['verdict_diagnostics'] == [
        {'code': 'MODEL_PROTOCOL_ERROR', 'origin': 'program_check', 'protocol_error': expected}]
    assert 'PRIVATE_PROVIDER_EXPLANATION' not in canonical_bytes(payload).decode()
    assert 'private-unrecognized-diagnostic' not in canonical_bytes(payload).decode()
    assert len(canonical_bytes(payload)) <= 8192
    frozen = [json.loads(line) for line in (store.root / 'learning/evidence.jsonl').read_text().splitlines()]
    stored_atom = next(e for e in frozen if e['id'] == atom['id'])
    assert any(ref['path'] == path.relative_to(store.root).as_posix()
               and ref['content_hash'] == bytes_hash(path.read_bytes())
               for ref in stored_atom['origin_refs'])


def test_review_bounds_diagnostics_and_reports_omissions(tmp_path, projects, facts):
    result, store, run_ids, context = run_task(tmp_path, projects, facts)
    run_id = run_ids[-1]
    path = store.record_path('verdicts', 'verdict-' + run_id)
    verdict = store.load_record('verdicts', 'verdict-' + run_id)
    issue = VerificationIssue('MODEL_PROTOCOL_ERROR', 'semantic_response_valid', 'program_check',
                              'not copied', measurements={'protocol_error': 'JSONDecodeError'})
    atomic_write(path, canonical_bytes(encode_record(replace(verdict, issues=(issue,) * 7))))
    built = prepare_review_input(store, result, context, draft=review_draft((run_id,)),
                                 exploration=exploration(offset=1))
    assert built.code == 'ready'
    summary = json.loads(next(e['text'] for e in built.material.payload['evidence']
                             if e['id'] == built.material.payload['draft_evidence_ids'][0]))
    assert len(summary['verdict_diagnostics']) == 3
    assert summary['verdict_diagnostics_omitted'] == 4


def test_unknown_future_or_self_source_is_not_guessed(tmp_path, projects, facts):
    cases = [
        (("unknown-id",), 5),
        (("draft-call",), 5),
        (("read-1",), 10),  # tool error happened after the draft call
    ]
    for index, (source_ids, offset) in enumerate(cases):
        result, store, run_ids, context = run_task(tmp_path / f"case-{index}", projects, facts)
        assert run_ids
        built = prepare_review_input(store, result, context, draft=review_draft(source_ids),
                                     exploration=exploration(offset=offset))
        assert built.code == "skipped_draft_evidence_unavailable", (source_ids, offset)


@pytest.mark.parametrize(('source_block', 'want'), [
    (1, 'ready'), (2, 'skipped_draft_evidence_unavailable'),
    (3, 'skipped_draft_evidence_unavailable'),
])
def test_same_message_sources_use_block_order(tmp_path, projects, facts, source_block, want):
    result, store, _, context = run_task(tmp_path, projects, facts)
    captured = exploration(offset=5)
    captured['draft_origin']['block_index'] = 2
    captured['tool_errors'][0]['block_index'] = source_block
    built = prepare_review_input(store, result, context, draft=review_draft(('read-1',)), exploration=captured)
    assert built.code == want
    if want != 'ready':
        assert not (store.root / 'learning/evidence.jsonl').exists()


def test_review_prioritizes_its_source_over_unrelated_errors(tmp_path, projects, facts):
    result, store, _, context = run_task(tmp_path, projects, facts)
    captured = exploration(offset=1)
    prototype = captured['tool_errors'][0]
    captured['tool_errors'] = tuple({**prototype, 'tool_call_id': f'optional-{i}',
                                    'result': 'unrelated-' + 'x' * 1200} for i in range(7)) + (
        {**prototype, 'result': 'REQUIRED_PATH_FAILURE'},)
    built = prepare_review_input(store, result, context, draft=review_draft(('read-1',)), exploration=captured)
    assert built.code == 'ready', built
    payload = built.material.payload
    assert len(canonical_bytes(payload)) <= 8192
    required = next(e for e in payload['evidence'] if e['id'] == payload['draft_evidence_ids'][0])
    assert 'REQUIRED_PATH_FAILURE' in required['text']
    assert any(o['code'] == 'INPUT_BUDGET' for o in payload['omissions'])
    frozen = [json.loads(line) for line in (store.root / 'learning/evidence.jsonl').read_text(encoding='utf-8').splitlines()]
    assert {e['id'] for e in frozen} == {e['id'] for e in payload['evidence']}
    assert set(built.material.evidence_map) == {e['id'] for e in frozen}
    assert all(set(a['evidence_ids']) <= set(built.material.evidence_map) for a in payload['attempts'])


def test_unavailable_required_source_does_not_freeze_an_unusable_capsule(tmp_path, projects, facts):
    result, store, _, context = run_task(tmp_path, projects, facts)
    captured = exploration(offset=1)
    captured['tool_errors'][0]['result'] = 'oversized-' * 500
    built = prepare_review_input(store, result, context, draft=review_draft(('read-1',)), exploration=captured)
    assert built.code == 'skipped_draft_evidence_unavailable'
    assert not (store.root / 'learning/evidence.jsonl').exists()


def test_future_original_run_cannot_support_an_earlier_draft(tmp_path, projects, facts):
    result, store, run_ids, context = run_task(tmp_path, projects, facts)
    built = prepare_review_input(store, result, context, draft=replace(review_draft((run_ids[0],)), event_seq=0),
                                 exploration=exploration(offset=1))
    assert built.code == 'skipped_draft_evidence_unavailable'
    assert not (store.root / 'learning/evidence.jsonl').exists()


@pytest.mark.parametrize('second_tool', ['Read', 'read_experience', 'unknown_tool'])
@pytest.mark.parametrize('redact', [False, True])
def test_reused_call_id_cannot_bind_an_earlier_error(tmp_path, projects, facts, second_tool, redact):
    from agentscope.message import AssistantMsg, ToolCallBlock, ToolResultBlock
    from reproagent.learning_material import capture_exploration
    from reproagent.core.serialization import canonical_hash
    result, store, _, context = run_task(tmp_path, projects, facts)
    call_id = 'read-SECRET_TOKEN' if redact else 'read-1'
    draft = review_draft(('read-[REDACTED]' if redact else call_id,))
    args = {'files': [], 'hypothesis': 'h', 'lesson_draft': {'situation': 's', 'observation': 'o',
            'lesson': 'l', 'source_ids': [call_id]}}
    draft = replace(draft, arguments_hash=canonical_hash(args))
    message = AssistantMsg(name='explorer', content=[
        ToolCallBlock(id=call_id, name='Read', input='{"file_path":"absent.py"}'),
        ToolResultBlock(id=call_id, name='Read', output='absent', state='error'),
        ToolCallBlock(id=call_id, name=second_tool, input='{}'),
        ToolResultBlock(id=call_id, name=second_tool, output='ok', state='success'),
        ToolCallBlock(id='draft-call', name='write_candidate', input=canonical_bytes(args).decode()),
    ])
    captured = capture_exploration([message], (), draft=draft, secrets=('SECRET_TOKEN',) if redact else ())
    built = prepare_review_input(store, result, context, draft=draft, exploration=captured)
    assert built.code == 'skipped_draft_evidence_unavailable'
    assert not (store.root / 'learning/evidence.jsonl').exists()
