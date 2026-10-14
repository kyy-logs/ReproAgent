"""Rejected tool parameters must be diagnosable without replaying untrusted text."""
import asyncio
import json

import pytest

from reproagent.adapters.agentscope.middleware import PhaseProtocolError
from tests.integration.test_agentscope_runtime import (
    environment, explore, events, read_reply, tool_reply,
)


@pytest.mark.parametrize('raw,category', [
    ('{\n"file_path":\n}', 'JSON_SYNTAX_ERROR'),
    ('[]', 'JSON_ROOT_NOT_OBJECT'),
    ('{"file_path":"a","file_path":"b"}', 'JSON_DUPLICATE_KEY'),
    ('{"file_path":NaN}', 'JSON_NON_FINITE'),
])
def test_specific_diagnostic_reaches_next_actual_request(tmp_path, projects, facts, raw, category):
    env = environment(tmp_path, projects, facts, answers=[
        tool_reply('Read', raw, call_id='bad'),
        tool_reply('request_information', {'question': 'Need evidence'}, call_id='stop')])
    assert explore(env).kind == 'request_information'
    errors = events(env, 'exploration.protocol_error')
    assert len(errors) == 1
    diagnostic = errors[0]
    assert diagnostic['result_code'] == 'INVALID_TOOL_INPUT'
    assert diagnostic['error_category'] == category
    assert diagnostic['protocol_failures_used'] == 1
    assert diagnostic['protocol_failures_limit'] == 3
    assert diagnostic['protocol_failure_codes'] == ['INVALID_TOOL_INPUT']
    correction = next(message['content'] for message in env.requests[1]['messages']
                      if message.get('role') == 'user' and message['content'].startswith('<system-reminder>'))
    assert category in correction
    if category == 'JSON_SYNTAX_ERROR':
        assert diagnostic['line'] == 3 and diagnostic['column'] == 1
        assert '"line":3' in correction and '"column":1' in correction
    assert len(env.requests) == 2 and env.context.budget.steps_used == 2
    assert not events(env, 'exploration.action')[:-1]


def test_cumulative_mixed_refusals_remain_bounded_and_report_real_sequence(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    env.answers.extend([
        tool_reply(calls=[('Read', {'file_path': 'a'}, 'a'), ('Read', {'file_path': 'b'}, 'b')]),
        read_reply(env.module, 'valid-read'),
        tool_reply('Read', '{"file_path":"PRIVATE_MARKER",}', call_id='bad-json'),
        tool_reply('Read', '[]', call_id='bad-root'),
        tool_reply('request_information', {'question': 'must never be requested'})])
    with pytest.raises(PhaseProtocolError) as caught:
        explore(env)
    assert caught.value.code == 'INVALID_TOOL_INPUT'
    assert caught.value.failures == ('MULTIPLE_TOOL_CALLS', 'INVALID_TOOL_INPUT', 'INVALID_TOOL_INPUT')
    assert 'MULTIPLE_TOOL_CALLS -> INVALID_TOOL_INPUT -> INVALID_TOOL_INPUT' in str(caught.value)
    assert '3/3' in str(caught.value) and 'JSON_ROOT_NOT_OBJECT' in str(caught.value)
    errors = events(env, 'exploration.protocol_error')
    assert [error['protocol_failures_used'] for error in errors] == [1, 2, 3]
    assert errors[-1]['protocol_failure_codes'] == list(caught.value.failures)
    assert len(env.requests) == 4 and env.context.budget.steps_used == 4
    assert [action['action'] for action in events(env, 'exploration.action')] == ['Read']
    # Only the trusted category/location is returned to the model, never the rejected data.
    assert 'PRIVATE_MARKER' not in json.dumps(env.requests)
    assert 'PRIVATE_MARKER' not in json.dumps(errors)
    assert 'PRIVATE_MARKER' not in str(caught.value)


def test_protocol_diagnostic_only_classifies_and_never_repairs():
    from reproagent.adapters.agentscope.protocol_diagnostics import tool_input_diagnostic
    assert tool_input_diagnostic('{"file_path":"a"}') is None
    assert tool_input_diagnostic({'file_path': 'a'}).error_category == 'TOOL_INPUT_TYPE_ERROR'
    assert tool_input_diagnostic(b'{}').error_category == 'TOOL_INPUT_TYPE_ERROR'
    diagnostic = tool_input_diagnostic('{"SECRET_KEY":1,"SECRET_KEY":2}')
    assert diagnostic.error_category == 'JSON_DUPLICATE_KEY'
    assert 'SECRET_KEY' not in str(diagnostic)


def test_rejected_arguments_stay_in_trace_not_the_next_context(tmp_path, projects, facts):
    from reproagent.observability import trace_session
    with trace_session() as recorder:
        env = environment(tmp_path, projects, facts, answers=[
            tool_reply('Read', '{"file_path":"RECORDED_BAD_BODY",}', call_id='bad'),
            tool_reply('request_information', {'question': 'Need evidence'})])
        assert explore(env).kind == 'request_information'
        document = recorder.finish(task_id='protocol-trace', status='NEEDS_INFORMATION', main_duration=1)
    bodies = [record['text'] for record in document['contents'] if record['source'] == 'provider_response']
    assert any('RECORDED_BAD_BODY' in text for text in bodies)
    assert 'RECORDED_BAD_BODY' not in json.dumps(env.requests)
    diagnoses = [span for span in document['spans'] if span['name'] == 'exploration.protocol_error']
    assert len(diagnoses) == 1
    assert diagnoses[0]['attributes']['result_code'] == 'INVALID_TOOL_INPUT'
    assert diagnoses[0]['attributes']['error_category'] == 'JSON_SYNTAX_ERROR'
    assert diagnoses[0]['attributes']['budget']['protocol_failures_used'] == 1
