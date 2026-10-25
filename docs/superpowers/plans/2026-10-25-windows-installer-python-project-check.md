# Windows 一键安装：Python 获取与目标项目检测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `install.cmd` 在全新 Windows 机器上先找现有 Python、找不到再自动安装，并在安装后用只读方式检查目标项目的 Python/pytest/模块/测试目录；全程不调用模型。

**Architecture:** PowerShell 负责在 Python 尚不存在时完成发现、winget/官方安装包兜底和签名校验；`install.py` 负责私有环境、现有 `setup` 集成、启动器检查，并调用新的 stdlib-only `scripts/project_check.py` 做目标项目只读诊断。不修改 `src/reproagent/**` 的业务逻辑，也不新增产品 CLI。

**Tech Stack:** Windows PowerShell 5.1、Python 3.11+、pytest、现有 `reproagent setup`、pytest 测试套件。

**Spec:** `docs/superpowers/specs/2026-10-25-windows-installer-python-project-check-design.md`

## Global Constraints

- **不修改** `src/reproagent/**`、`core/`、`adapters/`、`runner.py`、`verifier.py`、`workspace.py`、`exporter.py`、`store.py` 或任何业务逻辑。
- 只改 `install.cmd`（如需透传）、`scripts/install-windows.ps1`、`scripts/install.py`、新增 `scripts/project_check.py`、`tests/**`、`docs/windows-install.md` 和新增设计/计划/验收文档。
- 保留工作区所有既有未提交改动；不要 reset、checkout、stash 或覆盖别人的修改。
- 不提交、不 push、不合并；本计划用测试检查点代替 commit。用户明确要求提交时才提交。
- 安装、检查、目标检测都不调用模型；不打印、复制或保存密钥。
- 目标项目检查只读：不安装目标依赖，不修改目标源码；`-CollectOnly` 是显式授权执行目标 conftest/插件。
- 现有 21 项 Windows bootstrap 测试和现有凭据/安装测试不得回归。
- 自动安装 Python 只在找不到任何合格 Python 且未传 `-NoPythonInstall` 时发生；显式 `-Python` 失败时不自动降级。
- 官方安装包必须通过 Authenticode 校验（`Valid` 且签名主体含 `Python Software Foundation`）。
- 不修改 PATH；后续调用使用绝对路径。
- 测试只使用临时目录/临时 HOME，不触碰真实宿主入口、真实凭据或真实用户配置。

---

## File Structure

- `scripts/project_check.py`（新建）：目标项目只读诊断；只依赖标准库；纯函数 + 清晰的返回字典。
- `scripts/install.py`（修改）：新参数、参数前置校验、调用 `project_check`、启动器 `-Check`、报告写入。
- `scripts/install-windows.ps1`（修改）：Python 发现顺序、自动安装、参数透传。
- `install.cmd`（通常不改）：`%*` 已透传；只在需要调整帮助文本时改。
- `tests/unit/test_windows_project_check.py`（新建）：目标检查的单元测试。
- `tests/unit/test_windows_bootstrap.py`（修改）：新增 install 参数/启动器检查测试。
- `tests/integration/test_windows_bootstrap_entry.py`（修改）：CMD/PowerShell 参数透传、无 Python 失败路径。
- `tests/integration/test_windows_bootstrap_offline.py`（修改）：真实离线安装 + 目标检查。
- `docs/windows-install.md`（修改）：新参数、自动安装、检测语义、退出码。
- `docs/reviews/2026-10-25-windows-installer-python-project-check-acceptance.md`（新建）：验收记录。

---

### Task 1: 目标项目只读诊断模块

**Files:**
- Create: `scripts/project_check.py`
- Test: `tests/unit/test_windows_project_check.py`

**Interfaces:**
- Produces:
  - `check_project(project: Path, *, python: str | Path | None = None, task_config: Path | None = None, collect_only: bool = False) -> dict`
  - `summary_lines(result: dict) -> list[str]`
  - `SCHEMA_VERSION = 1`
- The result dict has keys: `schema_version`, `project`, `task_config`, `python`
  (`path`, `version`, `source`), `requires_python`, `pytest` (`available`, `version`),
  `test_dir`, `target_modules` (list of `{module, importable, error}`),
  `collect_only` (`ran`, `exit_code`, `collected`), `ready`, `warnings`, `errors`,
  `limitations`.

- [ ] **Step 1: Write failing tests for discovery and basic checks**

Create `tests/unit/test_windows_project_check.py`:

```python
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_windows_project_check.py -q
```

Expected: `ModuleNotFoundError: No module named 'scripts.project_check'` or import error.

- [ ] **Step 3: Implement `scripts/project_check.py`**

Create the module with these exact public functions and helpers:

```python
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
KNOWN_SECRET_ENV = ("DEEPSEEK_API_KEY", "REPROAGENT_API_KEY")
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
    for name in KNOWN_SECRET_ENV:
        environment.pop(name, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _run(argv, cwd, env, timeout):
    try:
        completed = subprocess.run(
            argv, cwd=str(cwd), env=env, shell=False, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        return {"exit_code": completed.returncode, "stdout": completed.stdout,
                "stderr": completed.stderr, "timed_out": False}
    except subprocess.TimeoutExpired:
        return {"exit_code": None, "stdout": "", "stderr": "timed out", "timed_out": True}
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
    target["PYTHONPATH"] = os.pathsep.join([*_source_paths(project, task), env.get("PYTHONPATH", "")]).strip(os.pathsep)
    return target


def _pytest_check(prefix, project, env):
    result = _run([*prefix, "-c", "import pytest; print(pytest.__version__)"],
                  project, env, DEFAULT_TIMEOUT)
    if result["exit_code"] != 0:
        return {"available": False, "version": None, "error": result["stderr"].strip()}
    return {"available": True, "version": result["stdout"].strip().splitlines()[-1], "error": None}


def _module_checks(prefix, project, env, task):
    modules = (task or {}).get("language", {}).get("target_modules", [])
    if not modules:
        return []
    target_env = _python_env(env, project, task)
    checks = []
    for module in modules:
        result = _run(
            [*prefix, "-c", "import importlib,sys; importlib.import_module(sys.argv[1]); print('ok')", module],
            project, target_env, DEFAULT_TIMEOUT,
        )
        checks.append({"module": module, "importable": result["exit_code"] == 0,
                       "error": None if result["exit_code"] == 0 else result["stderr"].strip()})
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
        project, target_env, COLLECT_TIMEOUT,
    )
    text = result["stdout"] + "\n" + result["stderr"]
    match = _COLLECTED.search(text)
    collected = int(match.group(1)) if match else None
    summary = next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")
    return {"ran": True, "exit_code": result["exit_code"], "collected": collected,
            "timed_out": result["timed_out"], "summary": summary[:2000]}


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
            result["errors"].append("explicit target Python is missing or older than 3.11")
            return result
    if selected is None:
        result["errors"].append("no Python 3.11+ found for the target project")
        return result
    prefix, info, source = selected
    result["python"] = {"path": info["executable"], "version": info["version"], "source": source}
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
        requires_ok and result["pytest"]["available"]
        and result["test_dir"] is not None and modules_ok and collect_ok
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
        f"Python: {python['path'] or '<not found>'} ({python['version'] or '-'}) [{python['source'] or '-'}]",
        f"pytest: {pytest_info['version'] if pytest_info['available'] else '<not available>'}",
        f"Test directory: {result['test_dir'] or '<not found>'}",
    ]
    for item in result["target_modules"]:
        lines.append(f"Module {item['module']}: {'ok' if item['importable'] else 'FAILED'}")
    if collect["ran"]:
        lines.append(f"CollectOnly: exit={collect['exit_code']} collected={collect['collected']}")
    lines.append(f"Ready: {result['ready']}")
    for error in result["errors"]:
        lines.append(f"  ERROR: {error}")
    for warning in result["warnings"]:
        lines.append(f"  WARNING: {warning}")
    return lines
```

- [ ] **Step 4: Run the tests and verify they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_windows_project_check.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Add collect-only and security tests**

Append tests to `tests/unit/test_windows_project_check.py`:

```python
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
    env = _clean_env()
    assert "DEEPSEEK_API_KEY" not in env
    assert "REPROAGENT_API_KEY" not in env


def test_no_target_writes_for_light_check(tmp_path):
    root = _project(tmp_path)
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    check_project(root, python=sys.executable)
    after = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before
    assert not (root / ".pytest_cache").exists()
```

- [ ] **Step 6: Run the full new test file**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_windows_project_check.py -q -rs
```

Expected: all pass; no target writes; no secrets in env.

---

### Task 2: `install.py` 编排与启动器检查

**Files:**
- Modify: `scripts/install.py`
- Test: `tests/unit/test_windows_bootstrap.py`
- Test: `tests/integration/test_windows_bootstrap_offline.py`

**Interfaces:**
- Consumes: `project_check.check_project`, `project_check.summary_lines`.
- Produces: `install(..., project=None, project_python=None, task_config=None, collect_only=False)`.
- New argparse flags: `--project`, `--project-python`, `--task-config`, `--collect-only`.
- Writes report: `<root>/.local/project-check.json`.
- Launcher check: run each selected host’s `scripts/reproagent.ps1 -Check`; no model.

- [ ] **Step 1: Add failing unit tests for argument validation and orchestration**

Append to `tests/unit/test_windows_bootstrap.py`:

```python
def test_collect_only_without_project_is_rejected(bootstrap, checkout, commands):
    calls, _ = commands
    assert bootstrap.install(checkout, collect_only=True) != 0
    assert not calls


def test_missing_project_is_rejected_before_lock(bootstrap, checkout, commands):
    calls, _ = commands
    assert bootstrap.install(checkout, project=checkout / "missing") != 0
    assert not calls


def test_project_check_is_called_and_report_written(bootstrap, checkout, commands, monkeypatch, capsys):
    calls, _ = commands
    report = {"ready": True, "python": {"path": "x", "version": "3.12", "source": "explicit"},
              "pytest": {"available": True, "version": "8"}, "test_dir": "tests",
              "target_modules": [], "collect_only": {"ran": False}, "errors": [], "warnings": []}
    seen = {}
    monkeypatch.setattr(bootstrap.project_check, "check_project",
                        lambda *a, **k: (seen.update({"args": a, "kwargs": k}) or report))
    monkeypatch.setattr(bootstrap.project_check, "summary_lines", lambda result: ["Project: ok"])
    target = checkout / "target"
    target.mkdir()
    assert bootstrap.install(checkout, project=target) == 0
    assert seen["args"][0] == target.resolve()
    assert (checkout / ".local/project-check.json").is_file()
    assert "Project: ok" in capsys.readouterr().out
```

- [ ] **Step 2: Run the new tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_windows_bootstrap.py -q
```

Expected: failures for unexpected keyword `project` / missing `project_check`.

- [ ] **Step 3: Implement `install.py` changes**

At the top, after imports:

```python
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import project_check
```

Add argument validation:

```python
def _validate_project_args(project, project_python, task_config, collect_only):
    if collect_only and project is None:
        raise RuntimeError('--collect-only requires --project.')
    if project is None:
        return None, None, None
    project = Path(project).resolve()
    if not project.is_dir():
        raise RuntimeError('The target project directory does not exist.')
    if project_python is not None:
        project_python = Path(project_python).resolve()
        if not project_python.is_file():
            raise RuntimeError('The target project Python does not exist.')
    if task_config is not None:
        task_config = Path(task_config).resolve()
        if not task_config.is_file():
            raise RuntimeError('The task configuration does not exist.')
        try:
            json.loads(task_config.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise RuntimeError('The task configuration is not valid JSON.')
    return project, project_python, task_config
```

Add launcher check:

```python
def _launcher_check(status, host, root):
    selected = ('claude-code', 'codex') if host == 'all' else (host,)
    for name in selected:
        info = (status.get('hosts') or {}).get(name)
        if not info or not info.get('root'):
            print(f'[ACTION] Launcher check skipped: {name} has no installed root.')
            continue
        script = Path(info['root']) / 'scripts' / 'reproagent.ps1'
        if not script.is_file():
            print(f'[ACTION] Launcher check skipped: {script} is missing.')
            continue
        powershell = shutil.which('powershell.exe')
        if not powershell:
            print('[ACTION] Launcher check skipped: powershell.exe not found.')
            continue
        result = _call([powershell, '-NoProfile', '-File', str(script), '-Check'], root, capture=True)
        try:
            payload = json.loads(result.stdout)
        except (ValueError, TypeError):
            payload = {}
        if result.returncode == 0 and payload.get('ready') is True:
            print(f'[OK] Launcher check passed: {name}')
        else:
            print(f'[ACTION] Launcher check failed: {name}')
```

Extend `install()`:

```python
def install(root: Path, *, host: str = 'all', model_config: Path | None = None,
            force: bool = False, project: Path | None = None,
            project_python: Path | None = None, task_config: Path | None = None,
            collect_only: bool = False) -> int:
```

After `root = Path(root).resolve()` and before the lock:

```python
    project, project_python, task_config = _validate_project_args(
        project, project_python, task_config, collect_only)
```

After the `status = _setup(command, root, check=True)` and the existing Ready/ACTION block:

```python
        print('[check] Launcher binding check...', flush=True)
        _launcher_check(status, host, root)
        if project is not None:
            print('[check] Target project environment...', flush=True)
            result = project_check.check_project(
                project, python=project_python, task_config=task_config,
                collect_only=collect_only)
            report = root / '.local' / 'project-check.json'
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                              encoding='utf-8')
            for line in project_check.summary_lines(result):
                print(line)
            print(f'[{"OK" if result.get("ready") else "ACTION"}] Project check report: {report}')
```

Add `import shutil` at top and `shutil.which` use. The existing `install.py` imports
`subprocess`, not `shutil`; add it. Wrap the `project_check.check_project` call in
`try/except (OSError, ValueError, RuntimeError)`: print `[ACTION] Project check failed`
and keep the tool-install exit code 0; a target-check crash must not roll back or fail an
otherwise successful installation.

Extend argparse:

```python
    parser.add_argument('--project', type=Path)
    parser.add_argument('--project-python', type=Path)
    parser.add_argument('--task-config', type=Path)
    parser.add_argument('--collect-only', action='store_true')
```

Pass to `install(...)`.

- [ ] **Step 4: Run unit tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_windows_bootstrap.py tests/unit/test_windows_project_check.py -q
```

Expected: pass.

- [ ] **Step 5: Update the offline integration test to exercise the project check**

In `tests/integration/test_windows_bootstrap_offline.py`, after the host entries are created, create a target project and a task config, then run the installer with `--project` and `--project-python` (use `sys.executable`, which has pytest in the test environment):

```python
    target = tmp_path / "target"
    (target / "tests").mkdir(parents=True)
    (target / "tests/test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (target / "issue.md").write_text("bug", encoding="utf-8")
    task = target / "task.json"
    task.write_text(json.dumps({
        "schema_version": 1, "repo": str(target),
        "issue_file": str(target / "issue.md"),
        "output_dir": str(target / "out"),
        "language": {"python": sys.executable, "target_modules": [],
                     "source_roots": [], "candidate_parent": "tests",
                     "pytest_args": ["-q"]},
    }), encoding="utf-8")
    with_project = subprocess.run(
        [sys.executable, str(root / "scripts/install.py"), "--project", str(target),
         "--project-python", sys.executable, "--task-config", str(task), "--collect-only"],
        cwd=tmp_path, env=environment, capture_output=True, timeout=150)
    assert with_project.returncode == 0, with_project.stdout + with_project.stderr
    assert (root / ".local/project-check.json").is_file()
    payload = json.loads((root / ".local/project-check.json").read_text(encoding="utf-8"))
    assert payload["ready"] is True
    assert payload["collect_only"]["collected"] >= 1
```

Note: `--collect-only` with the target `sys.executable` uses the test environment's pytest; no target dependency install. Keep this test inside the existing `test_offline_install` or add a second test using a reusable fixture. Do not rerun the full install twice; use the same installed environment.

- [ ] **Step 6: Run the integration test**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_windows_bootstrap_offline.py -q
```

Expected: pass; `project-check.json` ready; target files unchanged.

---

### Task 3: PowerShell Python 发现顺序

**Files:**
- Modify: `scripts/install-windows.ps1`
- Test: `tests/integration/test_windows_bootstrap_entry.py`

**Interfaces:**
- Produces PowerShell params: `-Project`, `-ProjectPython`, `-TaskConfig`, `-CollectOnly`, `-NoPythonInstall`.
- Discovery order: explicit → task config? (the task config is a target concern, not tool Python) → py → PATH → repo `.venv` → repo `venv` → `.local/install-env` → auto-install.
- Explicit `-Python` failure stops; no auto-fallback.
- `install.cmd` needs no change; `%*` already forwards.

- [ ] **Step 1: Add failing PowerShell forwarding tests**

In `tests/integration/test_windows_bootstrap_entry.py`, extend the fake `install.py` receipt test:

```python
def test_batch_entry_forwards_project_options(tmp_path):
    root, receipt, environment = _fixture(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    task = tmp_path / "task.json"
    task.write_text("{}", encoding="utf-8")
    python = sys.executable
    result = _run(
        root, environment,
        f'-Python "{python}" -Project "{target}" -ProjectPython "{python}" '
        f'-TaskConfig "{task}" -CollectOnly -NoPythonInstall')
    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["argv"] == [
        "--host", "all",
        "--project", str(target),
        "--project-python", str(python),
        "--task-config", str(task),
        "--collect-only",
    ]
```

- [ ] **Step 2: Run it and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_windows_bootstrap_entry.py::test_batch_entry_forwards_project_options -q
```

Expected: fail because the new params are not defined.

- [ ] **Step 3: Add PowerShell params and forwarding**

In `scripts/install-windows.ps1`, extend the `param(...)` block with:

```powershell
    [string]$Project,
    [string]$ProjectPython,
    [string]$TaskConfig,
    [switch]$CollectOnly,
    [switch]$NoPythonInstall,
```

After the existing install args:

```powershell
        if ($Project) { $installArgs += @('--project', $Project) }
        if ($ProjectPython) { $installArgs += @('--project-python', $ProjectPython) }
        if ($TaskConfig) { $installArgs += @('--task-config', $TaskConfig) }
        if ($CollectOnly) { $installArgs += '--collect-only' }
```

- [ ] **Step 4: Extend Python candidate discovery**

Replace the current candidate block with a helper that preserves the existing probe:

```powershell
function Test-PythonCandidate([string]$exe, [string[]]$prefixArgs = @()) {
    $probe = 'import json,sys; sys.exit(1) if sys.version_info < (3,11) else print(json.dumps(sys.executable,ensure_ascii=True))'
    $probeArgs = @($prefixArgs) + @('-c', $probe)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $found = & $exe @probeArgs 2>$null } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0 -or -not $found) { return $null }
    try { $decoded = $found | ConvertFrom-Json } catch { return $null }
    if ($decoded -is [string] -and (Test-Path -LiteralPath $decoded -PathType Leaf)) { return $decoded }
    return $null
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$candidates = @()
if ($Python) {
    $candidates += [pscustomobject]@{ Exe = $Python; Args = @() }
} else {
    $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($launcher) {
        $candidates += [pscustomobject]@{ Exe = $launcher.Source; Args = @('-3') }
    }
    foreach ($name in @('python.exe', 'python3.exe')) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
        if ($command -and $command.Source -notlike '*\Microsoft\WindowsApps\*') {
            $candidates += [pscustomobject]@{ Exe = $command.Source; Args = @() }
        }
    }
    foreach ($relative in @('.venv\Scripts\python.exe', 'venv\Scripts\python.exe',
                            '.local\install-env\Scripts\python.exe')) {
        $path = Join-Path $repoRoot $relative
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $candidates += [pscustomobject]@{ Exe = $path; Args = @() }
        }
    }
}
$selected = $null
foreach ($candidate in $candidates) {
    $selected = Test-PythonCandidate $candidate.Exe $candidate.Args
    if ($selected) { break }
}
```

Keep the existing “Python not found” message for the no-candidate case. The
`[pscustomobject]` entries avoid PowerShell nested-array flattening; use
`$candidate.Exe` and `$candidate.Args` consistently.

- [ ] **Step 5: Run entry tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_windows_bootstrap_entry.py -q
```

Expected: pass. Also test `.venv` discovery by running the installer without `-Python` in a temp repo whose `.venv` is a junction to the real `.venv`; skip when junction creation is unavailable, following the repo’s existing symlink-skip pattern.

---

### Task 4: Python 自动安装与签名校验

**Files:**
- Modify: `scripts/install-windows.ps1`
- Test: `tests/integration/test_windows_bootstrap_entry.py`

**Interfaces:**
- Adds `-NoPythonInstall` behavior.
- Winget command and official installer fallback; no PATH changes.
- Test seams (internal, not user docs): `REPROAGENT_TEST_MODE=1`,
  `REPROAGENT_TEST_WINGET=<cmd>`, `REPROAGENT_TEST_PYTHON_AFTER_INSTALL=<exe>`,
  `REPROAGENT_TEST_INSTALLER_PATH=<exe>`.

- [ ] **Step 1: Add failing no-Python tests**

Add to `tests/integration/test_windows_bootstrap_entry.py`:

```python
def test_no_python_with_no_install_fails_without_host_changes(tmp_path):
    root, receipt, environment = _fixture(tmp_path)
    environment = dict(environment)
    environment["PATH"] = r"C:\Windows\System32;C:\Windows;C:\Windows\System32\WindowsPowerShell\v1.0"
    result = _run(root, environment, "-NoPythonInstall")
    assert result.returncode == 2
    assert b'Python 3.11+' in result.stdout
    assert not receipt.exists()


def test_fake_winget_path_is_used(tmp_path):
    root, receipt, environment = _fixture(tmp_path)
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    winget = fake_bin / "winget.cmd"
    winget.write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")
    environment = dict(environment)
    environment["PATH"] = (str(fake_bin) + ";C:\\Windows\\System32;C:\\Windows;"
                           r"C:\Windows\System32\WindowsPowerShell\v1.0")
    environment["REPROAGENT_TEST_MODE"] = "1"
    environment["REPROAGENT_TEST_WINGET"] = str(winget)
    environment["REPROAGENT_TEST_PYTHON_AFTER_INSTALL"] = sys.executable
    result = _run(root, environment, "")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert receipt.exists()
```

The first test relies on the temp repo having no `.venv` and a sanitized PATH; ensure `_fixture` creates no `.venv`. The second test uses the test seam to avoid a real download.

- [ ] **Step 2: Run and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_windows_bootstrap_entry.py -q
```

Expected: the new tests fail until auto-install exists.

- [ ] **Step 3: Implement auto-install**

Add functions:

```powershell
$pythonVersion = '3.12.14'
$pythonInstallerUrl = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-amd64.exe"

function Find-InstalledPython {
    $roots = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python312\python.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Python312\python.exe')
    )
    foreach ($path in $roots) {
        if ($path -and (Test-Path -LiteralPath $path -PathType Leaf)) {
            $found = Test-PythonCandidate $path @()
            if ($found) { return $found }
        }
    }
    $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($launcher) { return Test-PythonCandidate $launcher.Source @('-3') }
    return $null
}

function Install-PythonWithWinget {
    $winget = $env:REPROAGENT_TEST_WINGET
    if (-not $winget) {
        $command = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue
        if (-not $command) { return $false }
        $winget = $command.Source
    }
    & $winget install --id Python.Python.3.12 -e --source winget --silent `
        --disable-interactivity --accept-package-agreements --accept-source-agreements
    return ($LASTEXITCODE -eq 0)
}

function Install-PythonOfficial {
    $installer = $env:REPROAGENT_TEST_INSTALLER_PATH
    if (-not $installer) {
        $installer = Join-Path $env:TEMP "reproagent-python-$pythonVersion-amd64.exe"
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -Uri $pythonInstallerUrl -OutFile $installer
        } catch { return $false }
    }
    try {
        $signature = Get-AuthenticodeSignature -LiteralPath $installer
        if ($null -eq $signature.SignerCertificate -or $signature.Status -ne 'Valid' -or
            $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
            return $false
        }
        $process = Start-Process -FilePath $installer -Wait -PassThru -ArgumentList @(
            '/quiet', 'InstallAllUsers=0', 'PrependPath=0', 'Include_pip=1', 'Include_launcher=1')
        return ($process.ExitCode -eq 0 -or $process.ExitCode -eq 3010)
    } finally {
        if (-not $env:REPROAGENT_TEST_INSTALLER_PATH -and (Test-Path -LiteralPath $installer)) {
            Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
        }
    }
}
```

After the existing candidate loop and the explicit-`-Python` failure check:

```powershell
$explicitFailed = $false
if ($Python -and -not $selected) {
    Write-Host '[ERROR] The Python given with -Python is missing or older than 3.11.'
    $explicitFailed = $true
    $installExit = 2
}
if (-not $selected -and -not $explicitFailed -and -not $NoPythonInstall) {
    Write-Host '[1/5] Python 3.11+ not found; trying automatic installation...'
    $installed = Install-PythonWithWinget
    if (-not $installed) { $installed = Install-PythonOfficial }
    if ($installed) {
        if ($env:REPROAGENT_TEST_MODE -eq '1' -and $env:REPROAGENT_TEST_PYTHON_AFTER_INSTALL) {
            $selected = $env:REPROAGENT_TEST_PYTHON_AFTER_INSTALL
        } else {
            $selected = Find-InstalledPython
        }
    }
}
if (-not $selected -and -not $explicitFailed) {
    if ($NoPythonInstall) {
        Write-Host '[ERROR] Python 3.11+ was not found and -NoPythonInstall was set.'
    } else {
        Write-Host '[ERROR] Python 3.11+ was not found and automatic installation failed.'
    }
    Write-Host 'Install Python from https://www.python.org/downloads/windows/ and retry.'
    $installExit = 2
}
```

Replace the existing `if (-not $selected) { ... } else { ... install ... }` with the
auto-install block followed by these error checks; only enter the install branch when
`$selected` is non-null. Keep the manual instructions. An explicit `-Python` failure sets
`$explicitFailed` and must not trigger auto-install.

- [ ] **Step 4: Run the auto-install tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_windows_bootstrap_entry.py -q
```

Expected: no-Python + `-NoPythonInstall` exits 2 with no receipt; fake winget path runs the fake install.py receipt.

- [ ] **Step 5: Add signature-rejection test**

Add a test that sets `REPROAGENT_TEST_MODE=1`, `REPROAGENT_TEST_INSTALLER_PATH` to an unsigned temp file, and a fake winget that exits 1; expect exit 2 and no receipt. This covers the refusal path without network.

- [ ] **Step 6: Run all Windows bootstrap tests**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/test_windows_bootstrap.py `
  tests/unit/test_windows_project_check.py `
  tests/integration/test_windows_bootstrap_entry.py `
  tests/integration/test_windows_bootstrap_offline.py -q -rs
```

Expected: all pass; skips only for platform-specific conditions.

---

### Task 5: 文档、验收与回归

**Files:**
- Modify: `docs/windows-install.md`
- Create: `docs/reviews/2026-10-25-windows-installer-python-project-check-acceptance.md`
- Test: full targeted suite

- [ ] **Step 1: Update `docs/windows-install.md`**

Add sections:

- Python 获取顺序（explicit → py → PATH → repo `.venv`/`venv` →
  `.local/install-env` → auto-install）
- 自动安装（winget 优先、官方安装包兜底、签名校验、UAC/网络、`-NoPythonInstall`）
- `-Project` / `-ProjectPython` / `-TaskConfig` / `-CollectOnly` 示例
- 目标检查只读，但会导入目标代码；`-CollectOnly` 会执行 conftest/插件
- `Ready`（工具结构）、Project Ready、真实复现四者分开
- `.local/project-check.json` 位置和退出码

- [ ] **Step 2: Run the targeted regression**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/test_windows_bootstrap.py `
  tests/unit/test_windows_project_check.py `
  tests/integration/test_windows_bootstrap_entry.py `
  tests/integration/test_windows_bootstrap_offline.py -q -rs
```

Also run the existing credential tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_integration_credentials.py -q
```

- [ ] **Step 3: Manual acceptance on this machine**

1. Existing `.venv` but no Python on PATH: run `install.cmd` without `-Python`; confirm it uses
   the repo `.venv` and does not auto-install.
2. Target project with `.venv` + pytest + tests: `-Project` light check reports ready;
   `-CollectOnly` collects tests; target files/mtimes unchanged.
3. `-NoPythonInstall` with a sanitized PATH: exit 2, no host changes.
4. No model HTTP requests during install/check/project check.
5. No secret strings in console output or `.local/project-check.json`.

- [ ] **Step 4: Write the acceptance record**

Create `docs/reviews/2026-10-25-windows-installer-python-project-check-acceptance.md` with:
- Commands, exit codes, test counts.
- Whether the `.venv` discovery, auto-install (real or simulated), target light/CollectOnly,
  and no-model/no-secret checks passed.
- Any environment deviations (UAC, network, winget unavailable).
- Explicit unverified items: real fresh-machine Python install if not executed; full-suite
  regression if not run; non-Windows.

- [ ] **Step 5: Final status check**

```powershell
git status --porcelain=v1
git diff --stat -- scripts install.cmd docs/windows-install.md tests
```

Confirm only the intended installer/test/docs files changed; no `src/reproagent/**` changes.
Do not commit unless the user asks.

---

## Self-Review

- **Spec coverage:** Python discovery/auto-install (Tasks 3–4), target project detection
  (Tasks 1–2), smoke checks (Task 2), docs/acceptance (Task 5), no model calls (global
  constraints), no business logic changes (global constraints).
- **Placeholder scan:** No TBD/TODO; code snippets define the public interfaces and
  result shape. The PowerShell snippets use concrete functions and flags; array/nesting
  details are called out for the implementer.
- **Type consistency:** `check_project(...) -> dict`; `summary_lines(result)`; `install(...,
  project, project_python, task_config, collect_only)`; PowerShell flags map to
  `--project`, `--project-python`, `--task-config`, `--collect-only`.
- **Known scope limit:** The official-installer success path is verified manually or with
  a test seam; tests cover refusal/no-Python/fake-winget paths without network. This is
  recorded in the acceptance doc.

## Execution Deviation

During Task 2/5 verification, the launcher `-Check` smoke failed on a Chinese temporary
checkout path. Manual reproduction showed that the generated `.ps1` was UTF-8 without a
BOM, so Windows PowerShell 5.1 decoded `$reproPinnedPython` as ANSI/mojibake. The
implemented fix is in `src/reproagent/integration/render.py`: `.ps1` files are encoded as
`utf-8-sig`; all other generated files remain UTF-8. This is an integration launcher
encoding fix, not a business-logic change. It was not in the original plan's file list;
the acceptance record documents the deviation and the 135-pass integration regression.
The next real `install.cmd -Force` will back up and update the old host wrappers.
