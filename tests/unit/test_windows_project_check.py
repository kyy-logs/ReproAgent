import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.project_check import check_project, summary_lines


def _project(tmp_path):
    root = tmp_path / "target"
    (root / "tests").mkdir(parents=True)
    (root / "tests/test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname="demo"\nrequires-python=">=3.9"\n', encoding="utf-8"
    )
    return root


def test_light_check_reports_python_pytest_and_test_dir(tmp_path):
    root = _project(tmp_path)
    result = check_project(root, python=sys.executable)
    assert result["schema_version"] == 1
    assert result["python"]["path"]
    assert result["python"]["version"]
    assert result["pytest"]["available"] is True
    assert result["test_dir"] == "tests"
    assert result["ready"] is True
    assert result["collect_only"]["ran"] is False
    assert any("does not authenticate" in item for item in result["limitations"])


def test_task_config_checks_target_module_import(tmp_path):
    root = _project(tmp_path)
    (root / "src/pkg").mkdir(parents=True)
    (root / "src/pkg/__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    task = root / "task.json"
    task.write_text(json.dumps({
        "schema_version": 1,
        "repo": str(root),
        "issue_file": str(root / "issue.md"),
        "output_dir": str(root / "out"),
        "language": {
            "python": sys.executable,
            "target_modules": ["pkg"],
            "source_roots": ["src"],
            "candidate_parent": "tests",
            "pytest_args": ["-q"],
        },
    }), encoding="utf-8")
    (root / "issue.md").write_text("bug", encoding="utf-8")
    result = check_project(root, task_config=task)
    assert result["target_modules"] == [{"module": "pkg", "importable": True, "error": None}]
    assert result["ready"] is True


def test_missing_pytest_is_reported_not_raised(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.setattr(
        "scripts.project_check._pytest_check",
        lambda *args, **kwargs: {
            "available": False, "version": None, "error": "No module named pytest",
        },
    )
    result = check_project(root, python=sys.executable)
    assert result["pytest"]["available"] is False
    assert result["ready"] is False
    assert result["errors"]


def test_summary_lines_are_plain_text(tmp_path):
    root = _project(tmp_path)
    result = check_project(root, python=sys.executable)
    lines = summary_lines(result)
    assert any("Project:" in line for line in lines)
    assert any("Ready:" in line for line in lines)


def test_collect_only_counts_tests(tmp_path):
    root = _project(tmp_path)
    result = check_project(root, python=sys.executable, collect_only=True)
    assert result["collect_only"]["ran"] is True
    assert result["collect_only"]["exit_code"] == 0
    assert result["collect_only"]["collected"] >= 1
    assert result["ready"] is True


def test_collect_only_failure_is_not_ready(tmp_path, monkeypatch):
    root = _project(tmp_path)
    monkeypatch.setattr(
        "scripts.project_check._collect_only",
        lambda *args, **kwargs: {
            "ran": True, "exit_code": 2, "collected": None, "timed_out": False,
            "summary": "ERROR collecting tests",
        },
    )
    result = check_project(root, python=sys.executable, collect_only=True)
    assert result["collect_only"]["exit_code"] == 2
    assert result["ready"] is False


def test_target_env_removes_reproagent_secrets(monkeypatch):
    from scripts.project_check import _clean_env

    monkeypatch.setenv("DEEPSEEK_API_KEY", "canary")
    monkeypatch.setenv("REPROAGENT_API_KEY", "canary-2")
    monkeypatch.setenv("PYTHONPATH", "wrong-checkout")
    monkeypatch.setenv("PYTHONHOME", "wrong-python")
    env = _clean_env()
    assert "DEEPSEEK_API_KEY" not in env
    assert "REPROAGENT_API_KEY" not in env
    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env


def test_no_target_writes_for_light_check(tmp_path):
    root = _project(tmp_path)
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    check_project(root, python=sys.executable)
    after = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before
    assert not (root / ".pytest_cache").exists()
