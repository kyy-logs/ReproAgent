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
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from reproagent.adapters.languages.python_pytest.adapter import absolute_python
from reproagent.adapters.languages.python_pytest.collector import read_probe
from reproagent.app import create_controller
from reproagent.core.budget import BudgetStopped
from reproagent.core.models import (CandidateDraft, DraftFile, EvidenceLevel, IssueContract, ModelConfig,
                                    ProjectView, PythonPytestConfig, TaskRequest, TaskResult, TaskState)
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
# A *directory* stops working twelve characters below the file limit, while a file keeps
# working to the limit itself.  ``workspace_path`` promotes at the file limit, because
# that is what a file needs, so this band -- where the probe directory (task + 41) and the
# run copy (task + 47) are both inside the reserved window -- is where every directory the
# tool creates has to be created through the form that reaches it.
RESERVED_BAND = MAX_PATH - 52
# The run copy is the task directory plus "/runs/run-<32 hex>/code" (47 characters),
# which no path form can reach past the directory a process can be started in.
UNRUNNABLE_BAND = MAX_PATH - 46


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


@WINDOWS_ONLY
@pytest.mark.parametrize('length', [208, 209, 210, 211])
def test_diagnostic_export_reads_existing_long_glob_children(tmp_path, length):
    from reproagent.core.serialization import bytes_hash
    output = deep_directory(tmp_path / 'export', length)
    assert len(str(output)) == length
    store = TaskStore(output)
    log_path = output / 'probes' / ('probe-' + 'a' * 32) / 'stdout.log'
    content = b'preflight evidence at a long path\n'
    atomic_write(log_path, content)
    assert readable(log_path).is_file()
    artifact = Exporter().export(TaskResult('review', TaskState.BLOCKED), store)
    report = json.loads(readable(artifact.root / 'report.json').read_text(encoding='utf-8'))
    mapping = next(item for item in report['log_mapping'] if item['source_path'].endswith('/stdout.log'))
    assert mapping['source_hash'] == bytes_hash(content)
    assert readable(artifact.root / mapping['export_path']).read_bytes() == content
    for entry in artifact.files:
        assert bytes_hash(readable(artifact.root / entry.path).read_bytes()) == entry.content_hash
    assert '\\\\?\\' not in json.dumps(report)


@WINDOWS_ONLY
@pytest.mark.parametrize('length', [248, 259])
def test_standalone_replay_creates_reserved_window_output_and_runs_pytest(tmp_path, projects, facts, length):
    from tests.integration.test_exporter import export_setup
    exporter, result, store, *_ = export_setup(tmp_path / 'package', projects, facts)
    package = exporter.export(result, store).root
    base = deep_directory(tmp_path / 'output-parent', 240)
    parent = base / ('p' * (length - 246))
    Path('\\\\?\\' + str(parent)).mkdir()
    output = parent / 'logs'
    assert len(str(output)) == length
    for label, factory, expected in [('buggy', projects.plain, 1), ('fixed', projects.fixed, 0)]:
        fresh = factory(tmp_path / label)
        run_output = output if label == 'buggy' else output.with_name('pass')
        run = subprocess.run([sys.executable, str(package / 'replay.py'), '--repo', str(fresh),
                              '--python', sys.executable, '--output', str(run_output), '--install'],
                             capture_output=True, timeout=60)
        assert run.returncode == expected, run.stderr.decode(errors='replace')
        assert read_probe(readable(run_output / 'probe.jsonl'), 'export-replay').probe_complete


@WINDOWS_ONLY
def test_standalone_replay_promotes_install_parent_before_directory_limit(tmp_path, projects, facts):
    from tests.integration.test_exporter import export_setup
    from reproagent.core.serialization import bytes_hash
    exporter, result, store, *_ = export_setup(tmp_path / 'package', projects, facts)
    package = exporter.export(result, store).root
    fresh = projects.plain(tmp_path / 'fresh')
    nested_parent = 'fixtures/' + 'd' * (249 - len(str(fresh)) - 10)
    install_path = nested_parent + '/s.txt'
    content = b'fixture\n'
    atomic_write(package / 'candidate' / install_path, content)
    report_path = package / 'report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    report['candidate_files'].append({'path': install_path, 'role': 'data'})
    report['pytest_args'] = [*report['pytest_args'], '--ignore=fixtures']
    atomic_write(report_path, json.dumps(report).encode())
    manifest_path = package / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    for entry in manifest['files']:
        if entry['path'] == 'report.json':
            entry['content_hash'] = bytes_hash(report_path.read_bytes())
    manifest['files'].append({'path': 'candidate/' + install_path, 'content_hash': bytes_hash(content)})
    atomic_write(manifest_path, json.dumps(manifest).encode())
    assert len(str(fresh / nested_parent)) == 249
    assert len(str(fresh / install_path)) < 260
    run = subprocess.run([sys.executable, str(package / 'replay.py'), '--repo', str(fresh), '--python', sys.executable,
                          '--output', str(tmp_path / 'replay'), '--install'], capture_output=True, timeout=60)
    assert run.returncode == 1, run.stderr.decode(errors='replace')
    assert readable(fresh / install_path).read_bytes() == content
    assert read_probe(tmp_path / 'replay/probe.jsonl', 'export-replay').probe_complete


@WINDOWS_ONLY
def test_standalone_replay_refuses_unstartable_cwd_before_install(tmp_path, projects, facts):
    from tests.integration.test_exporter import export_setup
    from reproagent.paths import directory_path
    exporter, result, store, *_ = export_setup(tmp_path / 'package', projects, facts)
    package = exporter.export(result, store).root
    deep = deep_directory(tmp_path / 'deep-parent', 240) / ('r' * 25)
    directory_path(deep).mkdir()
    run = subprocess.run([sys.executable, str(package / 'replay.py'), '--repo', str(deep), '--python', sys.executable,
                          '--output', str(tmp_path / 'output'), '--install'], capture_output=True, timeout=60)
    assert b'PROCESS_CWD_TOO_LONG' in run.stderr
    assert not readable(deep / 'tests/test_repro.py').exists()
    assert not (tmp_path / 'output').exists()


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


def band_workspace(tmp_path, projects, band=BAND):
    """A task directory in the band below the limit: snapshot root long, task root not.

    The default band is the shallowest one whose snapshot root crosses the limit, so
    the run copy's own directory (``task + 48``) still fits.  ``RUN_BAND`` asks for
    the deeper band where the run copy itself is past the limit, which is what
    execution has to survive.
    """
    repo = projects.plain(tmp_path / "repo")
    output = deep_directory(tmp_path / "out", band)
    assert len(str(workspace_path(output))) < MAX_PATH
    # The frozen copy is at least in the reserved window, which is what makes the band
    # interesting: a directory stops working twelve characters below the file limit.
    assert len(str(workspace_path(output / "snapshots" / ("snapshot-" + "0" * 32) / "code"))) >= MAX_PATH - 12
    request = TaskRequest(repo, output, repo / "issue.md",
                          language=PythonPytestConfig(python=sys.executable, target_modules=("example.parser",)))
    store = TaskStore(output)
    return request, store, Workspace(output, store)


def find_ripgrep():
    """The ripgrep the SDK's Grep runs, from the environment or PATH, or None."""
    candidates = (os.environ.get("REPROAGENT_RG_PATH"), shutil.which("rg"), shutil.which("rg.exe"))
    return next((path for path in candidates if path and Path(path).is_file()), None)


def glob_helper_path() -> str:
    """The bundled helper script the SDK's Glob resolves for a local backend."""
    import importlib.resources as resources

    return str(resources.files("agentscope.tool._builtin._scripts").joinpath("_glob_helper.py"))


def sdk_environment(tmp_path, projects, facts, rg=None):
    """Task 2's snapshot-bound SDK file tools over a frozen copy in the deep band.

    The import waits until here: this module also carries the boundary and venv
    regressions, which must run on a host where the SDK is not installed at all.
    """
    pytest.importorskip("agentscope")
    from reproagent.adapters.agentscope.evidence import EvidenceLedger
    from reproagent.adapters.agentscope.snapshot_backend import SnapshotBackend
    from reproagent.adapters.agentscope.tools import build_toolkit
    from reproagent.core.candidate_service import CandidateService
    from reproagent.core.phase import PhaseGate

    request, store, workspace = band_workspace(tmp_path, projects)
    context = facts.context()
    snapshot = workspace.freeze(request, context)
    project = ProjectView(snapshot)
    gate, service = PhaseGate(), CandidateService(project, workspace, context)
    backend = SnapshotBackend(project, store, context, rg_path=rg)
    ledger = EvidenceLedger(project, store)
    return SimpleNamespace(snapshot=snapshot, store=store, workspace=workspace, context=context, project=project,
                           backend=backend, ledger=ledger, rg=rg, gate=gate,
                           toolkit=build_toolkit(backend, ledger, service, gate, context))


async def _dispatch(env, name, payload, call_id="call-1"):
    from agentscope.message import ToolCallBlock
    from agentscope.state import AgentState

    response = None
    async for chunk in env.toolkit.call_tool(ToolCallBlock(id=call_id, name=name, input=json.dumps(payload)), AgentState()):
        response = chunk
    return response


def tool_call(env, name, payload):
    """One call through the real Toolkit, which is what the phase's model does."""
    return asyncio.run(_dispatch(env, name, payload))


def chunk_text(chunk) -> str:
    from agentscope.message import TextBlock

    return "".join(block.text for block in chunk.content if isinstance(block, TextBlock))


def sidecar_refs(text: str) -> list[dict]:
    """The evidence sidecar a file tool appended, or nothing when it cited no lines."""
    match = re.search(r"<evidence>(.*?)</evidence>", text, re.S)
    return [] if match is None else json.loads(match.group(1))["refs"]


def process_is_gone(pid: int) -> bool:
    """Whether Windows itself reports no running process with *pid*.

    ``Process.returncode`` is set by the same code under test, so the operating system
    is asked separately: a killed-and-reaped child must not be listed any more.
    """
    listed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                            capture_output=True, text=True, timeout=60)
    return not any(len(row) > 1 and row[1].strip() == str(pid) for row in csv.reader(listed.stdout.splitlines()))


@WINDOWS_ONLY
def test_directories_the_tool_builds_past_the_reserved_name_limit(tmp_path, projects, facts):
    """The directories the run needs are built where the plain form stops working.

    A *directory* stops working twelve characters below the file limit, because the name
    that will live inside it has to fit as well, while a file keeps working to the limit
    itself.  A task directory that is still below the file limit is therefore already too
    deep to ``mkdir`` through the form :func:`workspace_path` leaves ordinary, and the
    probe directory and the run copy both land in that window here.  Freezing the source,
    publishing the candidate, probing the environment and building the run copy all have
    to complete; whether the candidate can then be *executed* at this depth is the
    platform's own limit and is checked by the two bands either side of this one.
    """
    request, store, workspace = band_workspace(tmp_path, projects, band=RESERVED_BAND)
    context = facts.context()
    snapshot = workspace.freeze(request, context)
    candidate = workspace.publish(CandidateDraft(
        (DraftFile("tests/test_repro.py", b"from example.parser import parse\ndef test_empty(): assert parse([]) == []\n"),),
        snapshot.snapshot_id, "contract-1", 1, "empty input"), snapshot)

    # The frozen copy itself is past the file limit, and so are the files inside it --
    # including the temporary an atomic write stages next to them.
    longest = max((snapshot.root / entry.path for entry in snapshot.files), key=lambda path: len(str(path)))
    assert str(snapshot.root).startswith("\\\\?\\")
    assert len(str(longest)) > MAX_PATH and len(str(atomic_temporary(longest))) > MAX_PATH

    # Preparing the environment builds the probe directory and runs the probe in it, and
    # that directory -- like the run copy and the temporary beside it -- is in the
    # reserved window, where a plain ``mkdir`` no longer works.
    runner = Runner(workspace, store)
    environment = asyncio.run(runner.prepare(request, snapshot, context))
    run = workspace.fresh_run(snapshot, candidate, context)
    assert MAX_PATH - 12 <= len(str(run.root)) < MAX_PATH
    assert len(str(run.temp_root)) >= MAX_PATH - 12
    assert workspace_path(run.root / "tests" / "test_repro.py").read_bytes() == \
        b"from example.parser import parse\ndef test_empty(): assert parse([]) == []\n"
    assert environment.python == sys.executable


@WINDOWS_ONLY
def test_a_run_copy_no_process_can_enter_is_refused_by_name(tmp_path, projects, facts):
    """Past ``PROCESS_CWD_LIMIT`` the run is refused, not started somewhere else.

    ``SetCurrentDirectoryW`` refuses a working directory at the legacy limit just as
    ``CreateProcess`` does, in either path form, so no process can be started in the run
    copy once the task directory is deep enough.  What must not happen is the quieter
    failure: starting the test process in some other directory, where the candidate's
    relative selectors name nothing.  The refusal names the limit instead.
    """
    from reproagent.paths import PROCESS_CWD_LIMIT

    request, store, workspace = band_workspace(tmp_path, projects, band=UNRUNNABLE_BAND)
    context = facts.context()
    snapshot = workspace.freeze(request, context)
    candidate = workspace.publish(CandidateDraft(
        (DraftFile("tests/test_repro.py", b"from example.parser import parse\ndef test_empty(): assert parse([]) == []\n"),),
        snapshot.snapshot_id, "contract-1", 1, "empty input"), snapshot)
    runner = Runner(workspace, store)
    # The environment probe still runs: creating the copy and starting a process in it
    # are different limits, and the copy is built through the form that reaches it.
    environment = asyncio.run(runner.prepare(request, snapshot, context))
    with pytest.raises(ValueError, match=f"longer than {PROCESS_CWD_LIMIT} characters"):
        asyncio.run(runner.execute(candidate, snapshot, environment, context))
    run_root = next((store.root / "runs").iterdir()) / "code"
    assert len(str(run_root)) > PROCESS_CWD_LIMIT
    assert workspace_path(run_root / "tests" / "test_repro.py").read_bytes() == \
        b"from example.parser import parse\ndef test_empty(): assert parse([]) == []\n"


@WINDOWS_ONLY
def test_a_snapshot_root_past_the_limit_does_not_abort_the_run(tmp_path, projects, facts):
    """The run reaches the model's own end state and still delivers its package.

    The task root here is below the file limit while the frozen copy inside it is not, so
    the export build directory (``task + 53``) sits in the reserved window where a
    *directory* stops working before a file does.  That window is not a failure band: the
    build name and the final name are promoted together as soon as either crosses the file
    limit and stay ordinary below it, so the publish rename never mixes the two forms, and
    the package is published.  Both halves are checked here -- the snapshot-path mapping
    gets the task analysed and one exploration phase to its own result, and the diagnostic
    package is delivered rather than failing on the way out.
    """
    request, store, _ = band_workspace(tmp_path, projects)
    build = store.root / "artifacts" / (".building-" + "0" * 32)
    assert len(str(store.root)) < MAX_PATH
    assert MAX_PATH - 12 <= len(str(build)) < MAX_PATH        # the reserved window
    model = ScriptedModel(missing=True)
    controller = create_controller(request, ModelConfig(), gateway=model,
                                   explorer_factory=PhasePlan([ask("stop here")]))
    result = asyncio.run(controller.run(request, facts.context(limits=request.limits)))
    assert model.kinds == ["contract"]
    assert [context.contract.contract_id for context in controller.explorer.contexts]
    assert result.status == TaskState.NEEDS_INFORMATION
    assert result.stop_reason == "MISSING_INFORMATION"
    assert result.export_state == "published"
    assert (request.output_dir / "artifacts/diagnostic/report.json").exists()


@WINDOWS_ONLY
def test_deep_snapshot_reads_stay_relative_and_citable(tmp_path, projects, facts):
    """The SDK file tools read the frozen copy past the limit and cite it relatively.

    The recorded failure was a snapshot whose root already crossed the legacy limit, so
    the tools only reach it through the ``\\\\?\\`` form.  What they display must still name
    the file the way the rest of the task does -- relative to the task store, with no
    platform prefix and nothing absolute -- and the boundary must not be relaxed by the
    path form: a location outside the snapshot is refused whichever form it is written in.
    """
    from agentscope.message import ToolResultState

    from reproagent.adapters.agentscope.snapshot_backend import SnapshotDenied

    env = sdk_environment(tmp_path, projects, facts, rg=find_ripgrep())
    snapshot, backend = env.snapshot, env.backend
    module = snapshot.root / "example" / "parser.py"
    # The frozen copy and its files are only reachable through the long form: a read of one
    # is a real long-path read, not a shorter path in disguise.
    assert str(snapshot.root).startswith("\\\\?\\")
    assert len(str(module)) > MAX_PATH
    assert asyncio.run(backend.read_file("example/parser.py")) == (tmp_path / "repo" / "example" / "parser.py").read_bytes()

    # The evidence identity the product stores is workspace-relative, exactly as the old
    # native read reported it -- not the internal long form and not an absolute path.
    expected = f"snapshots/{snapshot.snapshot_id}/code/example/parser.py"
    content_hash = next(item for item in snapshot.files if item.path == "example/parser.py").content_hash

    read = tool_call(env, "Read", {"file_path": str(module)})
    assert "def parse(values):" in chunk_text(read)
    # The sidecar names the file the way the snapshot does, so the model can copy it back
    # -- never the long form it was read through.
    refs = sidecar_refs(chunk_text(read))
    assert [(ref["path"], ref["start_line"], ref["end_line"]) for ref in refs] == [("example/parser.py", 1, 2)]
    assert all(not Path(ref["path"]).is_absolute() and "\\\\?\\" not in ref["path"] for ref in refs)

    # A search of the same deep root cites the same registered lines, even though ripgrep
    # prints the long absolute path the backend handed it.
    if env.rg is not None:
        grep = tool_call(env, "Grep", {"pattern": "def parse", "path": str(snapshot.root), "output_mode": "content"})
        assert [ref["path"] for ref in sidecar_refs(chunk_text(grep))] == ["example/parser.py"]

    # Citing those displayed lines back ends the phase with the store-relative reference
    # the rest of the task uses, which is what the deep root must not change.
    revision = {"source_refs": [{"path": "example/parser.py", "content_hash": content_hash,
                                 "start_line": 1, "end_line": 2}], "reason": "the parse source"}
    assert json.loads(chunk_text(tool_call(env, "revise_contract", revision)))["status"] == "revision_requested"
    assert [(ref.path, ref.content_hash, ref.start_line, ref.end_line) for ref in env.gate.result.source_refs] == \
        [(expected, content_hash, 1, 2)]
    cited = env.gate.result.source_refs[0].path
    assert not Path(cited).is_absolute() and "\\\\?\\" not in cited and str(snapshot.root) not in cited

    # Outside the snapshot is refused whatever form it takes, and the SDK tool reports the
    # refusal as a controlled error rather than reading from the machine.
    for outside in (str(tmp_path / "repo" / "issue.md"), str(snapshot.root.parent / "snapshot.json"), str(tmp_path)):
        with pytest.raises(SnapshotDenied):
            asyncio.run(backend.read_file(outside))
    assert tool_call(env, "Read", {"file_path": str(tmp_path / "repo" / "issue.md")}).state is ToolResultState.ERROR


@WINDOWS_ONLY
def test_a_helper_child_on_a_long_path_is_killed_and_reaped(tmp_path, projects, facts, monkeypatch):
    """Task 2's timeout and cancel policy holds where the paths themselves are long.

    The SDK's Glob runs a helper script as a child process, and that child is killed and
    reaped before a timed-out or cancelled call returns.  A snapshot root past the legacy
    limit must not change that: the helper still runs against the deep root, and no child
    survives either ending.
    """
    env = sdk_environment(tmp_path, projects, facts)
    backend, root = env.backend, env.snapshot.root
    command = ["python3", glob_helper_path(), "--pattern", "**/*.py", "--base-dir", str(root)]
    assert str(root).startswith("\\\\?\\") and len(str(root)) > MAX_PATH

    spawned, real = [], asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        process = await real(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    # It runs at this depth, and what it matched is still the registered manifest.
    accepted = asyncio.run(backend.exec_shell(command))
    assert accepted.ok(), accepted.stderr
    matched = json.loads(accepted.stdout.decode("utf-8"))
    assert any(Path(path).name == "parser.py" for path in matched)
    assert all(Path(path).is_absolute() and str(root) in path for path in matched)
    assert spawned[-1].returncode is not None and process_is_gone(spawned[-1].pid)

    # A timeout kills and reaps the child, and the operating system agrees it is gone.
    timed_out = asyncio.run(backend.exec_shell(command, timeout=0.0))
    assert timed_out.exit_code == -1 and b"timed out" in timed_out.stderr
    assert spawned[-1].returncode is not None and process_is_gone(spawned[-1].pid)

    # A cancel that lands while the child runs kills it too, and the call reports it.
    async def cancelling(*args, **kwargs):
        process = await real(*args, **kwargs)
        spawned.append(process)
        env.context.cancel_event.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", cancelling)
    with pytest.raises(BudgetStopped):
        asyncio.run(backend.exec_shell(command))
    assert spawned[-1].returncode is not None and process_is_gone(spawned[-1].pid)

    # With the flag still set a further call starts no child at all, and once it is cleared
    # the same deep-root invocation works again.
    started = len(spawned)
    with pytest.raises(BudgetStopped):
        asyncio.run(backend.exec_shell(command))
    assert len(spawned) == started
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    env.context.cancel_event.clear()
    assert asyncio.run(backend.exec_shell(command)).ok()
    assert spawned[-1].returncode is not None and process_is_gone(spawned[-1].pid)


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
