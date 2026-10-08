import asyncio
from dataclasses import replace
from reproagent.core.models import ModelConfig
from evals.schema import EvalResult
from evals.swt_bench.run import run_batch
from tests.integration.test_swt_batch import inputs
from tests.unit.test_experience_store import card, library, exp


def test_frozen_experience_round_never_writes(tmp_path, projects):
    catalog, manifest, bindings = inputs(tmp_path, projects)
    path = tmp_path / "shared.json"
    library(path, card())
    before = path.read_bytes()
    calls = []
    async def run_one(case, model, output, **kwargs):
        calls.append(kwargs)
        return EvalResult(case.case_id, status="EXHAUSTED", duration=1)
    result = asyncio.run(run_batch(catalog, manifest, bindings, ModelConfig(), tmp_path / "batch",
        run_one=run_one, experience_file=path))
    assert len(calls) == 1 and calls[0]["learn_experience"] is False
    assert calls[0]["experience_file"] == path.resolve()
    assert path.read_bytes() == before
    assert result["configuration"]["experience_library_hash"] == exp().load_experience_snapshot(path).content_hash


def test_changed_library_invalidates_frozen_round(tmp_path, projects):
    catalog, manifest, bindings = inputs(tmp_path, projects)
    bindings["fixture__repo-2"] = {**bindings["fixture__repo-1"], "instance_id": "fixture__repo-2"}
    path = tmp_path / "shared.json"
    library(path, card())
    calls = []
    async def run_one(case, model, output, **kwargs):
        calls.append(case.case_id)
        library(path, card("changed"))
        return EvalResult(case.case_id, status="EXHAUSTED", duration=1)
    result = asyncio.run(run_batch(catalog, manifest, bindings, ModelConfig(), tmp_path / "batch",
        run_one=run_one, experience_file=path))
    assert calls == ["fixture__repo-1"]
    assert len(result["outcomes"]) == 2
    assert result["outcomes"]["fixture__repo-2"]["status"] == "EXPERIENCE_SOURCE_CHANGED"
    assert result["source_comparison_status"] == "changed"


def test_total_cost_includes_learning_once():
    from evals.swt_bench.results import summarize_round
    round_data = {"outcomes": {"case": {"status": "DONE", "duration": 1, "http_attempts": 5,
        "usage": {"total_tokens": 75}, "known_cost_subtotal": 0.3,
        "learning": {"code": "written", "duration": 0.2, "http_attempts": 1,
            "usage": {"total_tokens": 15}, "known_cost_subtotal": 0.1}}}}
    summary = summarize_round(round_data)
    assert summary["http_attempts"] == 5 and summary["total_tokens"] == 75
    assert summary["known_cost_subtotal"] == 0.3
    assert summary["learning_http_attempts"] == 1 and summary["learning_total_tokens"] == 15
    assert summary["learning_duration"] == 0.2


def test_run_case_reads_frozen_library_with_actual_sdk(tmp_path, projects, facts, monkeypatch):
    import json, sys, httpx
    from evals import run
    from evals.schema import EvalCase
    from reproagent.app import create_controller
    from reproagent.core.models import ModelRequest
    from tests.unit.test_controller import ScriptedModel
    from tests.integration.test_agentscope_backends import CANDIDATE, tool_response, text_response
    buggy = projects.plain(tmp_path / "buggy")
    fixed = projects.fixed(tmp_path / "fixed")
    path = tmp_path / "memory.json"
    library(path, card(summary="empty input advice"))
    before = path.read_bytes()
    script = ScriptedModel()
    seen, controllers = [], []
    async def handle(request):
        payload = json.loads(request.content)
        seen.append(payload)
        if payload.get("tools"):
            return tool_response("write_candidate", {"files": [{"path": "tests/test_repro.py",
                "content": CANDIDATE, "role": "test"}], "hypothesis": "empty input"}, "write")
        data = json.loads(payload["messages"][-1]["content"])
        assert "task_status" not in data
        kind = "verdict" if "available_refs" in data else "contract"
        response = await script.complete(ModelRequest(tuple(payload["messages"]), kind), facts.context())
        return text_response(response.text)
    def factory(request, model, **kwargs):
        assert request.learn_experience is False
        controller = create_controller(request, model, transport=httpx.MockTransport(handle), **kwargs)
        controllers.append(controller)
        return controller
    monkeypatch.setattr(run, "create_controller", factory)
    case = EvalCase("case", "https://offline.example/issue", "hash", buggy, "old", fixed, "new",
        sys.executable, sys.executable, (buggy / "issue.md").read_text(encoding="utf-8"),
        review_status="approved", target_modules=("example.parser",), source_roots=(".",), candidate_parent="tests")
    result = asyncio.run(run.run_case(case, ModelConfig(base_url="https://offline.example/v1", model="offline"),
        tmp_path / "evaluation", experience_file=path))
    assert result.status == "DONE" and result.fix_validation_status == "passed"
    assert result.learning["http_attempts"] == 0
    assert result.experience_library_hash == exp().load_experience_snapshot(path).content_hash
    assert path.read_bytes() == before
    assert any("experience_summaries" in json.dumps(p) for p in seen if p.get("tools"))


def test_round_rejects_library_in_target_before_output(tmp_path, projects):
    import pytest
    catalog, manifest, bindings = inputs(tmp_path, projects)
    path = tmp_path / "buggy" / "memory.json"
    library(path, card())
    output = tmp_path / "batch"
    with pytest.raises(ValueError, match="overlaps"):
        asyncio.run(run_batch(catalog, manifest, bindings, ModelConfig(), output, experience_file=path))
    assert not output.exists()
