import importlib
from dataclasses import replace

import pytest

from reproagent.core.models import CandidateDraft, DraftFile, PythonPytestConfig, TaskRequest
from reproagent.store import TaskStore


def prepare(tmp_path, projects, facts, source_style="plain"):
    repo = getattr(projects, source_style)(tmp_path / "中文 repo")
    task = tmp_path / "task"
    request = TaskRequest(repo, task, repo / "issue.md", language=PythonPytestConfig())
    workspace = importlib.import_module("reproagent.workspace").Workspace(task, TaskStore(task))
    snapshot = workspace.freeze(request, facts.context())
    return repo, request, workspace, snapshot


def draft(snapshot, path="tests/test_repro.py", candidate_id="candidate-1"):
    return CandidateDraft((DraftFile(path, b"def test_repro():\n    assert False\n"),), snapshot.snapshot_id, "contract-1", 1, "empty input", candidate_id=candidate_id)


def test_nested_output_is_excluded_from_snapshot(tmp_path, projects, facts):
    repo = projects.plain(tmp_path / "repo")
    output = repo / "repro-output"
    output.mkdir()
    (output / "previous.json").write_text("{}")
    workspace = importlib.import_module("reproagent.workspace").Workspace(output / "task", TaskStore(output / "task"))
    snapshot = workspace.freeze(TaskRequest(repo, output, repo / "issue.md"), facts.context())
    assert not (snapshot.root / "repro-output").exists()
    assert (snapshot.root / "example" / "parser.py").exists()


def test_rejects_install_escape_even_without_symlink_privileges(tmp_path, projects, facts):
    _, _, workspace, snapshot = prepare(tmp_path, projects, facts)
    for path in ("../outside.py", "C:/outside.py", "tests/../escape.py", "tests\\escape.py"):
        with pytest.raises(ValueError):
            workspace.publish(draft(snapshot, path), snapshot)


def test_rejects_external_symlink_and_install_escape(tmp_path, projects, facts):
    repo = projects.plain(tmp_path / "repo")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    try:
        (repo / "linked.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable; path traversal checked separately")
    workspace = importlib.import_module("reproagent.workspace").Workspace(tmp_path / "task", TaskStore(tmp_path / "task"))
    with pytest.raises(ValueError, match="outside|escape"):
        workspace.freeze(TaskRequest(repo, tmp_path / "out", repo / "issue.md"), facts.context())


def test_unicode_space_paths_and_uncommitted_files_survive(tmp_path, projects, facts):
    repo, request, workspace, snapshot = prepare(tmp_path, projects, facts, "src_layout")
    payload = b"def parse(values):\r\n    return values\r\n"
    (repo / "src" / "example" / "parser.py").write_bytes(payload)
    snapshot2 = workspace.freeze(request, facts.context())
    assert (snapshot2.root / "src" / "example" / "parser.py").read_bytes() == payload


def test_each_run_starts_clean_and_candidate_is_immutable(tmp_path, projects, facts):
    repo, _, workspace, snapshot = prepare(tmp_path, projects, facts)
    candidate = workspace.publish(draft(snapshot), snapshot)
    first = workspace.fresh_run(snapshot, candidate)
    (first.root / "generated.txt").write_text("dirty")
    second = workspace.fresh_run(snapshot, candidate)
    assert not (second.root / "generated.txt").exists()
    assert (second.root / "tests" / "test_repro.py").read_bytes() == b"def test_repro():\n    assert False\n"
    assert not (repo / "tests" / "test_repro.py").exists()
    with pytest.raises(ValueError, match="immutable"):
        workspace.publish(draft(snapshot), snapshot)


@pytest.mark.parametrize("relative", ["example/parser.py", "tests/test_existing.py", "tests/conftest.py", "pyproject.toml"])
def test_protected_source_tests_and_config_changes_are_detected(tmp_path, projects, facts, relative):
    repo = projects.plain(tmp_path / "repo")
    (repo / "tests" / "conftest.py").write_text("# fixture")
    (repo / "pyproject.toml").write_text("# config")
    workspace = importlib.import_module("reproagent.workspace").Workspace(tmp_path / "task", TaskStore(tmp_path / "task"))
    snapshot = workspace.freeze(TaskRequest(repo, tmp_path / "out", repo / "issue.md"), facts.context())
    candidate = workspace.publish(draft(snapshot), snapshot)
    run = workspace.fresh_run(snapshot, candidate)
    (run.root / relative).write_text("modified")
    assert relative in workspace.check(run, snapshot, candidate).modified


def test_candidate_content_change_is_detected(tmp_path, projects, facts):
    _, _, workspace, snapshot = prepare(tmp_path, projects, facts)
    candidate = workspace.publish(draft(snapshot), snapshot)
    run = workspace.fresh_run(snapshot, candidate)
    (run.root / "tests" / "test_repro.py").write_text("changed")
    assert not workspace.check(run, snapshot, candidate).candidate_ok
