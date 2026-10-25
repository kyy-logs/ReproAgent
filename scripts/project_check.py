"""Read-only target project diagnostics for the Windows one-click installer.

This module uses only the standard library and never imports ReproAgent.  It runs
the target project's own interpreter in subprocesses; it does not install or
modify anything in the target project.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

SCHEMA_VERSION = 1
MIN_PYTHON = (3, 11)
DEFAULT_TIMEOUT = 30
COLLECT_TIMEOUT = 120
REMOVED_ENV = ("DEEPSEEK_API_KEY", "REPROAGENT_API_KEY", "PYTHONPATH", "PYTHONHOME")
_COLLECTED = re.compile(r"(\d+)\s+tests?\s+collected", re.IGNORECASE)


def _requires_python_ok(requires, version):
    """Best-effort check for a simple `>=x.y` lower bound; report-only otherwise."""
    if not requires or not version:
        return True
    match = re.search(r">=\s*(\d+)\.(\d+)", requires)
    if not match:
        return True
    actual = tuple(int(part) for part in version.split(".")[:2])
    return actual >= (int(match.group(1)), int(match.group(2)))


def _clean_env() -> dict[str, str]:
    environment = dict(os.environ)
    for name in REMOVED_ENV:
        environment.pop(name, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _run(argv, cwd, env, timeout):
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            env=env,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "exit_code": None,
            "stdout": "",
            "stderr": "timed out",
            "timed_out": True,
        }
    except OSError as exc:
        return {"exit_code": None, "stdout": "", "stderr": str(exc), "timed_out": False}


def _probe(prefix, cwd, env):
    code = (
        "import json,sys; print(json.dumps({"
        "'executable':sys.executable,'version':sys.version.split()[0],"
        "'major':sys.version_info[0],'minor':sys.version_info[1]}))"
    )
    result = _run([*prefix, "-c", code], cwd, env, DEFAULT_TIMEOUT)
    if result["exit_code"] != 0:
        return None
    try:
        data = json.loads(result["stdout"].strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("version"), str):
        return None
    return data


def _load_task(task_config):
    if task_config is None:
        return None
    try:
        data = json.loads(Path(task_config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _candidates(project, python, task, task_config):
    if python is not None:
        return [([str(Path(python))], "explicit")], True
    configured = (task or {}).get("language", {}).get("python")
    if configured:
        path = Path(configured)
        if not path.is_absolute() and task_config is not None:
            path = Path(task_config).parent / path
        return [([str(path)], "task_config")], True
    found = []
    for name in (".venv", "venv"):
        path = project / name / "Scripts" / "python.exe"
        if path.is_file():
            found.append(([str(path)], f"project_{name}"))
    launcher = shutil.which("py.exe") or shutil.which("py")
    if launcher:
        found.append(([launcher, "-3"], "py_launcher"))
    for name in ("python.exe", "python3.exe"):
        path = shutil.which(name)
        if path:
            found.append(([path], "path"))
    return found, False


def _source_paths(project, task):
    paths = [str(project)]
    for root in (task or {}).get("language", {}).get("source_roots", []):
        candidate = project if root == "." else project / root
        if str(candidate) not in paths:
            paths.append(str(candidate))
    return paths


def _python_env(env, project, task):
    target = dict(env)
    target["PYTHONPATH"] = os.pathsep.join(
        [*_source_paths(project, task), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    return target


def _pytest_check(prefix, project, env):
    result = _run(
        [*prefix, "-c", "import pytest; print(pytest.__version__)"],
        project,
        env,
        DEFAULT_TIMEOUT,
    )
    if result["exit_code"] != 0:
        return {"available": False, "version": None,
                "error": result["stderr"].strip()[:1000]}
    return {
        "available": True,
        "version": result["stdout"].strip().splitlines()[-1],
        "error": None,
    }


def _module_checks(prefix, project, env, task):
    modules = (task or {}).get("language", {}).get("target_modules", [])
    if not modules:
        return []
    target_env = _python_env(env, project, task)
    checks = []
    for module in modules:
        result = _run(
            [
                *prefix,
                "-c",
                "import importlib,sys; importlib.import_module(sys.argv[1]); print('ok')",
                module,
            ],
            project,
            target_env,
            DEFAULT_TIMEOUT,
        )
        checks.append(
            {
                "module": module,
                "importable": result["exit_code"] == 0,
                "error": None if result["exit_code"] == 0
                else result["stderr"].strip()[:1000],
            }
        )
    return checks


def _test_dir(project, task):
    configured = (task or {}).get("language", {}).get("candidate_parent")
    candidates = [configured] if configured else ["tests", "testing", "test"]
    for name in candidates:
        if name and (project / name).is_dir():
            return name
    return None


def _collect_only(prefix, project, env, task):
    target_env = _python_env(env, project, task)
    target_env["PYTEST_ADDOPTS"] = ""
    result = _run(
        [*prefix, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        project,
        target_env,
        COLLECT_TIMEOUT,
    )
    text = result["stdout"] + "\n" + result["stderr"]
    match = _COLLECTED.search(text)
    collected = int(match.group(1)) if match else None
    summary = next(
        (line.strip() for line in reversed(text.splitlines()) if line.strip()), ""
    )
    return {
        "ran": True,
        "exit_code": result["exit_code"],
        "collected": collected,
        "timed_out": result["timed_out"],
        "summary": summary[:2000],
    }


def check_project(project, *, python=None, task_config=None, collect_only=False):
    project = Path(project).resolve()
    task_path = Path(task_config).resolve() if task_config is not None else None
    task = _load_task(task_path)
    result = {
        "schema_version": SCHEMA_VERSION,
        "project": str(project),
        "task_config": str(task_path) if task_path is not None else None,
        "python": {"path": None, "version": None, "source": None},
        "requires_python": None,
        "pytest": {"available": False, "version": None},
        "test_dir": None,
        "target_modules": [],
        "collect_only": {"ran": False, "exit_code": None, "collected": None},
        "ready": False,
        "warnings": [],
        "errors": [],
        "limitations": ["does not authenticate the model", "does not run tests"],
    }
    if not project.is_dir():
        result["errors"].append("project directory does not exist")
        return result
    if task_path is not None and task is None:
        result["errors"].append("task configuration is not a valid JSON object")
        return result
    pyproject = project / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib

            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            result["requires_python"] = data.get("project", {}).get("requires-python")
        except (OSError, ValueError):
            result["warnings"].append("pyproject.toml could not be parsed")
    env = _clean_env()
    candidates, explicit = _candidates(project, python, task, task_path)
    selected = None
    for prefix, source in candidates:
        info = _probe(prefix, project, env)
        if info and (info["major"], info["minor"]) >= MIN_PYTHON:
            selected = (prefix, info, source)
            break
        if explicit:
            result["errors"].append(
                "explicit target Python is missing or older than 3.11"
            )
            return result
    if selected is None:
        result["errors"].append("no Python 3.11+ found for the target project")
        return result
    prefix, info, source = selected
    result["python"] = {
        "path": info["executable"],
        "version": info["version"],
        "source": source,
    }
    requires_ok = _requires_python_ok(result["requires_python"], info["version"])
    result["pytest"] = _pytest_check(prefix, project, env)
    result["test_dir"] = _test_dir(project, task)
    result["target_modules"] = _module_checks(prefix, project, env, task)
    if collect_only:
        result["collect_only"] = _collect_only(prefix, project, env, task)
    modules_ok = all(item["importable"] for item in result["target_modules"])
    collect_ok = (not collect_only) or (
        result["collect_only"]["exit_code"] == 0
        and (result["collect_only"]["collected"] or 0) > 0
    )
    result["ready"] = bool(
        requires_ok
        and result["pytest"]["available"]
        and result["test_dir"] is not None
        and modules_ok
        and collect_ok
    )
    if not requires_ok:
        result["errors"].append("target Python does not satisfy requires-python")
    if not result["test_dir"]:
        result["errors"].append("no test directory found (tests/testing/test)")
    if not result["pytest"]["available"]:
        result["errors"].append("pytest is not importable")
    if collect_only and not collect_ok:
        result["errors"].append("pytest collection failed or collected no tests")
    return result


def summary_lines(result):
    python = result["python"]
    pytest_info = result["pytest"]
    collect = result["collect_only"]
    lines = [
        f"Project: {result['project']}",
        f"Python: {python['path'] or '<not found>'} "
        f"({python['version'] or '-'}) [{python['source'] or '-'}]",
        f"pytest: {pytest_info['version'] if pytest_info['available'] else '<not available>'}",
        f"Test directory: {result['test_dir'] or '<not found>'}",
    ]
    for item in result["target_modules"]:
        lines.append(
            f"Module {item['module']}: {'ok' if item['importable'] else 'FAILED'}"
        )
    if collect["ran"]:
        lines.append(
            f"CollectOnly: exit={collect['exit_code']} collected={collect['collected']}"
        )
    lines.append(f"Ready: {result['ready']}")
    for error in result["errors"]:
        lines.append(f"  ERROR: {error}")
    for warning in result["warnings"]:
        lines.append(f"  WARNING: {warning}")
    return lines
