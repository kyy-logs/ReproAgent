"""The evaluation entry point must retain the same evidence as the product CLI."""
import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

from evals.run import run_case
from evals.schema import EvalCase
from reproagent.app import create_controller
from reproagent.core.models import ModelConfig
from tests.integration.test_agentscope_backends import text_response, tool_response
from tests.integration.test_agentscope_runtime import tool_reply


def eval_environment(tmp_path, projects, monkeypatch, *, terminal_failure=False):
    import evals.run as entry
    original = projects.plain(tmp_path / 'original')
    fixed = projects.fixed(tmp_path / 'fixed')
    case = EvalCase('offline-case', 'https://example.org/issue', 'issue-hash', original, 'base', fixed, 'fix',
                    sys.executable, sys.executable, 'parse([]) should return [], but raises IndexError. SYNTHETIC_PRIVATE_KEY',
                    review_status='approved', target_modules=('example',))
    monkeypatch.setenv('EVAL_OFFLINE_KEY', 'SYNTHETIC_PRIVATE_KEY')
    model = ModelConfig(base_url='https://offline.example/v1', model='offline',
                        api_key_env='EVAL_OFFLINE_KEY', output_limit_field='max_tokens')
    requests, controllers = [], []
    def assemble(request, config, **kwargs):
        exploration_calls = 0
        async def handle(http_request):
            nonlocal exploration_calls
            payload = json.loads(http_request.content)
            requests.append(payload)
            if not payload.get('tools'):
                return text_response(json.dumps({'trigger': 'empty input', 'expected': 'return []',
                    'reported_actual': 'IndexError', 'source_indices': [0], 'observable_checks': [],
                    'assumptions': [], 'missing_information': []}))
            exploration_calls += 1
            if terminal_failure or exploration_calls == 1:
                return tool_reply('Read', '{"file_path":"ORIGINAL_ARG_MARKER",}', call_id=f'bad-{exploration_calls}')
            return tool_response('request_information', {'question': 'Need more evidence'}, 'stop')
        controller = create_controller(request, config, transport=httpx.MockTransport(handle), **kwargs)
        controllers.append(controller)
        return controller
    monkeypatch.setattr(entry, 'create_controller', assemble)
    return case, model, requests, controllers


def test_eval_default_trace_keeps_refused_body_and_final_failure(tmp_path, projects, monkeypatch):
    case, model, requests, controllers = eval_environment(tmp_path, projects, monkeypatch, terminal_failure=True)
    result = asyncio.run(run_case(case, model, tmp_path / 'out'))
    assert result.status == 'FAILED' and result.stop_reason == 'MODEL_PROTOCOL_ERROR'
    assert result.http_attempts == 4 and len(requests) == 4  # One analysis + three refused calls.
    path = tmp_path / 'out/task/observability/trace.json'
    assert path.is_file(), 'the actual evaluation entry must write its default per-task trace'
    assert path.with_suffix('.html').is_file()
    raw = path.read_text(encoding='utf-8')
    assert 'SYNTHETIC_PRIVATE_KEY' not in raw
    document = json.loads(raw)
    assert any('ORIGINAL_ARG_MARKER' in record.get('text', '') for record in document['contents']
               if record['source'] == 'provider_response')
    errors = [span for span in document['spans'] if span['name'] == 'exploration.protocol_error']
    assert [span['attributes']['budget']['protocol_failures_used'] for span in errors] == [1, 2, 3]
    assert all(span['attributes']['error_category'] == 'JSON_SYNTAX_ERROR' for span in errors)
    assert controllers[0].store.load_record('task', 'task').status.value == result.status


def test_eval_trace_on_off_preserves_model_calls_and_outcome(tmp_path, projects, monkeypatch):
    case, model, requests, _ = eval_environment(tmp_path, projects, monkeypatch)
    plain = asyncio.run(run_case(case, model, tmp_path / 'plain', trace=False))
    first_calls = len(requests)
    traced = asyncio.run(run_case(case, model, tmp_path / 'traced'))
    assert (plain.status, plain.evidence_level, plain.http_attempts, plain.usage) == (
        traced.status, traced.evidence_level, traced.http_attempts, traced.usage)
    assert plain.status == 'NEEDS_INFORMATION'
    assert first_calls == 3 and len(requests) == 6
    assert not (tmp_path / 'plain/task/observability').exists()
    assert (tmp_path / 'traced/task/observability/trace.json').is_file()
    correction = next(m['content'] for m in requests[-1]['messages']
                      if m.get('role') == 'user' and m['content'].startswith('<system-reminder>'))
    assert 'JSON_SYNTAX_ERROR' in correction and 'ORIGINAL_ARG_MARKER' not in correction


@pytest.mark.parametrize('fault', ['json', 'html', 'finish'])
def test_eval_observation_failure_keeps_result_and_counter(tmp_path, projects, monkeypatch, capsys, fault):
    case, model, requests, _ = eval_environment(tmp_path, projects, monkeypatch)
    from reproagent import trace_output
    def unavailable(*args, **kwargs):
        raise OSError('UNSAFE_RAW_ERROR_DETAILS')
    if fault == 'finish':
        from reproagent.observability import TraceRecorder
        monkeypatch.setattr(TraceRecorder, 'finish', unavailable)
    else:
        monkeypatch.setattr(trace_output, 'write_trace' if fault == 'json' else 'write_trace_html', unavailable)
    result = asyncio.run(run_case(case, model, tmp_path / 'out'))
    assert result.status == 'NEEDS_INFORMATION' and result.http_attempts == 3 and len(requests) == 3
    error = capsys.readouterr().err
    assert 'UNSAFE_RAW_ERROR_DETAILS' not in error
    assert 'reproagent:' in error
    if fault in ('json', 'finish'):
        assert not (tmp_path / 'out/task/observability/trace.html').exists()


def test_two_eval_cases_keep_separate_traces(tmp_path, projects, monkeypatch):
    from dataclasses import replace
    case, model, requests, _ = eval_environment(tmp_path, projects, monkeypatch)
    async def run():
        first = await run_case(replace(case, case_id='case-one', allowed_description=case.allowed_description+' CASE_ONE'),
                               model, tmp_path / 'one')
        second = await run_case(replace(case, case_id='case-two', allowed_description=case.allowed_description+' CASE_TWO'),
                                model, tmp_path / 'two')
        return first, second
    results = asyncio.run(run())
    documents = [json.loads((tmp_path / name / 'task/observability/trace.json').read_text(encoding='utf-8'))
                 for name in ('one', 'two')]
    assert [doc['task_id'] for doc in documents] == ['case-one', 'case-two']
    assert documents[0]['trace_id'] != documents[1]['trace_id']
    assert 'CASE_TWO' not in json.dumps(documents[0]) and 'CASE_ONE' not in json.dumps(documents[1])
    assert all(result.status == 'NEEDS_INFORMATION' for result in results) and len(requests) == 6


def test_eval_result_preserves_task_candidate_and_export_state(tmp_path, projects, monkeypatch):
    from dataclasses import asdict
    case, model, requests, _ = eval_environment(tmp_path, projects, monkeypatch)
    result = asyncio.run(run_case(case, model, tmp_path / "out"))
    task = json.loads((tmp_path / "out/task/task.json").read_text(encoding="utf8"))
    saved = asdict(result)
    assert saved["accepted_candidate_id"] == task["accepted_candidate_id"]
    assert saved["export_state"] == task["export_state"] == "published"
