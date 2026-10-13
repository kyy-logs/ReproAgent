import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from reproagent.app import create_controller
from reproagent.core.models import ModelConfig, ModelRequest, ModelResponse, TaskState
from tests.unit.test_controller import setup, ScriptedModel
from tests.unit.test_experience_store import card, library, exp
from tests.integration.test_agentscope_backends import CANDIDATE, tool_response, text_response


def run_sdk_task(tmp_path, projects, facts, *, learning="valid", learn=True, seed=True, fail_export=False, classification="REPRODUCED", priced=False, pitfalls=False, directory=False):
    request, _, _, ctx = setup(tmp_path, projects, facts)
    path = tmp_path / ('shared-experiences' if directory else 'shared-experiences.json')
    if seed:
        seeded = card(summary='empty input advice', detail='HISTORICAL_HINT')
        if directory:
            assert exp().append_experience(path, seeded, None) == 'written'
        else:
            library(path, seeded)
    request = replace(request, experience_file=path, learn_experience=learn)
    script = ScriptedModel(classification=classification)
    seen, main_calls, learning_calls = [], [], []
    holder = {}
    read = False
    listed = False
    file_calls = 0
    async def handle(http_request):
        nonlocal read, listed, file_calls
        payload = json.loads(http_request.content)
        seen.append(payload)
        data = json.loads(payload["messages"][-1]["content"]) if not payload.get("tools") else None
        if data is not None and "evidence" in data and "task_status" in data:
            controller = holder["controller"]
            assert controller._explorer_closed
            assert controller.gateway.gateway.factory._clients == []
            assert controller.explorer.runtime is None
            sealed = controller.store.load_record("task", data_task_id(controller))
            assert sealed.status in (TaskState.DONE, TaskState.FAILED, TaskState.BLOCKED,
                TaskState.NEEDS_INFORMATION, TaskState.EXHAUSTED)
            kind = 'reproduction' if sealed.status == TaskState.DONE else 'diagnostic'
            package = controller.store.root / 'artifacts' / kind
            if not fail_export:
                assert (package / "manifest.json").is_file()
            learning_calls.append(data)
            if learning == "json_mode":
                if not any("json" in m["content"].lower() for m in payload["messages"] if m["role"] == "system"):
                    return httpx.Response(400, json={"error": {"message": "JSON mode requires a JSON instruction"}})
            if learning == "http_error":
                return httpx.Response(401, json={"error": {"message": "invalid authentication"}})
            if learning == "network_error":
                raise httpx.ConnectError("offline connection failed", request=http_request)
            if learning == "timeout":
                await asyncio.Event().wait()
            if learning == "invalid":
                return text_response('{"experience":{"category":"unknown"}}')
            if learning == "missing_evidence":
                return text_response('{"experience":{"category":"workflow","tags":["pytest"],"name":"Steps","description":"When steps are exhausted.","summary":"Steps exhausted.","detail":"E1 describes the observation."}}')
            if learning == "invalid_json":
                return text_response('{"experience":')
            if learning == "invalid_structure":
                return text_response('{"experience":[]}')
            if learning == "root_array":
                return text_response('[]')
            if learning == "root_null":
                return text_response('null')
            if learning == "truncated":
                body = text_response('{}').json()
                body['choices'][0]['finish_reason'] = 'length'
                return httpx.Response(200, json=body)
            if learning == "empty":
                return text_response('{"experience":null}')
            ids = [data["evidence"][0]["id"]]
            if learning == "surrogate":
                return text_response(json.dumps({'experience': {'category':'workflow', 'tags':['pytest'], 'name':'Invalid text', 'description':'When validating generated text.',
                    'summary':'\ud800', 'detail':'invalid Unicode', 'evidence_ids':ids}}))
            return text_response(json.dumps({"experience": {"category": "model", "tags": ["pytest"], "name":"Empty input assertions", "description":"When checking empty input behaviour.",
                "summary": "Empty input assertions need original behaviour evidence.",
                "detail": ('x' * 6000 if learning == 'oversize' else "Condition: empty input. Observation: the original test raised IndexError. Check current source before choosing exception assertions."),
                "evidence_ids": ids}}))
        if payload.get("tools"):
            main_calls.append("exploration")
            view = holder['controller'].experience_service.view
            has_history = view is not None and view.notice() is not None
            if has_history and not listed:
                listed = True
                return tool_response("list_experiences", {"category":"model"}, "catalog")
            if has_history and not read:
                read = True
                for message in reversed(payload['messages']):
                    if message['role'] != 'tool': continue
                    try: page = json.loads(message['content'])
                    except (ValueError, TypeError): continue
                    if page.get('code') == 'ready' and page.get('items'):
                        return tool_response('read_experience', {'id':page['items'][0]['id']}, 'memory')
                return tool_response('request_information', {'question':'No catalog entry was displayed.'}, 'stop')
            if pitfalls and file_calls < 2:
                file_calls += 1
                path = 'absent.py' if file_calls == 1 else 'example/parser.py'
                root = holder['controller'].explorer.project.snapshot.root
                return tool_response('Read', {'file_path':str(root/path)}, f'file{file_calls}')
            if pitfalls == 'information':
                return tool_response('request_information', {'question':'Please clarify the expected result.'}, 'info')
            return tool_response("write_candidate", {"files": [{"path": "tests/test_repro.py",
                "content": CANDIDATE, "role": "test"}], "hypothesis": "empty input"}, "write")
        kind = "verdict" if "available_refs" in data else "contract"
        main_calls.append(kind)
        response = await script.complete(ModelRequest(tuple(payload["messages"]), kind), ctx)
        return text_response(response.text)
    model = ModelConfig(base_url="https://offline.example/v1", model="offline", output_limit_field="max_tokens",
        input_cost_per_million=100 if priced else None, output_cost_per_million=200 if priced else None)
    controller = create_controller(request, model, transport=httpx.MockTransport(handle))
    holder["controller"] = controller
    if fail_export:
        def fail(*args):
            raise OSError("export unavailable")
        controller.exporter.export = fail
    result = asyncio.run(controller.run(request, ctx))
    return request, controller, result, seen, main_calls, learning_calls


def test_learning_sees_sdk_pitfalls_and_citations_with_trace_disabled(tmp_path, projects, facts):
    from reproagent.observability import trace_session
    with trace_session(enabled=False):
        _, controller, result, _, _, learning = run_sdk_task(tmp_path, projects, facts, pitfalls=True)
    assert result.status == TaskState.DONE and len(learning) == 1
    text = json.dumps(learning[0], ensure_ascii=False)
    assert 'absent.py' in text and 'return [values[0]]' in text
    assert 'HISTORICAL_HINT' not in text
    assert controller.explorer.learning_context['tool_errors']
    assert controller.explorer.learning_context['source_refs']
    assert not (controller.store.root/'observability/trace.json').exists()


def test_learning_retains_issued_source_before_any_candidate_is_run(tmp_path, projects, facts):
    _, controller, result, _, _, learning = run_sdk_task(tmp_path, projects, facts, pitfalls='information')
    assert result.status == TaskState.NEEDS_INFORMATION
    assert len(learning) == 1 and controller.learning_result.code == 'written'
    text = json.dumps(learning[0], ensure_ascii=False)
    assert 'return [values[0]]' in text and 'absent.py' in text
    assert learning[0]['attempts'] == []


def test_capture_failure_cannot_replace_sealed_task_or_cleanup(tmp_path, projects, facts, monkeypatch):
    from reproagent import learning_material
    def fail(*args, **kwargs):
        raise RuntimeError('capture unavailable')
    monkeypatch.setattr(learning_material, 'capture_exploration', fail)
    _, controller, result, _, _, learning = run_sdk_task(tmp_path, projects, facts)
    assert result.status == TaskState.DONE and len(learning) == 1
    assert controller.explorer.model_factory is None
    assert {'kind':'exploration', 'code':'CAPTURE_FAILED'} in learning[0]['omissions']


def test_learning_reports_input_overflow_without_creating_model(tmp_path, projects, facts):
    from tests.integration.test_exporter import export_setup
    from reproagent.store import atomic_write
    _, result, store, _, _, _, request = export_setup(tmp_path, projects, facts)
    atomic_write(store.root/'input/issue.md', '大'.encode() * 3000)
    def forbidden(store):
        raise AssertionError('overflow must skip model creation')
    service = exp().ExperienceService(replace(request, experience_file=tmp_path/'shared.json'), store, forbidden)
    context = facts.context()
    service.prepare(None, context)
    outcome = asyncio.run(service.learn(result, context))
    assert outcome.code == 'skipped_input_too_large'
    assert not (store.root/'learning/evidence.jsonl').exists()


def data_task_id(controller):
    # task.json is one record, and its stored task id need not equal the caller's optional id.
    return json.loads((controller.store.root / "task.json").read_text(encoding="utf-8"))["task_id"]


def test_learning_starts_only_after_main_result_is_sealed(tmp_path, projects, facts):
    request, controller, result, seen, main, learning = run_sdk_task(tmp_path, projects, facts)
    assert result.status == TaskState.DONE
    assert len(learning) == 1
    assert controller.learning_result.code == "written"
    assert len(exp().load_experience_snapshot(request.experience_file).cards) == 2
    saved = controller.store.load_record("task", result.task_id)
    assert saved == result
    package = controller.store.root / "artifacts/reproduction"
    assert "learning/evidence.jsonl" not in (package / "manifest.json").read_text(encoding="utf-8")
    assert "Empty input assertions need" not in (package / "report.md").read_text(encoding="utf-8")


def test_json_mode_provider_can_write_learning_without_affecting_main_result(tmp_path, projects, facts):
    request, controller, result, _, _, calls = run_sdk_task(tmp_path, projects, facts, learning="json_mode")
    assert result.status == TaskState.DONE and len(calls) == 1
    assert controller.learning_result.code == "written"
    assert len(exp().load_experience_snapshot(request.experience_file).cards) == 2
    assert controller.store.load_record("task", result.task_id) == result


@pytest.mark.parametrize("learning,count", [("http_error", 1), ("network_error", 3)])
def test_learning_provider_failure_is_not_invalid_experience(tmp_path, projects, facts, learning, count):
    request, controller, result, _, _, calls = run_sdk_task(tmp_path, projects, facts, learning=learning)
    assert result.status == TaskState.DONE and len(calls) == count
    outcome = controller.learning_result
    assert outcome.code == "provider_error" and outcome.http_attempts == count
    assert outcome.unknown_usage_attempts == count and outcome.unknown_cost_attempts == count
    assert len(exp().load_experience_snapshot(request.experience_file).cards) == 1
    assert controller.store.load_record("task", result.task_id) == result


def test_summary_load_occurs_after_analysis_before_first_explore(tmp_path, projects, facts):
    _, controller, result, seen, main, learning = run_sdk_task(tmp_path, projects, facts, learn=False)
    assert result.status == TaskState.DONE and not learning
    assert main == ["contract", "exploration", "exploration", "exploration", "verdict", "verdict"]
    assert "HISTORICAL_HINT" not in json.dumps(seen[0])
    assert "experience_summaries" not in json.dumps(seen[1])
    assert "HISTORICAL_HINT" not in json.dumps(seen[2])
    assert "HISTORICAL_HINT" in json.dumps(seen[3])
    verdicts = [p for p in seen if not p.get("tools") and "available_refs" in json.loads(p["messages"][-1]["content"])]
    assert all("HISTORICAL_HINT" not in json.dumps(p) for p in verdicts)
    assert controller.learning_result.code == "skipped_read_only"


@pytest.mark.parametrize("learning,code", [("invalid", "invalid_missing_fields"), ("empty", "empty"),
    ("missing_evidence", "invalid_missing_fields"), ("invalid_json", "invalid_json"),
    ("invalid_structure", "invalid_structure"), ("root_array", "invalid_structure"),
    ("root_null", "invalid_structure"), ("truncated", "invalid_model_output"),
    ("surrogate", "invalid_fields")])
def test_learning_failure_preserves_task_and_package(tmp_path, projects, facts, learning, code):
    request, controller, result, _, _, calls = run_sdk_task(tmp_path, projects, facts, learning=learning)
    assert result.status == TaskState.DONE
    assert controller.learning_result.code == code and len(calls) == 1
    assert controller.store.load_record("task", result.task_id) == result
    assert len(exp().load_experience_snapshot(request.experience_file).cards) == 1
    if learning == "missing_evidence":
        assert controller.learning_result.missing_fields == ("evidence_ids",)
        events, _ = controller.store.read_events()
        saved = next(e.payload for e in events if e.kind == 'experience.learning')
        assert saved['code'] == 'invalid_missing_fields' and saved['missing_fields'] == ['evidence_ids']


def test_unexpected_learning_exception_is_internal_not_storage_failure(tmp_path, projects, facts, monkeypatch):
    def fail(*args, **kwargs):
        raise KeyError('synthetic-private-runtime-key')
    monkeypatch.setattr(exp(), 'extract_experience', fail)
    request, controller, result, _, _, _ = run_sdk_task(tmp_path, projects, facts)
    assert result.status == TaskState.DONE and controller.learning_result.code == 'internal_error'
    assert controller.store.load_record('task', result.task_id) == result
    assert len(exp().load_experience_snapshot(request.experience_file).cards) == 1
    events, _ = controller.store.read_events()
    summary = next(e.payload for e in events if e.kind == 'experience.learning')
    assert 'synthetic-private-runtime-key' not in json.dumps(summary)


def test_learning_validation_outcome_is_visible_on_its_trace_span(tmp_path, projects, facts):
    from reproagent.observability import trace_session
    with trace_session() as recorder:
        _, controller, result, _, _, calls = run_sdk_task(tmp_path, projects, facts, learning='missing_evidence')
        document = recorder.finish(task_id=result.task_id, status=result.status.value, main_duration=result.duration)
    assert result.status == TaskState.DONE and len(calls) == 1
    learning = next(s for s in document['spans'] if s['name']=='learn')
    assert learning['attributes']['result_code']=='invalid_missing_fields'


def test_export_failure_still_learns_original_observation(tmp_path, projects, facts):
    _, controller, result, _, _, learning = run_sdk_task(tmp_path, projects, facts, fail_export=True)
    assert result.status == TaskState.FAILED and result.export_state == "failed"
    assert len(learning) == 1 and controller.learning_result.code == "written"


@pytest.mark.parametrize("status", ["DONE", "BLOCKED", "NEEDS_INFORMATION", "EXHAUSTED", "FAILED"])
def test_exhausted_main_budget_does_not_cancel_learning_budget(tmp_path, projects, facts, status):
    from tests.unit.test_experience_learning import material_setup
    _, store, result, _, _ = material_setup(tmp_path, projects, facts)
    # The service will build its own capsule, so use a separate fixture that hasn't learned.
    from tests.integration.test_exporter import export_setup
    _, result, store, _, _, snapshot, request = export_setup(tmp_path / "task-source", projects, facts)
    from reproagent.store import atomic_write
    atomic_write(store.root / "input/issue.md", (snapshot.root / "issue.md").read_bytes())
    result = replace(result, status=TaskState(status), export_state="published")
    store.save_record("task", result.task_id, result)
    requests, closed = [], []
    class Gateway:
        async def complete(self, request, context):
            requests.append(context)
            assert context.budget.limits.task_timeout_seconds == 30
            return ModelResponse('{"experience":null}')
        async def aclose(self):
            closed.append(True)
    service = exp().ExperienceService(replace(request, experience_file=tmp_path / "shared.json"), store, lambda store: Gateway())
    main = facts.context()
    service.prepare(None, main)
    main.budget.deadline = 0
    outcome = asyncio.run(service.learn(result, main))
    assert outcome.code == "empty" and len(requests) == 1 and closed
    assert requests[0].budget is not main.budget
    assert store.load_record("task", result.task_id) == result


@pytest.mark.parametrize("mode", ["cancelled", "readonly", "broken", "no_evidence"])
def test_skipped_learning_never_calls_gateway(tmp_path, projects, facts, mode):
    from reproagent.store import TaskStore
    request, _, _, context = setup(tmp_path, projects, facts)
    path = tmp_path / "shared.json"
    request = replace(request, experience_file=path, learn_experience=mode != "readonly")
    if mode == "broken":
        path.write_text("broken", encoding="utf-8")
    def forbidden(store):
        raise AssertionError("no model should be created")
    service = exp().ExperienceService(request, TaskStore(request.output_dir), forbidden)
    service.prepare(None, context)
    from reproagent.core.models import TaskResult
    result = TaskResult("task", TaskState.CANCELLED if mode == "cancelled" else TaskState.EXHAUSTED)
    outcome = asyncio.run(service.learn(result, context))
    assert outcome.code in ("skipped_cancelled", "skipped_read_only", "store_error", "skipped_no_evidence")

def test_learning_timeout_preserves_sealed_result(tmp_path, projects, facts, monkeypatch):
    """The deadline must cut off the call, which means it has to outlast the preparation.

    The learning budget starts before ``build_learning_input`` and the check that raises is
    taken before the request is sent, so a budget shorter than the preparation produces
    ``timeout`` with no request at all -- the same code, from the other side of the call.
    At 0.15s that is what happened on CI, where the whole of the store reading and hashing
    took longer than the budget; 5s keeps the deadline far above preparation and still
    reaches the hanging call, which is the branch this test is about.
    """
    monkeypatch.setattr(exp(), "LEARNING_SECONDS", 5)
    _, controller, result, _, _, calls = run_sdk_task(tmp_path, projects, facts, learning="timeout")
    assert result.status == TaskState.DONE and controller.learning_result.code == "timeout"
    assert len(calls) == 1 and controller.store.load_record("task", result.task_id) == result


def test_learning_store_error_preserves_sealed_result(tmp_path, projects, facts, monkeypatch):
    original = exp().atomic_write
    def conditional(path, content):
        if Path(path).name == "shared-experiences.json":
            raise OSError("library cannot be replaced")
        return original(path, content)
    monkeypatch.setattr(exp(), "atomic_write", conditional)
    request, controller, result, _, _, _ = run_sdk_task(tmp_path, projects, facts)
    assert result.status == TaskState.DONE and controller.learning_result.code == "store_error"
    assert len(exp().load_experience_snapshot(request.experience_file).cards) == 1


def test_experience_inside_fixed_repository_is_rejected_before_model(tmp_path, projects, facts):
    from reproagent.core.models import FixValidationRequest
    request, _, _, ctx = setup(tmp_path, projects, facts)
    fixed = projects.fixed(tmp_path / "fixed")
    request = replace(request, experience_file=fixed / "exp.json")
    def forbidden(request):
        raise AssertionError("bad path must fail before HTTP")
    controller = create_controller(request,
        ModelConfig(base_url="https://offline.example/v1", model="offline"),
        transport=httpx.MockTransport(forbidden))
    result = asyncio.run(controller.run(request, ctx, FixValidationRequest(fixed)))
    assert result.status == TaskState.BLOCKED
    assert not request.experience_file.exists()

def test_busy_library_does_not_change_sealed_result(tmp_path, projects, facts):
    import subprocess
    import sys
    path = tmp_path / "shared-experiences.json"
    library(path, card(summary="empty input advice"))
    before = path.read_bytes()
    script = """import os,sys
f=open(sys.argv[1],'a+b'); f.seek(0); f.write(b'0'); f.flush(); f.seek(0)
if os.name=='nt':
 import msvcrt
 msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
else:
 import fcntl
 fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
print('locked',flush=True)
sys.stdin.readline()
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(path) + ".lock"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        _, controller, result, _, _, calls = run_sdk_task(tmp_path, projects, facts, seed=False)
        assert result.status == TaskState.DONE and controller.learning_result.code == "busy"
        assert len(calls) == 1 and path.read_bytes() == before
    finally:
        child.communicate(chr(10), timeout=10)


@pytest.mark.parametrize("learning,priced", [("empty",False),("valid",True)])
def test_unrecorded_learning_preserves_collected_usage(tmp_path, projects, facts, monkeypatch, learning, priced):
    from reproagent.store import TaskStore
    append = TaskStore.append_event
    def fail_only_final_summary(self, kind, refs=(), payload=None):
        if kind == "experience.learning":
            raise OSError("summary cannot be saved")
        return append(self, kind, refs, payload)
    monkeypatch.setattr(TaskStore, "append_event", fail_only_final_summary)
    request, controller, result, _, _, calls = run_sdk_task(tmp_path, projects, facts, learning=learning, priced=priced)
    assert result.status == TaskState.DONE and len(calls) == 1
    events, errors = controller.store.read_events()
    attempts = [e.payload for e in events if e.kind == "model.attempt" and e.payload.get("response_kind") == "learning"]
    outcome = controller.learning_result
    assert outcome.code == ("written" if priced else "empty") and outcome.http_attempts == len(attempts) == 1
    assert outcome.usage["total_tokens"] == sum(a["usage"]["total_tokens"] for a in attempts) > 0
    assert outcome.unknown_cost_attempts == (0 if priced else 1) and not outcome.event_recorded
    if priced:
        assert outcome.known_cost_subtotal == sum(a["cost_value"] for a in attempts) > 0
        assert outcome.experience_id in {c.id for c in exp().load_experience_snapshot(request.experience_file).cards}
    assert controller.store.load_record("task", result.task_id) == result


def test_deeply_corrupt_optional_library_does_not_stop_main_task(tmp_path, projects, facts):
    path = tmp_path / "shared-experiences.json"
    content = b'{"schema_version":1,"items":' + b"[" * 10000 + b"]" * 10000 + b"}"
    path.write_bytes(content)
    request, controller, result, seen, main, learning = run_sdk_task(tmp_path, projects, facts, seed=False)
    assert result.status == TaskState.DONE and not learning
    assert controller.learning_result.code == "store_error"
    assert all(len(p["tools"]) == 6 for p in seen if p.get("tools"))
    assert path.read_bytes() == content
