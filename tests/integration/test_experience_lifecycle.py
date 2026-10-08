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


def run_sdk_task(tmp_path, projects, facts, *, learning="valid", learn=True, seed=True, fail_export=False, classification="REPRODUCED"):
    request, _, _, ctx = setup(tmp_path, projects, facts)
    path = tmp_path / "shared-experiences.json"
    if seed:
        library(path, card(summary="empty input advice", detail="HISTORICAL_HINT"))
    request = replace(request, experience_file=path, learn_experience=learn)
    script = ScriptedModel(classification=classification)
    seen, main_calls, learning_calls = [], [], []
    holder = {}
    read = False
    async def handle(http_request):
        nonlocal read
        payload = json.loads(http_request.content)
        seen.append(payload)
        data = json.loads(payload["messages"][-1]["content"]) if not payload.get("tools") else None
        if data is not None and "evidence" in data and "task_status" in data:
            controller = holder["controller"]
            assert controller._explorer_closed
            assert controller.gateway.gateway.factory._clients == []
            assert controller.explorer.runtime is None
            sealed = controller.store.load_record("task", data_task_id(controller))
            assert sealed.status in (TaskState.DONE, TaskState.FAILED)
            package = controller.store.root / "artifacts/reproduction"
            if not fail_export:
                assert (package / "manifest.json").is_file()
            learning_calls.append(data)
            if learning == "timeout":
                await asyncio.Event().wait()
            if learning == "invalid":
                return text_response('{"experience":{"category":"unknown"}}')
            if learning == "empty":
                return text_response('{"experience":null}')
            ids = [data["evidence"][0]["id"]]
            return text_response(json.dumps({"experience": {"category": "model", "tags": ["pytest"],
                "summary": "Empty input assertions need original behaviour evidence.",
                "detail": "Condition: empty input. Observation: the original test raised IndexError. Check current source before choosing exception assertions.",
                "evidence_ids": ids}}))
        if payload.get("tools"):
            main_calls.append("exploration")
            if seed and not read:
                summaries = []
                for message in payload["messages"]:
                    if message["role"] == "user":
                        try:
                            summaries = json.loads(message["content"]).get("experience_summaries", summaries)
                        except (ValueError, TypeError):
                            pass
                if summaries:
                    read = True
                    return tool_response("read_experience", {"id": summaries[0]["id"]}, "memory")
            return tool_response("write_candidate", {"files": [{"path": "tests/test_repro.py",
                "content": CANDIDATE, "role": "test"}], "hypothesis": "empty input"}, "write")
        kind = "verdict" if "available_refs" in data else "contract"
        main_calls.append(kind)
        response = await script.complete(ModelRequest(tuple(payload["messages"]), kind), ctx)
        return text_response(response.text)
    model = ModelConfig(base_url="https://offline.example/v1", model="offline", output_limit_field="max_tokens")
    controller = create_controller(request, model, transport=httpx.MockTransport(handle))
    holder["controller"] = controller
    if fail_export:
        def fail(*args):
            raise OSError("export unavailable")
        controller.exporter.export = fail
    result = asyncio.run(controller.run(request, ctx))
    return request, controller, result, seen, main_calls, learning_calls


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


def test_summary_load_occurs_after_analysis_before_first_explore(tmp_path, projects, facts):
    _, controller, result, seen, main, learning = run_sdk_task(tmp_path, projects, facts, learn=False)
    assert result.status == TaskState.DONE and not learning
    assert main == ["contract", "exploration", "exploration", "verdict", "verdict"]
    assert "HISTORICAL_HINT" not in json.dumps(seen[0])
    assert "experience_summaries" in json.dumps(seen[1])
    verdicts = [p for p in seen if not p.get("tools") and "available_refs" in json.loads(p["messages"][-1]["content"])]
    assert all("HISTORICAL_HINT" not in json.dumps(p) for p in verdicts)
    assert controller.learning_result.code == "skipped_read_only"


@pytest.mark.parametrize("learning,code", [("invalid", "invalid"), ("empty", "empty")])
def test_learning_failure_preserves_task_and_package(tmp_path, projects, facts, learning, code):
    request, controller, result, _, _, calls = run_sdk_task(tmp_path, projects, facts, learning=learning)
    assert result.status == TaskState.DONE
    assert controller.learning_result.code == code and len(calls) == 1
    assert controller.store.load_record("task", result.task_id) == result
    assert len(exp().load_experience_snapshot(request.experience_file).cards) == 1


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
    monkeypatch.setattr(exp(), "LEARNING_SECONDS", 0.15)
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
