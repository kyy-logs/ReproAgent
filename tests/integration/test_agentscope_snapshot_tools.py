"""The real SDK file tools, bound to the registered source snapshot.

Read/Grep/Glob are pointed at a SnapshotBackend over a frozen Workspace snapshot, so
everything they can return must come from the registered manifest: the candidate
storage, the hidden fix root, an unregistered file inside the snapshot and every
`../`/link escape must fail closed, a changed file must be refused even when the SDK
read cache is primed, and the rg/Glob helper argv the SDK emits is the only argv the
backend runs.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentscope.message import TextBlock, ToolResultState
from agentscope.state import AgentState
from agentscope.tool import Glob, Grep, Read

from reproagent.adapters.agentscope import snapshot_backend
from reproagent.adapters.agentscope.evidence import READ_LINE_CHARACTERS, EvidenceLedger
from reproagent.adapters.agentscope.snapshot_backend import SnapshotBackend, SnapshotDenied, UnverifiedContent
from reproagent.core.budget import BudgetStopped
from reproagent.core.models import CandidateDraft, DraftFile, FileEntry, ProjectView, PythonPytestConfig, TaskRequest
from reproagent.core.serialization import bytes_hash
from reproagent.paths import relative_name
from reproagent.store import TaskStore
from reproagent.workspace import Workspace

MARKER = "boundary_marker_77"
LATE_SECRET = "added_after_freeze_token_4310"
CANDIDATE_SECRET = "candidate_only_token_9137"
FIXED_SECRET = "fixed_only_token_4212"
CRLF_BYTES = b"first line\r\nsecond line\r\n"
MODULE_TEXT = (
    '"""Empty input must return an empty list."""\n'
    "\n"
    "def parse(values):\n"
    "    return [values[0]]\n"
    "\n"
    f"def {MARKER}(values):\n"
    "    return values or []\n"
)
MARKER_LINE = next(index for index, line in enumerate(MODULE_TEXT.splitlines(), 1) if MARKER in line)
OTHER_ABSOLUTE = "C:/Windows/win.ini" if os.name == "nt" else "/etc/hostname"
#: Wider than the 500 bytes the SDK's Grep shows whole, narrower than Read's 2000.
WIDE_LINE = "# " + "x" * 598
#: Wider than either tool displays whole.
VERY_WIDE_LINE = "# " + "x" * 2098
#: Names a manifest must never serve, whatever it claims to hold. Freezing leaves these out
#: of the snapshot, so reaching a read with one registered means the manifest came from
#: somewhere other than a freeze -- a hand-written or corrupted record. The secret in a
#: served view would be exposed whether or not the manifest was supposed to hold it, so the
#: read itself has to refuse, and these names are the whole of the rule.
SENSITIVE_NAMES = (".env", ".env.local", "credentials.json", "id_rsa", "id_ed25519", "service.key", "server.pem")


def chunk_text(chunk) -> str:
    return "".join(block.text for block in chunk.content if isinstance(block, TextBlock))


def entry(snapshot, path):
    return next(item for item in snapshot.files if item.path == path)


def find_ripgrep():
    """A real ripgrep for the SDK's Grep tool, or None.

    The SDK runs ``rg`` from PATH.  A machine can also keep one out of PATH, so an
    explicit ``REPROAGENT_RG_PATH`` is accepted here; without either, the Grep
    assertions check the explicit block instead of the hits.
    """
    candidates = (os.environ.get("REPROAGENT_RG_PATH"), shutil.which("rg"), shutil.which("rg.exe"))
    return next((path for path in candidates if path and Path(path).is_file()), None)


def glob_helper_path():
    """The bundled helper script the SDK's Glob resolves for a local backend."""
    import importlib.resources as resources

    return str(resources.files("agentscope.tool._builtin._scripts").joinpath("_glob_helper.py"))


def make_escape_link(link: Path, target: Path) -> bool:
    """A path inside the snapshot that resolves outside it, or False.

    A host without symlink privilege still allows a directory junction, which is the
    escape an unprivileged writer on the snapshot can actually make.
    """
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name != "nt":
        return False
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    return created.returncode == 0 and link.exists()


def environment(tmp_path, projects, facts, rg=None):
    """A frozen snapshot, plus the candidate and hidden fix copies it must not expose."""
    repo = projects.plain(tmp_path / "中文 repo")
    (repo / "example" / "中文模块.py").write_text(MODULE_TEXT, encoding="utf-8", newline="")
    (repo / "example" / "crlf.py").write_bytes(CRLF_BYTES)
    (repo / "example" / "binary.py").write_bytes(b"value = 1\x00\x02\n")
    (repo / "example" / "latin.py").write_bytes("note = 'café'\n".encode("latin-1"))
    (repo / "example" / "form_feed.py").write_bytes(b"line1\x0cform\nsecond\n")
    (repo / "example" / "long_lines.py").write_text(WIDE_LINE + "\n# short\n" + VERY_WIDE_LINE + "\n", encoding="utf-8", newline="")
    # Two more registered files carry the marker, so a search that needs more than one
    # command line still has to reach all of them.
    (repo / "example" / "second.py").write_text(f"value = 2\n# {MARKER}\n", encoding="utf-8", newline="")
    (repo / "tests" / "test_marker.py").write_text(f"# {MARKER}\n", encoding="utf-8", newline="")
    fixed = projects.fixed(tmp_path / "fixed-hidden")
    (fixed / "example" / "secret_fix.py").write_text(f"# {FIXED_SECRET}\n", encoding="utf-8")
    task = tmp_path / "task"
    store = TaskStore(task)
    workspace = Workspace(task, store)
    request = TaskRequest(repo, task, repo / "issue.md", language=PythonPytestConfig())
    snapshot = workspace.freeze(request, facts.context())
    candidate = workspace.publish(CandidateDraft(
        (DraftFile("tests/test_repro.py", f"# {CANDIDATE_SECRET}\n".encode()),),
        snapshot.snapshot_id, "contract-1", 1, "empty input", candidate_id="candidate-1"), snapshot)
    project = ProjectView(snapshot, (candidate,))
    context = facts.context()
    module = snapshot.root / "example" / "中文模块.py"
    return SimpleNamespace(repo=repo, fixed=fixed, task=task, store=store, workspace=workspace, snapshot=snapshot,
        candidate=candidate, project=project, context=context, root=snapshot.root, module=module,
        module_path=str(module), crlf=snapshot.root / "example" / "crlf.py",
        backend=SnapshotBackend(project, store, context, rg_path=rg), ledger=EvidenceLedger(project, store))


def test_real_sdk_glob_grep_read_use_registered_snapshot(tmp_path, projects, facts):
    rg = find_ripgrep()
    env = environment(tmp_path, projects, facts, rg=rg)
    # A file written into the frozen copy after the freeze: the manifest, not the
    # directory listing, decides what the tools can serve.
    (env.root / "example" / "added_after_freeze.py").write_text(f"# {LATE_SECRET}\n", encoding="utf-8")

    listing = chunk_text(asyncio.run(Glob(backend=env.backend).call(pattern="**/*.py", path=str(env.root))))
    assert "中文模块.py" in listing and "test_existing.py" in listing
    assert "added_after_freeze.py" not in listing

    content = chunk_text(asyncio.run(Read(backend=env.backend).call(file_path=env.module_path, _agent_state=AgentState())))
    assert f"{MARKER_LINE:6d}\tdef {MARKER}(values):" in content
    assert chunk_text(asyncio.run(Read(backend=env.backend).call(file_path=str(env.crlf)))) == "     1\tfirst line\n     2\tsecond line"

    # The frozen bytes are the ones that were hashed, CRLF and CJK included.
    assert env.module.read_bytes().decode("utf-8") == MODULE_TEXT
    assert entry(env.snapshot, "example/中文模块.py").content_hash == bytes_hash(MODULE_TEXT.encode("utf-8"))
    assert env.crlf.read_bytes() == CRLF_BYTES
    assert entry(env.snapshot, "example/crlf.py").content_hash == bytes_hash(CRLF_BYTES)

    refused = asyncio.run(Read(backend=env.backend).call(file_path=str(env.fixed / "example" / "parser.py")))
    assert refused.state is ToolResultState.ERROR and "does not exist" in chunk_text(refused)
    # The hidden fix root and the candidate storage are not searchable either: outside the
    # snapshot, both Glob and Grep report a refusal rather than listing what is there (with
    # ripgrep absent, the refusal is the explicit block — see the two tests below).
    hidden = chunk_text(asyncio.run(Glob(backend=env.backend).call(pattern="**/*.py", path=str(env.fixed))))
    assert "Directory not found" in hidden and "parser.py" not in hidden
    refusals = []
    for path in (str(env.fixed), str(env.candidate.storage_root)):
        refused_grep = asyncio.run(Grep(backend=env.backend).call(pattern=MARKER, path=path))
        assert refused_grep.state is ToolResultState.ERROR
        refusals.append(chunk_text(refused_grep))
    for text in (listing, content):
        # Nothing the manifest does not hold appears in a served view: not the candidate, not
        # the hidden fix copy, not even the name of a file written into the frozen copy later.
        assert CANDIDATE_SECRET not in text and FIXED_SECRET not in text and LATE_SECRET not in text
        assert "secret_fix.py" not in text and str(env.fixed) not in text and str(env.candidate.storage_root) not in text
    for text in (hidden, chunk_text(refused), *refusals):
        # A refusal names only the path that was asked for, and never a byte of what is there.
        assert CANDIDATE_SECRET not in text and FIXED_SECRET not in text and "secret_fix.py" not in text


@pytest.mark.skipif(find_ripgrep() is None, reason="no ripgrep on this host, so the SDK search blocks by design")
def test_real_sdk_grep_content_hits_registered_lines(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts, rg=find_ripgrep())
    grep = asyncio.run(Grep(backend=env.backend).call(pattern=MARKER, path=str(env.root), output_mode="content"))
    assert grep.state is ToolResultState.SUCCESS
    text = chunk_text(grep)
    assert f"{env.module_path}:{MARKER_LINE}:def {MARKER}(values):" in text
    assert CANDIDATE_SECRET not in text and FIXED_SECRET not in text and LATE_SECRET not in text
    assert str(env.fixed) not in text and str(env.candidate.storage_root) not in text


@pytest.mark.skipif(find_ripgrep() is None, reason="no ripgrep on this host, so the SDK search blocks by design")
def test_a_search_larger_than_one_command_line_reaches_every_registered_file(tmp_path, projects, facts, monkeypatch):
    # A repository does not fit on one command line, and ripgrep cannot read a path list from
    # a file, so the registered files are searched in ordered runs.  A tiny command-line
    # bound forces one run per file here.
    monkeypatch.setattr(snapshot_backend, "MAX_COMMAND_CHARS", 260)
    env = environment(tmp_path, projects, facts, rg=find_ripgrep())
    result = asyncio.run(env.backend.exec_shell(["rg", "--hidden", "--sort", "path", "--max-columns", "500", "-n", MARKER, str(env.root)]))
    assert result.ok(), result.stderr
    relative = [line[len(str(env.root)) + 1:] for line in result.stdout.decode("utf-8").splitlines() if line]
    assert all(re.fullmatch(r"[^:]+:\d+:.*", line) for line in relative)   # path:line:content
    found = [line.split(":", 1)[0] for line in relative]
    assert {Path(path).name for path in found} == {"中文模块.py", "second.py", "test_marker.py"}
    assert found == sorted(found)                    # the runs stay in path order
    grep = chunk_text(asyncio.run(Grep(backend=env.backend).call(pattern=MARKER, path=str(env.root), output_mode="content")))
    for name in ("中文模块.py", "second.py", "test_marker.py"):
        assert name in grep
    # A path that is not in the snapshot is still refused before any run happens.
    assert asyncio.run(env.backend.exec_shell(["rg", "--hidden", MARKER, str(env.fixed)])).exit_code == 2


@pytest.mark.parametrize("name", SENSITIVE_NAMES)
def test_a_registered_sensitive_name_is_never_served(tmp_path, projects, facts, name):
    """The manifest is not the only gate: a credential name is refused at the read itself."""
    env = environment(tmp_path, projects, facts)
    secret = f"private_token_for_{name}"
    (env.root / name).write_text(secret, encoding="utf-8")
    item = FileEntry(name, bytes_hash(secret.encode()), len(secret.encode()), "data")
    project = replace(env.project, snapshot=replace(env.snapshot, files=(*env.snapshot.files, item)))
    backend, path = SnapshotBackend(project, env.store, env.context), str(env.root / name)
    frozen = backend.snapshot
    # The entry is registered, so "not part of the snapshot" cannot be what refuses it.
    assert frozen.registered(path) is item
    with pytest.raises(SnapshotDenied) as denied:
        frozen.read_verified(item)
    assert "sensitive" in str(denied.value)
    with pytest.raises(SnapshotDenied):
        asyncio.run(backend.read_file(path))
    # The SDK tool reports it as a controlled error, and the file's bytes never reach a view.
    view = asyncio.run(Read(backend=backend).call(file_path=path))
    assert view.state is ToolResultState.ERROR and secret not in chunk_text(view)


def test_traversal_symlink_and_cache_cannot_bypass_hash(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    backend, ledger = env.backend, env.ledger
    late = env.root / "example" / "added_after_freeze.py"
    late.write_text(f"# {LATE_SECRET}\n", encoding="utf-8")

    # Anything the manifest does not name is denied, whatever form the path takes.
    for path in (str(env.root / ".." / ".."), str(env.root.parent / "snapshot.json"), str(env.fixed / "example" / "parser.py"),
                 str(env.candidate.storage_root / "test_repro.py"), str(late), str(tmp_path), OTHER_ABSOLUTE, ""):
        with pytest.raises(SnapshotDenied):
            asyncio.run(backend.read_file(path))
    # A relative path anchors at the snapshot root, and one that resolves to a registered
    # file inside it is the same file; one that resolves out of it never is.
    assert asyncio.run(backend.read_file("example/crlf.py")) == CRLF_BYTES
    assert asyncio.run(backend.read_file("example/../example/crlf.py")) == CRLF_BYTES
    for path in ("../example/crlf.py", "example/../../example/crlf.py", f"..{os.sep}..{os.sep}example"):
        with pytest.raises(SnapshotDenied):
            asyncio.run(backend.read_file(path))

    # The SDK tool reports the same refusal as a controlled error rather than a crash.
    outside = asyncio.run(Read(backend=backend).call(file_path=str(env.fixed / "example" / "parser.py")))
    assert outside.state is ToolResultState.ERROR and LATE_SECRET not in chunk_text(outside)

    # A first read primes the SDK's read cache; a file changed afterwards (with its
    # mtime put back, the only key that cache looks at) is still refused.
    state = AgentState()
    first = chunk_text(asyncio.run(Read(backend=backend).call(file_path=env.module_path, _agent_state=state)))
    assert MARKER in first
    stat = env.module.stat()
    changed = bytearray(env.module.read_bytes())
    changed[0] = ord("#")
    env.module.write_bytes(bytes(changed))
    os.utime(env.module, (stat.st_atime, stat.st_mtime))
    with pytest.raises(UnverifiedContent):
        asyncio.run(backend.read_file(env.module_path))
    with pytest.raises(UnverifiedContent):
        ledger.record_visible(env.module_path, MARKER_LINE, MARKER_LINE)
    second = asyncio.run(Read(backend=backend).call(file_path=env.module_path, _agent_state=state))
    assert second.state is ToolResultState.ERROR and MARKER not in chunk_text(second)

    # Missing, binary and non-UTF-8 files are controlled errors, and issue no reference.
    citation = ledger.record_visible(str(env.crlf), 1, 2)
    assert ledger.contains(citation)
    for name, refusal in (("missing.py", SnapshotDenied), ("binary.py", UnverifiedContent), ("latin.py", UnverifiedContent)):
        path = str(env.root / "example" / name)
        with pytest.raises(refusal):
            asyncio.run(backend.read_file(path))
        with pytest.raises(refusal):
            ledger.record_visible(path, 1, 1)
        assert not ledger.contains(replace(citation, path=relative_name(env.root / "example" / name, env.store.root)))
        assert asyncio.run(Read(backend=backend).call(file_path=path)).state is ToolResultState.ERROR

    # A registered file removed from the frozen copy is refused too, and the citation
    # this ledger already issued for it stops being evidence.
    env.crlf.unlink()
    with pytest.raises(UnverifiedContent):
        asyncio.run(backend.read_file(str(env.crlf)))
    assert not ledger.contains(citation)


def test_an_escape_link_inside_the_snapshot_is_refused(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    link = env.root / "escaped"
    if not make_escape_link(link, env.fixed):
        pytest.skip("this host does not allow a link inside the snapshot")
    target = link / "example" / "parser.py"
    assert Path(os.path.realpath(target)).exists()      # the escape is real, not a broken link
    assert not asyncio.run(env.backend.is_dir(str(link)))
    with pytest.raises(SnapshotDenied):
        asyncio.run(env.backend.read_file(str(target)))
    with pytest.raises(SnapshotDenied):
        env.ledger.record_visible(str(target), 1, 1)
    listing = chunk_text(asyncio.run(Glob(backend=env.backend).call(pattern="**/*.py", path=str(env.root))))
    assert "secret_fix.py" not in listing and FIXED_SECRET not in listing


def test_helper_is_bounded_and_cannot_run_arbitrary_commands(tmp_path, projects, facts, monkeypatch):
    rg = find_ripgrep()
    env = environment(tmp_path, projects, facts, rg=rg)
    backend = env.backend
    helper = glob_helper_path()
    written = tmp_path / "written_by_shell.txt"

    # No general shell, no write, no delete: the SDK registers none of these tools, and
    # the backend refuses them even when something else calls it directly.
    for command in (["cmd", "/c", f"echo hi > {written}"], ["/bin/sh", "-c", f"cat {env.fixed}/example/parser.py"],
                    ["whoami"], ["pwd"], ["find", str(env.root), "-type", "f"], ["rm", "-rf", str(env.root)],
                    ["printenv", "HOME"], ["python3", "-c", "print(1)"], []):
        result = asyncio.run(backend.exec_shell(command))
        assert not result.ok() and b"blocked" in result.stderr, command
    assert not written.exists()
    with pytest.raises(SnapshotDenied):
        asyncio.run(backend.write_file(str(env.root / "example" / "written.py"), b"x = 1\n"))
    with pytest.raises(SnapshotDenied):
        asyncio.run(backend.delete_path(str(env.crlf)))
    assert env.crlf.exists() and not (env.root / "example" / "written.py").exists()

    # A disguised or repeated rg argv cannot widen what rg is given to read.
    secret = str(env.fixed / "example" / "parser.py")
    for args in (["--hidden", "--sort", "path", "--pre", f"cmd /c type {secret}", MARKER, str(env.root)],
                 ["--hidden", "--sort", "path", "--files-from", secret, MARKER, str(env.root)],
                 ["--hidden", "--sort", "path", "-g", "*", MARKER, str(env.root)],
                 ["--hidden", "--sort", "path", "--follow", MARKER, str(env.root)],
                 ["--hidden", "--sort", "path", "--glob", str(env.fixed / "*.py"), MARKER, str(env.root)],
                 ["--hidden", "--sort", "modified", MARKER, str(env.root)],
                 ["--hidden", "--max-columns", "-1", MARKER, str(env.root)],
                 ["--hidden", "--type", "py; cmd /c whoami", MARKER, str(env.root)],
                 ["--hidden", MARKER, "--pre=x"], [MARKER], [MARKER, str(env.root), str(env.root)],
                 ["--hidden", MARKER, str(env.fixed)], ["--hidden", MARKER, str(env.root / "example" / "missing.py")]):
        assert not asyncio.run(backend.exec_shell(["rg", *args])).ok(), args
    # A search outside the snapshot is refused, not answered "no matches": the model has
    # to be able to tell a search that ran from one that never happened.
    outside = asyncio.run(backend.exec_shell(["rg", "--hidden", "--sort", "path", MARKER, str(env.fixed)]))
    assert outside.exit_code == 2 and outside.stdout == b"" and b"not a registered file or directory" in outside.stderr
    # A directory the snapshot holds but no registered file lives in is an empty search,
    # never a walk of that directory.
    empty = env.root / "example" / "empty_after_freeze"
    empty.mkdir()
    (empty / "hidden.py").write_text(f"# {LATE_SECRET}\n", encoding="utf-8")
    blank = asyncio.run(backend.exec_shell(["rg", "--hidden", "--sort", "path", LATE_SECRET, str(empty)]))
    assert blank.stdout == b"" and LATE_SECRET.encode() not in blank.stdout
    if rg is None:
        assert blank.exit_code == 126 and b"ripgrep" in blank.stderr      # nothing was searched, so nothing is claimed
    else:
        assert blank.exit_code == 1 and blank.stderr == b""

    # The argv the SDK's Glob emits is accepted, and serves the manifest only.
    (env.root / "example" / "added_after_freeze.py").write_text(f"# {LATE_SECRET}\n", encoding="utf-8")
    accepted = asyncio.run(backend.exec_shell(["python3", helper, "--pattern", "**/*.py", "--base-dir", str(env.root)]))
    assert accepted.ok()
    matched = json.loads(accepted.stdout.decode("utf-8"))
    assert any(path.endswith("中文模块.py") for path in matched)
    assert not any("added_after_freeze" in path for path in matched)
    assert all(Path(path).is_absolute() and str(env.root) in path for path in matched)
    for command in (["python3", helper, "--pattern", "**/*", "--base-dir", str(env.fixed)],
                    ["python3", helper, "--pattern", "**/*", "--base-dir", "../.."],
                    ["python3", helper, "--pattern", "../**/*", "--base-dir", str(env.root)],
                    ["python3", helper, "--pattern", str(env.fixed / "*"), "--base-dir", str(env.root)],
                    ["python3", helper, "--pattern", "**/*", "--base-dir", str(env.root), "--extra", "1"],
                    ["python3", helper, "--base-dir", str(env.root)],
                    ["python3", str(tmp_path / "evil.py"), "--pattern", "**/*", "--base-dir", str(env.root)],
                    ["/bin/sh", helper, "--pattern", "**/*", "--base-dir", str(env.root)],
                    [sys.executable, helper, "--pattern", "**/*", "--base-dir", str(env.root), "--pattern", "x"]):
        assert not asyncio.run(backend.exec_shell(command)).ok(), command

    # A timeout or a cancel kills and reaps the child, and a cancelled call starts nothing.
    spawn = ["python3", helper, "--pattern", "**/*", "--base-dir", str(env.root)]
    spawned, real = [], asyncio.create_subprocess_exec
    async def spy(*args, **kwargs):
        process = await real(*args, **kwargs)
        spawned.append(process)
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    timed_out = asyncio.run(backend.exec_shell(spawn, timeout=0.0))
    assert timed_out.exit_code == -1 and b"timed out" in timed_out.stderr
    assert spawned and spawned[-1].returncode is not None
    env.context.cancel_event.set()
    with pytest.raises(BudgetStopped):
        asyncio.run(backend.exec_shell(spawn))
    with pytest.raises(BudgetStopped):
        asyncio.run(backend.exec_shell(["rg", "--hidden", "--sort", "path", MARKER, str(env.root)]))
    assert len(spawned) == 1
    env.context.cancel_event.clear()
    assert asyncio.run(backend.exec_shell(spawn)).ok()

    # A cancel that lands while the child runs kills it too.
    async def cancelling(*args, **kwargs):
        process = await real(*args, **kwargs)
        spawned.append(process)
        env.context.cancel_event.set()
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", cancelling)
    with pytest.raises(BudgetStopped):
        asyncio.run(backend.exec_shell(spawn))
    assert spawned[-1].returncode is not None
    env.context.cancel_event.clear()


def test_without_ripgrep_the_sdk_search_blocks_instead_of_falling_back(tmp_path, projects, facts, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    env = environment(tmp_path, projects, facts, rg=None)
    result = asyncio.run(env.backend.exec_shell(["rg", "--hidden", "--sort", "path", MARKER, str(env.root)]))
    assert not result.ok() and b"rg" in result.stderr and b"blocked" in result.stderr
    grep = asyncio.run(Grep(backend=env.backend).call(pattern=MARKER, path=str(env.root), output_mode="content"))
    assert grep.state is ToolResultState.ERROR and "ripgrep" in chunk_text(grep).lower()


def test_ledger_cites_only_verified_original_lines(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    cited = env.ledger.record_visible(env.module_path, MARKER_LINE, MARKER_LINE)
    assert cited.path == relative_name(env.module, env.store.root)
    assert cited.content_hash == entry(env.snapshot, "example/中文模块.py").content_hash
    assert (cited.start_line, cited.end_line) == (MARKER_LINE, MARKER_LINE)
    assert env.ledger.contains(cited)
    # A citation the ledger did not issue, or one whose file no longer matches it, is not evidence.
    for forged in (replace(cited, content_hash=bytes_hash(b"elsewhere")), replace(cited, start_line=MARKER_LINE + 1),
                   replace(cited, end_line=MARKER_LINE + 1), replace(cited, path=relative_name(env.crlf, env.store.root))):
        assert not env.ledger.contains(forged)
    for start, end in ((0, 1), (3, 2), (1, 10_000), (1, len(MODULE_TEXT.splitlines()) + 1)):
        with pytest.raises(UnverifiedContent):
            env.ledger.record_visible(env.module_path, start, end)
    # A path outside the manifest is denied before any line is considered.
    with pytest.raises(SnapshotDenied):
        env.ledger.record_visible(str(env.fixed / "example" / "parser.py"), 1, 1)
    # Re-citing the very range the SDK displayed is stable.
    assert env.ledger.record_visible(env.module_path, MARKER_LINE, MARKER_LINE) == cited


def test_ledger_counts_lines_the_way_the_verifier_does(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    # A CRLF file: the lines the verifier splits out of the bytes and the lines the SDK
    # displays are the same lines.
    counted = len(env.crlf.read_bytes().splitlines())
    assert counted == 2
    assert (env.ledger.record_visible(str(env.crlf), 1, counted).end_line) == counted
    with pytest.raises(UnverifiedContent):
        env.ledger.record_visible(str(env.crlf), 1, counted + 1)
    # A form feed splits the SDK's view of the text but not the verifier's view of the bytes,
    # so the two disagree about what line 2 is; nothing in that file is citable.
    raw = (env.root / "example" / "form_feed.py").read_bytes()
    assert (len(raw.splitlines()), len(raw.decode("utf-8").splitlines())) == (2, 3)
    for start, end in ((1, 1), (2, 2), (3, 3), (1, 3)):
        with pytest.raises(UnverifiedContent):
            env.ledger.record_visible(str(env.root / "example" / "form_feed.py"), start, end)


def test_a_line_grep_cannot_display_whole_is_not_citable(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts)
    wide = str(env.root / "example" / "long_lines.py")
    assert 500 < len(WIDE_LINE) <= READ_LINE_CHARACTERS < len(VERY_WIDE_LINE)
    # The SDK's Grep always passes --max-columns 500, so its view of a 600-byte line is not
    # the whole line and the line cannot be cited from a Grep view.
    with pytest.raises(UnverifiedContent):
        env.ledger.record_visible(wide, 1, 1)
    # Read displays that line whole, so a caller whose view came from Read may say so.
    reader = EvidenceLedger(env.project, env.store, max_line_characters=READ_LINE_CHARACTERS)
    assert reader.contains(reader.record_visible(wide, 1, 1))
    # Neither tool displays a 2100-byte line whole.
    for ledger in (env.ledger, reader):
        with pytest.raises(UnverifiedContent):
            ledger.record_visible(wide, 3, 3)
    # The intact lines of the same file are still citable.
    assert env.ledger.contains(env.ledger.record_visible(wide, 2, 2))
