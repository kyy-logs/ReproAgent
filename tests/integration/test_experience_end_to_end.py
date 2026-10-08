import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

from reproagent.core.models import TaskResult, TaskState
from reproagent.experience import LearningResult
from tests.integration.test_experience_lifecycle import run_sdk_task
from tests.unit.test_experience_store import exp


def test_report_contains_only_actually_read_ids(tmp_path, projects, facts):
    request, controller, result, _, _, _ = run_sdk_task(tmp_path, projects, facts, learn=False)
    report = json.loads((request.output_dir / "artifacts/reproduction/report.json").read_text(encoding="utf-8"))
    assert report["experience_read_ids"] == list(controller.experience_service.view.read_ids)
    assert report["experience_read_ids"][0] in (request.output_dir / "artifacts/reproduction/report.md").read_text(encoding="utf-8")
    request2, _, _, _, _, _ = run_sdk_task(tmp_path / "no-read", projects, facts, learn=False, seed=False)
    report2 = json.loads((request2.output_dir / "artifacts/reproduction/report.json").read_text(encoding="utf-8"))
    assert report2["experience_read_ids"] == []


def test_task_a_learns_and_task_b_reads_without_changing_verdict(tmp_path, projects, facts):
    from dataclasses import replace
    # First task learns in an empty library and does not see its own new card.
    request, controller, result, _, _, _ = run_sdk_task(tmp_path / "a", projects, facts, seed=False)
    assert result.status == TaskState.DONE and controller.learning_result.code == "written"
    assert controller.experience_service.view.read_ids == ()
    learned = exp().load_experience_snapshot(request.experience_file).cards
    assert len(learned) == 1
    from tests.unit.test_experience_store import library
    destination = tmp_path / "b"
    destination.mkdir()
    library(destination / "shared-experiences.json", *learned)
    # seed=True is used by the scripted exploration to read; prevent helper from replacing the learned fixture.
    from tests.integration import test_experience_lifecycle as helpers
    original = helpers.library
    helpers.library = lambda *args: None
    try:
        req2, ctrl2, res2, seen, _, calls = run_sdk_task(destination, projects, facts, learn=False)
    finally:
        helpers.library = original
    assert res2.status == TaskState.DONE and not calls
    assert ctrl2.experience_service.view.read_ids == (learned[0].id,)
    assert all("historical suggestions" not in json.dumps(p).lower() for p in seen if not p.get("tools"))
    package = req2.output_dir / "artifacts/reproduction"
    fresh = projects.plain(tmp_path / "fresh")
    replay = subprocess.run([sys.executable, str(package / "replay.py"), "--repo", str(fresh),
        "--python", sys.executable, "--output", str(tmp_path / "replayed"), "--install"], capture_output=True, timeout=30)
    assert replay.returncode == 1, replay.stderr.decode(errors="replace")
    assert "learning/evidence.jsonl" not in (package / "manifest.json").read_text(encoding="utf-8")


def test_cli_reports_learning_separately(tmp_path, projects, monkeypatch, capsys):
    from reproagent import cli
    repo = projects.plain(tmp_path / "repo")
    config = tmp_path / "task.json"
    config.write_text(json.dumps({"schema_version": 1, "repo": str(repo),
        "issue_file": str(repo / "issue.md"), "output_dir": str(tmp_path / "task")}), encoding="utf-8")
    model = tmp_path / "model.json"
    model.write_text('{"base_url":"https://offline.example/v1","model":"offline"}', encoding="utf-8")
    class Controller:
        learning_result = LearningResult("empty", duration=0.1, http_attempts=1, usage={"total_tokens":15})
        runner = SimpleNamespace(adapter=SimpleNamespace(inspect=lambda *args: None))
        async def run(self, *args):
            return TaskResult("task", TaskState.DONE, duration=1)
    monkeypatch.setattr(cli, "create_controller", lambda *args, **kwargs: Controller())
    monkeypatch.setenv("REPROAGENT_API_KEY", "offline-test")
    assert cli.main(["run", "--config", str(config), "--model-config", str(model)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "DONE" and output["learning"]["code"] == "empty"
    assert output["learning"]["usage"]["total_tokens"] == 15


def test_advice_cannot_override_current_evidence(tmp_path, projects, facts):
    request, controller, result, seen, main, learning = run_sdk_task(
        tmp_path, projects, facts, learn=False, classification="NOT_REPRODUCED")
    assert controller.experience_service.view.read_ids
    assert result.status != TaskState.DONE and not learning
    assert not (request.output_dir / "artifacts/reproduction").exists()
    assert "verdict" in main


def test_cancelled_task_never_teaches(tmp_path, projects, facts):
    from dataclasses import replace
    from reproagent.app import create_controller
    from reproagent.core.models import ModelConfig
    from tests.unit.test_controller import setup
    import httpx
    request, _, _, context = setup(tmp_path, projects, facts)
    request = replace(request, experience_file=tmp_path / "shared.json")
    context.cancel_event.set()
    def refuse(request):
        raise AssertionError("cancelled tasks cannot send model requests")
    controller = create_controller(request, ModelConfig(base_url="https://offline.example/v1", model="offline"), transport=httpx.MockTransport(refuse))
    result = asyncio.run(controller.run(request, context))
    assert result.status == TaskState.CANCELLED
    assert controller.learning_result.http_attempts == 0
    assert not request.experience_file.exists()


def test_cli_warns_when_learning_summary_could_not_be_saved(tmp_path, projects, monkeypatch, capsys):
    from reproagent import cli
    repo = projects.plain(tmp_path / "repo")
    config = tmp_path / "task.json"
    config.write_text(json.dumps({"schema_version":1,"repo":str(repo),"issue_file":str(repo/"issue.md"),
        "output_dir":str(tmp_path/"task")}), encoding="utf-8")
    model = tmp_path / "model.json"
    model.write_text('{"base_url":"https://offline.example/v1","model":"offline"}', encoding="utf-8")
    class Controller:
        learning_result = LearningResult("empty", http_attempts=1, usage={"total_tokens":15}, event_recorded=False)
        runner = SimpleNamespace(adapter=SimpleNamespace(inspect=lambda *args: None))
        async def run(self, *args):
            return TaskResult("task", TaskState.DONE)
    monkeypatch.setattr(cli, "create_controller", lambda *args, **kwargs: Controller())
    monkeypatch.setenv("REPROAGENT_API_KEY", "offline-test")
    assert cli.main(["run", "--config", str(config), "--model-config", str(model)]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["learning"]["http_attempts"] == 1
    assert "could not be saved" in output.err
