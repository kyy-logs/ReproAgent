import json
import re

from reproagent.adapters.agentscope.runtime import AgentScopeRuntime
from reproagent.adapters.agentscope.tools import build_toolkit
from reproagent.experience_drafts import ExperienceDraftSession
from tests.integration.test_agentscope_runtime import environment, explore, tool_reply, write_payload, SYSTEM_PROMPT


def _runtime(env):
    session = ExperienceDraftSession(env.store)
    toolkit = build_toolkit(env.backend, env.ledger, env.service, env.gate, env.context,
                           experience_mode='draft_review', draft_session=session)
    env.runtime = AgentScopeRuntime(env.model, toolkit, env.gate, env.context,
        system_prompt=SYSTEM_PROMPT, store=env.store, draft_session=session)
    return session


def test_protocol_correction_delivers_a_citable_source_without_running_bad_calls(tmp_path, projects, facts):
    requests = []
    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        if len(requests) == 1:
            return tool_reply(calls=[('Read', {'file_path': 'absent.py'}, 'a'),
                                     ('Read', {'file_path': 'other.py'}, 'b')])
        visible = [json.loads(match) for m in data['messages'] if m.get('role') == 'user'
                   for match in re.findall(r'<learning_source>(.*?)</learning_source>', str(m.get('content', '')))]
        value = write_payload()
        value['lesson_status'] = 'draft'
        value['lesson_draft'] = {'situation': 'when choosing several tools in one response',
            'observation': 'the whole response was rejected before any tool executed',
            'lesson': 'choose one tool per response',
            'source_ids': [visible[-1]['learning_source_id'] if visible else 'missing-source']}
        return tool_reply('write_candidate', value, call_id='publish')
    env = environment(tmp_path, projects, facts, handler=handler)
    session = _runtime(env)
    assert explore(env).kind == 'candidate'
    assert session.latest is not None
    events, errors = env.store.read_events()
    assert not errors
    assert not any(e.kind == 'exploration.action' and e.payload.get('action') == 'Read' for e in events)
    source = next(e for e in events if e.kind == 'experience.source')
    assert source.payload['fact'] == {'kind': 'protocol_error', 'code': 'MULTIPLE_TOOL_CALLS'}


def test_source_storage_failure_preserves_the_actual_error_and_candidate(tmp_path, projects, facts, monkeypatch):
    env = environment(tmp_path, projects, facts, answers=[
        tool_reply('Read', {'file_path': 'absent.py'}, call_id='bad-read'),
        tool_reply('write_candidate', write_payload(), call_id='publish')])
    session = _runtime(env)
    append = env.store.append_event
    def fail_source(kind, refs, payload):
        if kind == 'experience.source':
            raise OSError('source storage unavailable')
        return append(kind, refs, payload)
    monkeypatch.setattr(env.store, 'append_event', fail_source)
    assert explore(env).kind == 'candidate'
    outputs = [str(m.get('content', '')) for m in env.requests[1]['messages'] if m.get('role') == 'tool']
    assert any('file_path must be an absolute path' in text for text in outputs)
    assert all('<learning_source>' not in text for text in outputs)
    assert session.latest is None
