"""Deep workspace paths end to end, plus the boundary and interpreter regressions.

The recorded SWT round could not snapshot six Sphinx cases on Windows with the
output path the operator supplied: the frozen copy, the per-run copy and the
probe files nest deeply enough that individual paths cross the legacy 260
character limit, and the tool failed at atomic-write time.  These tests hand the
tool ordinary path arguments -- no registry change, no ``\\?\\`` prefix written by
the caller -- and check the snapshot, execution, protection check, export and the
standalone replay.  ``MAX_PATH`` is the Windows limit itself, not a ReproAgent
constant.

Windows reserves twelve characters of ``MAX_PATH`` for the name that will live
inside a directory, so a directory path stops working at 248 while a file path
keeps working up to 260.  The deep fixtures here stay inside the directory limit
by construction and let file paths cross the file limit, which is how the
recorded failure presented itself.
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from reproagent.adapters.languages.python_pytest.adapter import absolute_python
from reproagent.adapters.languages.python_pytest.collector import read_probe
from reproagent.app import create_controller
from reproagent.core.models import (CandidateDraft, DraftFile, EvidenceLevel, IssueContract, ModelConfig,
                                    ProjectView, PythonPytestConfig, TaskRequest, TaskResult, TaskState)
from reproagent.core.tools import Tools
from reproagent.exporter import Exporter
from reproagent.paths import workspace_path
from reproagent.runner import Runner
from reproagent.store import TaskStore, atomic_write, safe_child
from reproagent.workspace import Workspace, inventory
from tests.unit.test_controller import PhasePlan, ScriptedModel, ask

MAX_PATH = 260
WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="the long-path branch is Windows-only")
LONG_NAME = "long_" + "n" * 40 + ".py"          # a source file the repository itself carries
# Deep enough that the repository files the tool copies cross the file limit,
# while every directory the tool builds stays inside the twelve-char reserve.
DEEP = MAX_PATH - 100
# The snapshot root is the task directory plus "/snapshots/snapshot-<32 hex>/code",
# so this length puts the snapshot root exactly at the limit while the task root,
# and everything that only reads the task root, is still below it.
BAND = MAX_PATH - 57


def deep_directory(base: Path, minimum_length: int) -> Path:
    """An existing directory at least ``minimum_length`` characters deep.

    Windows needs the length to reach the file limit; every other platform keeps
    the plain base path, where that length means nothing.
    """
    root = base
    if os.name == "nt":
        while len(str(root)) < minimum_length:
            step = min(minimum_length - len(str(root)) - 1, 100)
            if step <= 0:
                break
            root = root / ("d" * step)
    root.mkdir(parents=True, exist_ok=True)
    return root


def readable(path: Path) -> Path:
    """The same path in the form this test process needs to read it back."""
    text = str(path)
    if os.name == "nt" and len(text) >= MAX_PATH and not text.startswith("\\\\?\\"):
        return Path("\\\\?\\" + text)
    return path


def atomic_temporary(path: Path) -> Path:
    """Where ``store.atomic_write`` stages the bytes it renames over ``path``."""
    return path.with_name(f'.{path.name}.{"0" * 32}.tmp')


def build(tmp_path, projects, facts):
    """A frozen snapshot and published candidate inside a deep task directory."""
    repo = projects.plain(tmp_path / "repo")
    (repo / LONG_NAME).write_text("LONG = 1\n", encoding="utf-8")
    output = deep_directory(tmp_path / "out", DEEP)
    request = TaskRequest(repo, output, repo / "issue.md",
                          language=PythonPytestConfig(python=sys.executable, source_roots=(".",),
                                                      target_modules=("example.parser",)))
    store = TaskStore(output)
    workspace = Workspace(output, store)
    context = facts.context()
    snapshot = workspace.freeze(request, context)
    candidate = workspace.publish(CandidateDraft(
        (DraftFile("tests/test_repro.py", b"from example.parser import parse\ndef test_empty(): assert parse([]) == []\n"),),
        snapshot.snapshot_id, "contract-1", 1, "empty input"), snapshot)
    return request, store, workspace, snapshot, candidate, context


@WINDOWS_ONLY
def test_deep_delivery_survives_paths_past_the_legacy_limit(tmp_path, projects, facts):
    request, store, workspace, snapshot, candidate, context = build(tmp_path, projects, facts)
    longest = max((snapshot.root / entry.path for entry in snapshot.files), key=lambda path: len(str(path)))
    # The frozen copy crosses the limit, and so does the temporary the tool
    # renames over it: the recorded failures stopped inside atomic_write.
    assert len(str(longest)) > MAX_PATH
    assert len(str(atomic_temporary(longest))) > MAX_PATH

    runner = Runner(workspace, store)
    environment = asyncio.run(runner.prepare(request, snapshot, context))
    execution = asyncio.run(runner.execute(candidate, snapshot, environment, context))
    assert execution.observation.probe_complete and execution.raw.exit_code == 1
    assert execution.protection.ok
    assert execution.observation.framework_details["target_calls"]["example.parser"] > 0

    store.save_record("contracts", "contract-1", IssueContract("contract-1", expected="parse([]) returns []",
                                                               reported_actual="IndexError", trigger="empty list"))
    result = TaskResult("task", TaskState.EXPORTING, evidence_level=EvidenceLevel.REPEATED_OBSERVATION,
                        accepted_candidate_id=candidate.candidate_id)
    manifest = Exporter().export(result, store)
    report = (manifest.root / "report.json").read_text(encoding="utf-8")
    # The platform prefix is an internal detail: it must not reach the package a
    # reader opens, and the task directory is still redacted to <task>.
    assert "\\\\?\\" not in report and str(store.root) not in report

    fresh = projects.plain(tmp_path / "fresh")
    # The replay tool refuses an output directory that already exists, so only
    # its parent is built: deep, but never created by the test.
    replay_output = deep_directory(tmp_path / "replay", MAX_PATH - 40) / "logs"
    replay = subprocess.run([sys.executable, "replay.py", "--repo", str(fresh), "--python", sys.executable,
                             "--output", str(replay_output), "--install"],
                            cwd=manifest.root, capture_output=True, timeout=120)
    assert replay.returncode == 1, replay.stderr.decode(errors="replace")
    assert (fresh / "tests/test_repro.py").read_bytes() == (candidate.storage_root / "tests/test_repro.py").read_bytes()
    assert read_probe(readable(replay_output / "probe.jsonl"), "export-replay").probe_complete


def band_workspace(tmp_path, projects):
    """A task directory in the band below the limit: snapshot root long, task root not.

    Execution cannot reach this band -- the run copy's own directory would be
    ``task + 47`` characters, past the twelve-character directory reserve, and its
    files are what pytest would have to open -- so the checks here stay above it.
    """
    repo = projects.plain(tmp_path / "repo")
    output = deep_directory(tmp_path / "out", BAND)
    assert len(str(workspace_path(output))) < MAX_PATH
    assert str(workspace_path(output / "snapshots" / ("snapshot-" + "0" * 32) / "code")).startswith("\\\\?\\")
    request = TaskRequest(repo, output, repo / "issue.md",
                          language=PythonPytestConfig(python=sys.executable, target_modules=("example.parser",)))
    store = TaskStore(output)
    return request, store, Workspace(output, store)


@WINDOWS_ONLY
def test_a_snapshot_root_past_the_limit_does_not_abort_the_run(tmp_path, projects, facts):
    """The run reaches the model's own end state instead of an internal error.

    The package itself cannot be published at this depth -- the export build
    directory is ``task + 53`` characters, past the Windows directory limit but
    below the file limit -- so the run ends ``FAILED`` with an unhelpful
    ``export_state``.  What is checked here is what the snapshot-path mapping
    decides: the task is analysed, one exploration phase runs to its own result,
    and the task stops for the phase's reason instead of aborting on the way in.
    """
    request, store, _ = band_workspace(tmp_path, projects)
    model = ScriptedModel(missing=True)
    controller = create_controller(request, ModelConfig(), gateway=model,
                                   explorer_factory=PhasePlan([ask("stop here")]))
    result = asyncio.run(controller.run(request, facts.context(limits=request.limits)))
    assert model.kinds == ["contract"]
    assert [context.contract.contract_id for context in controller.explorer.contexts]
    assert result.stop_reason == "MISSING_INFORMATION"


@WINDOWS_ONLY
def test_deep_tool_reads_stay_relative_to_the_workspace(tmp_path, projects, facts):
    request, store, workspace = band_workspace(tmp_path, projects)
    context = facts.context()
    snapshot = workspace.freeze(request, context)
    tools = Tools(ProjectView(snapshot), workspace, context)
    read = tools.read_file("example/parser.py", 1, 2)
    assert [ref.path for ref in read.evidence_refs] == [f"snapshots/{snapshot.snapshot_id}/code/example/parser.py"]
    assert "def parse" in read.text
    assert tools.search_code("def parse", "snapshot").text.startswith("example/parser.py:1:")


def test_boundary_check_is_not_relaxed_by_the_path_form(tmp_path):
    store = TaskStore(deep_directory(tmp_path / "out", DEEP))
    for relative in ("../outside.py", "C:/outside.py", "tests/../escape.py", "tests\\escape.py",
                     "\\\\?\\C:\\outside.py", "./escape.py"):
        with pytest.raises(ValueError):
            safe_child(store.root, relative)
    assert safe_child(store.root, "tests/test_repro.py") == store.root / "tests" / "test_repro.py"


def test_a_link_that_escapes_a_deep_workspace_is_still_refused(tmp_path):
    root = deep_directory(tmp_path / "out", DEEP)
    run_root = root / "runs" / ("run-" + "0" * 32) / "code"
    run_root.mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    try:
        (run_root / "linked.txt").symlink_to(outside)
    except OSError:
        pytest.skip("link creation needs privilege on Windows; traversal is checked above")
    with pytest.raises(ValueError, match="outside|escape"):
        inventory(run_root)


@WINDOWS_ONLY
def test_unicode_names_survive_the_long_path_form(tmp_path):
    root = deep_directory(tmp_path / "out", MAX_PATH - 60)
    directory = root / ("文" * 46)
    target = directory / ("中文文件" * 5 + ".txt")
    assert len(str(directory)) < MAX_PATH - 12 < len(str(target))
    atomic_write(target, "内容".encode("utf-8"))
    assert readable(target).read_bytes() == "内容".encode("utf-8")
    assert "中文文件" * 5 + ".txt" in [entry.name for entry in directory.iterdir()]


def test_a_venv_interpreter_runs_its_own_pytest_through_its_link():
    """A venv interpreter must be run through its link, not its target.

    On Linux a venv's ``bin/python`` is a symlink to the base interpreter, which
    has none of the venv's packages; resolving it drops the venv.  This skips
    where the running interpreter is not reached through a link (Windows copies
    ``Scripts/python.exe``), and the ubuntu CI leg runs the suite from ``.venv``.
    """
    interpreter = Path(sys.executable)
    target = Path(os.path.realpath(interpreter))
    if interpreter == target:
        pytest.skip("this interpreter is not reached through a link")
    assert Path(absolute_python(str(interpreter))) == interpreter
    probe = subprocess.run([absolute_python(str(interpreter)), "-c",
                            "import sys, pytest; print(sys.prefix); print(sys.base_prefix); print(pytest.__file__)"],
                           capture_output=True, text=True, timeout=120)
    assert probe.returncode == 0, probe.stderr
    prefix, base_prefix, pytest_file = probe.stdout.splitlines()
    assert Path(prefix) != Path(base_prefix)
    assert Path(pytest_file).is_relative_to(Path(prefix))
