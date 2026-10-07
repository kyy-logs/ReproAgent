"""The read-only view of one frozen source snapshot that the SDK's file tools run behind.

``Read``/``Grep``/``Glob`` accept a ``BackendBase``, and every filesystem query the base
class does not declare abstract (``file_exists``, ``is_dir``, ``list_dir``, ``scandir``,
``stat``, ``stat_mtime``, ``getcwd``, ``delete_path``) is *derived from* ``exec_shell``.
A permissive shell would therefore be the whole boundary, so this backend answers those
queries from the frozen manifest instead, refuses writes and deletes outright, and runs
only the two program shapes the SDK actually issues:

* its ripgrep argv, with the search path it was given replaced by the registered files
  under that path, searched in as many path-ordered runs as one command line allows
  (ripgrep has no way to read a path list from a file, and it is never handed a
  directory);
* its ``_glob_helper.py`` invocation, run with this process's own interpreter and its
  matches filtered through the manifest.

No other argv is executed, so a pattern, path or option smuggled into either shape
cannot widen what the tools read, and the derived base-class queries see a refusal
(:data:`EXIT_BLOCKED`) rather than a host command.

The manifest, not the directory listing, is the truth: a file written into the frozen
copy after the freeze is invisible, and a registered file whose bytes changed is refused
on every read.  ``stat_mtime`` returns a fresh token on each call so the SDK's read
cache is never served without that re-verification.

Cancellation follows the product's own convention (``BudgetStopped``), and a timed-out
or cancelled child is killed and reaped before the call returns.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from agentscope.tool import BackendBase, DirEntry, ExecResult

from .dependency import require_agentscope
from ...core.budget import BudgetStopped
from ...core.models import CallContext, FileEntry, ProjectView
from ...core.serialization import bytes_hash
from ...paths import identity_key, is_within, relative_name, workspace_path
from ...store import TaskStore
from ...workspace import sensitive_path

#: A command that is not one of the two shapes the SDK issues.  Distinct from ripgrep's
#: own 2 so a refusal can never be read as "no matches".
EXIT_BLOCKED = 126
#: Ripgrep's convention for "I could not run this search", used for a refused search.
EXIT_RG_ERROR = 2
#: The SDK's convention for a command killed on timeout.
EXIT_TIMEOUT = -1

#: A registered file is either served whole or refused; a view is never silently short.
MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_STDOUT_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
#: The Glob helper's JSON holds absolute paths only, so it is allowed to be larger.
MAX_GLOB_BYTES = 4 * 1024 * 1024
#: ``CreateProcess`` refuses a command line past 32767 characters; stay clear of it.
MAX_COMMAND_CHARS = 24000
#: How many command-line-sized search runs one search may take.  A repository does not fit
#: on a single command line, so a search of the snapshot root is split into ordered runs;
#: this ceiling keeps a pathological snapshot from turning one search into unbounded work.
MAX_SEARCH_RUNS = 256
MAX_PATTERN_CHARS = 1024
MAX_PATH_CHARS = 4096
MAX_GLOB_CHARS = 512
#: How often a running child is checked against the task's cancel flag, as the local
#: runtime checks its own subprocess.
CANCEL_POLL_SECONDS = 0.02

TRUNCATION_MARKER = "\n[... output truncated by the snapshot backend ...]\n"

#: The options the SDK's ``Grep`` emits.  Anything absent from this pair of sets is
#: refused, so ``--pre``, ``--files-from``, ``-f/--file``, ``--follow``, ``--no-ignore``
#: and every other way to reach a file outside the manifest cannot be smuggled in.
RG_FLAGS = frozenset({"--hidden", "-U", "--multiline-dotall", "-i", "-l", "-c", "-n", "-H", "--with-filename"})
RG_VALUED_FLAGS = frozenset({"--sort", "--glob", "--max-columns", "--type", "-A", "-B", "-C"})
RG_INTEGERS = {"--max-columns": (1, 100_000), "-A": (0, 1000), "-B": (0, 1000), "-C": (0, 1000)}
RG_TYPE = re.compile(r"[A-Za-z0-9_+-]{1,32}\Z")
INTERPRETERS = frozenset({"python", "python3", "python.exe", "python3.exe", "py", "py.exe"})
GLOB_HELPER_NAME = "_glob_helper.py"


class SnapshotRefusal(Exception):
    """Base for every request the registered snapshot does not serve."""


class SnapshotDenied(SnapshotRefusal, PermissionError):
    """A path, command or command shape that is not part of the registered snapshot."""


class UnverifiedContent(SnapshotRefusal, ValueError):
    """Registered, but not verified original UTF-8 text, or not a real line range in it."""


class FrozenSnapshot:
    """The manifest of one frozen snapshot, plus reads that are verified against it.

    Paths are always resolved before they are looked up, and a file is always read
    through the entry's own manifest path, so a link or a renamed name on disk can
    neither extend nor redirect what the snapshot serves.
    """

    def __init__(self, project: ProjectView, store: TaskStore) -> None:
        self.project, self.store = project, store
        self.root = workspace_path(project.snapshot.root)
        self._registered = []
        for item in project.snapshot.files:
            path = self._absolute(item)
            self._registered.append((identity_key(path), item, path))
        self._by_key = {key: (item, path) for key, item, path in self._registered}
        self._paths = {item.path: path for _, item, path in self._registered}
        self._verify_manifest()

    def _verify_manifest(self) -> None:
        """The registry is the stored snapshot record's, not whatever the caller passed."""
        try:
            record = self.store.load_record("snapshots", self.project.snapshot.snapshot_id)
        except FileNotFoundError:
            return
        if record.manifest_hash != self.project.snapshot.manifest_hash:
            raise ValueError("snapshot manifest does not match the stored record")

    def _absolute(self, item: FileEntry) -> Path:
        path = workspace_path(self.root.joinpath(*item.path.split("/")))
        if not is_within(path, self.root):
            raise SnapshotDenied(f"registered path escapes the snapshot: {item.path!r}")
        return path

    def anchored(self, path: str) -> str | None:
        """*path* as an absolute path, relative paths anchored at the snapshot root."""
        if not isinstance(path, str) or not path or len(path) > MAX_PATH_CHARS or "\x00" in path:
            return None
        if os.path.isabs(path):
            return os.path.normpath(path)
        return os.path.normpath(os.path.join(str(self.root), path))

    def registered(self, path: str) -> FileEntry | None:
        """The manifest entry the location *path* names, or None.

        The lookup is by the location the path *resolves to*, so a name inside the
        snapshot that links out of it — or a registered name replaced by such a link —
        matches nothing.
        """
        anchored = self.anchored(path)
        if anchored is None:
            return None
        candidate = workspace_path(anchored)
        if not is_within(candidate, self.root):
            return None
        found = self._by_key.get(identity_key(candidate))
        return None if found is None else found[0]

    def validate_registered(self, path: str) -> FileEntry:
        """The manifest entry for *path*, or refuse."""
        item = self.registered(path)
        if item is None:
            raise SnapshotDenied(f"not part of the registered snapshot: {path!r}")
        return item

    def path_of(self, item: FileEntry) -> Path:
        """The frozen copy's path for a manifest entry, never the path a caller named."""
        path = self._paths.get(item.path)
        if path is None:
            raise SnapshotDenied(f"entry is not part of this snapshot: {item.path!r}")
        return path

    def directory(self, path: str) -> Path | None:
        """The directory inside the snapshot that *path* names, or None."""
        anchored = self.anchored(path)
        if anchored is None:
            return None
        resolved = workspace_path(anchored)
        if not is_within(resolved, self.root) or not os.path.isdir(resolved):
            return None
        return resolved

    def registered_under(self, directory: Path) -> list[Path]:
        """The registered files at or under *directory*, in manifest order."""
        key, prefix = identity_key(directory), identity_key(directory) + os.sep
        return [path for path_key, _, path in self._registered if path_key == key or path_key.startswith(prefix)]

    def level(self, directory: Path) -> tuple[list[FileEntry], list[str]]:
        """The registered files directly in *directory* and the directory names below it."""
        key, prefix = identity_key(directory), identity_key(directory) + os.sep
        relative = relative_name(directory, self.root)
        head = "" if relative in ("", ".") else f"{relative}/"
        files, names = [], []
        for path_key, item, _ in self._registered:
            if not (path_key == key or path_key.startswith(prefix)):
                continue
            name, _, deeper = item.path[len(head):].partition("/")
            if deeper:
                if name not in names:
                    names.append(name)
            else:
                files.append(item)
        return files, names

    def read_verified(self, item: FileEntry) -> bytes:
        """The entry's bytes, re-verified against the manifest on this read.

        Every read is fresh: no hash, no decoded line and no file body is cached here,
        so a file changed after an earlier read is refused rather than remembered.
        """
        if sensitive_path(item.path):
            raise SnapshotDenied(f"sensitive file is not readable: {item.path!r}")
        if item.size > MAX_TEXT_BYTES:
            raise UnverifiedContent(f"file is larger than the verified text limit of {MAX_TEXT_BYTES} bytes: {item.path!r}")
        try:
            data = self.path_of(item).read_bytes()
        except OSError as exc:
            raise UnverifiedContent(f"registered file cannot be read: {item.path!r}") from exc
        if len(data) != item.size or bytes_hash(data) != item.content_hash:
            raise UnverifiedContent(f"registered file changed on disk: {item.path!r}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnverifiedContent(f"file is not UTF-8 text: {item.path!r}") from exc
        if "\x00" in text:
            raise UnverifiedContent(f"file is not text: {item.path!r}")
        return data


class SnapshotBackend(BackendBase):
    """The SDK's read-only view of one registered source snapshot."""

    # The snapshot is a host directory, so the host's path semantics apply; the base
    # class default (``posixpath``) would reject absolute Windows paths.
    _path_module = os.path
    os_name = os.name

    def __init__(self, project: ProjectView, store: TaskStore, context: CallContext, *, rg_path: str | None = None) -> None:
        """Bind the tools to *project*'s frozen snapshot.

        Args:
            project: the snapshot (and candidates, which are never served) to bind.
            store: the task store, whose snapshot record the manifest is checked against.
            context: budget and cancellation for every read and command.
            rg_path (`str | None`, optional): the ripgrep to run; ``rg`` from PATH by
                default.  Without one, the SDK search is refused explicitly.
        """
        require_agentscope()
        self.project, self.store, self.context = project, store, context
        self.snapshot = FrozenSnapshot(project, store)
        self.root = self.snapshot.root
        self.rg_path = rg_path if rg_path is not None else shutil.which("rg")
        self._helper_root = self._installed_sdk()
        self._reads = 0

    @staticmethod
    def _installed_sdk() -> Path | None:
        """The installed SDK package, which is the only place its Glob helper may live."""
        import agentscope

        return None if agentscope.__file__ is None else Path(agentscope.__file__).resolve().parent

    def _check(self) -> None:
        """The task deadline and the cancel flag apply to every read and every command."""
        self.context.budget.check()
        if self.context.cancel_event.is_set():
            raise BudgetStopped("CANCELLED")

    # ── the manifest as the tools see it ───────────────────────────────────

    def validate_registered(self, path: str) -> FileEntry:
        """The manifest entry *path* names, or refuse."""
        return self.snapshot.validate_registered(path)

    def registered(self, path: str) -> FileEntry | None:
        """The manifest entry *path* names, or None."""
        return self.snapshot.registered(path)

    # ── reads ─────────────────────────────────────────────────────────────

    async def read_file(self, path: str) -> bytes:
        """The bytes of a registered file, re-verified against the manifest."""
        self._check()
        return self.snapshot.read_verified(self.snapshot.validate_registered(path))

    async def write_file(self, path: str, data: bytes) -> None:
        """Refused: the snapshot is read-only."""
        raise SnapshotDenied(f"the snapshot is read-only: {path!r}")

    async def delete_path(self, path: str) -> None:
        """Refused: the snapshot is read-only."""
        raise SnapshotDenied(f"the snapshot is read-only: {path!r}")

    # ── filesystem queries, answered from the manifest ────────────────────

    async def getcwd(self) -> str:
        """The SDK's default search base is the snapshot root."""
        self._check()
        return str(self.root)

    async def file_exists(self, path: str) -> bool:
        """Whether the manifest has *path*, or *path* is a directory inside the snapshot."""
        self._check()
        return self.snapshot.registered(path) is not None or self.snapshot.directory(path) is not None

    async def is_dir(self, path: str) -> bool:
        """Whether *path* is a directory inside the snapshot."""
        self._check()
        return self.snapshot.directory(path) is not None

    async def stat_mtime(self, path: str) -> float | None:
        """A new token per call, so a cached read is never served unverified.

        The SDK caches decoded lines under this value and reuses them without asking the
        backend again; what has to be re-checked is the manifest, so this token is always
        new and every Read re-reads and re-hashes the file.
        """
        self._check()
        if self.snapshot.registered(path) is None:
            return None
        self._reads += 1
        return float(self._reads)

    async def list_dir(self, path: str, *, recursive: bool = False) -> list[str]:
        """The registered names in *path*, or the registered paths under it."""
        self._check()
        directory = self.snapshot.directory(path)
        if directory is None:
            return []
        if recursive:
            return [str(candidate) for candidate in self.snapshot.registered_under(directory)]
        files, names = self.snapshot.level(directory)
        return [self.basename(item.path) for item in files] + names

    async def scandir(self, path: str) -> list[DirEntry]:
        """One manifest level: registered files and the directories that hold them."""
        self._check()
        directory = self.snapshot.directory(path)
        if directory is None:
            return []
        files, names = self.snapshot.level(directory)
        return [DirEntry(self.basename(item.path), False, item.size, None) for item in files] + [DirEntry(name, True, None, None) for name in names]

    async def stat(self, path: str) -> DirEntry | None:
        """The registered entry for *path*, sized from the manifest."""
        self._check()
        item = self.snapshot.registered(path)
        if item is not None:
            return DirEntry(self.basename(item.path), False, item.size, None)
        directory = self.snapshot.directory(path)
        return None if directory is None else DirEntry(directory.name, True, None, None)

    # ── the only programs this backend runs ───────────────────────────────

    async def exec_shell(self, command: list[str], *, cwd: str | None = None, timeout: float | None = None) -> ExecResult:
        """Run the SDK's ripgrep search or its Glob helper, and nothing else.

        A refused command returns a non-zero result whose stderr says so; the base
        class's derived queries then fail closed instead of reaching the host.
        """
        self._check()
        argv = [str(part) for part in command or ()]
        if cwd is not None and not is_within(workspace_path(cwd), self.root):
            return self._blocked(f"working directory outside the snapshot: {cwd!r}")
        budgeted = self.context.budget.command_timeout()
        timeout = budgeted if timeout is None else min(float(timeout), budgeted)
        if argv and self.basename(argv[0]).lower() in ("rg", "rg.exe"):
            return await self._run_ripgrep(argv, timeout)
        if self._is_glob_helper(argv):
            return await self._run_glob_helper(argv, timeout)
        return self._blocked(f"command is not the SDK's search helper: {argv[:2]!r}")

    @staticmethod
    def _blocked(reason: str) -> ExecResult:
        return ExecResult(EXIT_BLOCKED, b"", f"blocked: {reason}".encode("utf-8"))

    async def _run_ripgrep(self, argv: list[str], timeout: float) -> ExecResult:
        """Run ripgrep over the registered files under the path it was given."""
        try:
            args, target = self._parse_ripgrep(argv)
        except SnapshotDenied as exc:
            return ExecResult(EXIT_RG_ERROR, b"", f"blocked: {exc}".encode("utf-8"))
        # The search set is the manifest's, and it is either a directory inside the
        # snapshot or one registered file.  A path the snapshot does not hold is refused
        # rather than searched, so "no matches" always means the search really ran.
        directory = self.snapshot.directory(str(target))
        item = None if directory is not None else self.snapshot.registered(str(target))
        if directory is None and item is None:
            return ExecResult(EXIT_RG_ERROR, b"", f"blocked: {str(target)!r} is not a registered file or directory".encode("utf-8"))
        files = [str(path) for path in self.snapshot.registered_under(directory)] if directory is not None else [str(self.snapshot.path_of(item))]
        if not self.rg_path:
            # Refused rather than answered: without ripgrep there is no search to report on,
            # and the old in-process search is not a fallback.
            return self._blocked("ripgrep (rg) is not available, so the SDK search cannot run")
        if not files:
            # A snapshot directory with no registered file under it: an empty result, and
            # never a search of the directory itself (rg with no path searches its cwd).
            return ExecResult(1, b"", b"")
        runs = self._runs(args, sorted(files))
        if len(runs) > MAX_SEARCH_RUNS:
            return ExecResult(EXIT_RG_ERROR, b"", f"blocked: {len(files)} registered files under {str(target)!r} need more search runs than the snapshot backend allows; narrow the search path".encode("utf-8"))
        result, truncated = await self._search(args, runs, timeout)
        if not truncated:
            return result
        return ExecResult(result.exit_code, result.stdout + TRUNCATION_MARKER.encode("utf-8"), result.stderr)

    def _runs(self, args: list[str], files: list[str]) -> list[list[str]]:
        """Split *files*, already in path order, into command-line-sized ripgrep runs.

        ripgrep cannot read a path list from a file (no ``--files-from``, verified against
        13.0.0 and 15.1.0), so a search that does not fit on one command line is run as
        several ordered runs instead.  The runs are contiguous in path order, so their
        concatenated output is the path-sorted output one run would have produced.
        """
        fixed = len(self.rg_path or "") + sum(len(part) + 1 for part in args) + 2
        runs: list[list[str]] = []
        group: list[str] = []
        size = fixed
        for path in files:
            if group and size + len(path) + 1 > MAX_COMMAND_CHARS:
                runs.append(group)
                group, size = [], fixed
            group.append(path)
            size += len(path) + 1
        if group:
            runs.append(group)
        return runs

    async def _search(self, args: list[str], runs: list[list[str]], timeout: float) -> tuple[ExecResult, bool]:
        """Run ripgrep over every registered file, one command line at a time."""
        if len(runs) > 1:
            # A run that holds one file prints no path, so a split search would lose the
            # file each match came from; -H keeps every run's output shaped like one run's.
            args = ["-H", *args]
        deadline = time.monotonic() + timeout
        output, found, truncated = b"", False, False
        for group in runs:
            if len(output) >= MAX_STDOUT_BYTES:
                truncated = True
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return ExecResult(EXIT_TIMEOUT, b"", b"timed out"), False
            result, cut = await self._spawn([self.rg_path, *args, *group], remaining, limit=MAX_STDOUT_BYTES - len(output))
            if result.exit_code not in (0, 1):
                # Part of the search did not run: the failure is reported, never the matches
                # found so far, which would read like a complete result.
                return result, False
            output += result.stdout
            found = found or result.exit_code == 0
            if cut:
                truncated = True
                break
        return ExecResult(0 if found else 1, output, b""), truncated

    def _parse_ripgrep(self, argv: list[str]) -> tuple[list[str], Path]:
        """The SDK's ripgrep argv as (validated options plus pattern, search path).

        The pattern is always passed as an ``-e`` value, so a pattern can never be read
        as an option, and the trailing search path must be the last token: anything the
        SDK does not emit is refused rather than filtered later.
        """
        tokens, args, pattern, target = argv[1:], [], None, None
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token == "-e":
                if pattern is not None or index + 1 >= len(tokens):
                    raise SnapshotDenied("ripgrep was given a repeated or empty pattern")
                pattern, index = self._pattern(tokens[index + 1]), index + 2
                continue
            if token in RG_FLAGS:
                args.append(token)
                index += 1
                continue
            if token in RG_VALUED_FLAGS:
                if index + 1 >= len(tokens):
                    raise SnapshotDenied(f"ripgrep option {token!r} has no value")
                args.extend([token, self._option_value(token, tokens[index + 1])])
                index += 2
                continue
            if token.startswith("-"):
                raise SnapshotDenied(f"ripgrep option {token!r} is not part of the SDK's search")
            if pattern is None and index < len(tokens) - 1:
                pattern = self._pattern(token)
                index += 1
                continue
            if target is not None or index != len(tokens) - 1:
                raise SnapshotDenied("ripgrep must name exactly one pattern and one search path")
            anchored = self.snapshot.anchored(token)
            if anchored is None:
                raise SnapshotDenied(f"invalid ripgrep search path: {token!r}")
            target = workspace_path(anchored)
            index += 1
        if pattern is None or target is None:
            raise SnapshotDenied("ripgrep must name exactly one pattern and one search path")
        return [*args, "-e", pattern], target

    def _option_value(self, flag: str, value: str) -> str:
        """Validate one value the SDK attaches to a ripgrep option."""
        if flag == "--sort":
            if value != "path":
                raise SnapshotDenied(f"unsupported ripgrep sort order: {value!r}")
            return value
        if flag == "--glob":
            names = [part for part in re.split(r"[\\/]+", value) if part]
            if not value or len(value) > MAX_GLOB_CHARS or "\x00" in value or ".." in names or os.path.isabs(value):
                raise SnapshotDenied(f"ripgrep glob is not a snapshot-relative pattern: {value!r}")
            return value
        if flag == "--type":
            if not RG_TYPE.fullmatch(value):
                raise SnapshotDenied(f"unsupported ripgrep file type: {value!r}")
            return value
        low, high = RG_INTEGERS[flag]
        try:
            number = int(value)
        except ValueError:
            raise SnapshotDenied(f"ripgrep option {flag!r} needs a number, not {value!r}") from None
        if not low <= number <= high:
            raise SnapshotDenied(f"ripgrep option {flag!r} is out of range: {value!r}")
        return value

    @staticmethod
    def _pattern(text: str) -> str:
        if not text or len(text) > MAX_PATTERN_CHARS or "\x00" in text:
            raise SnapshotDenied("empty or oversized search pattern")
        return text

    def _is_glob_helper(self, argv: list[str]) -> bool:
        """Whether *argv* is the SDK's Glob helper invocation, exactly as Glob issues it."""
        if len(argv) != 6 or argv[2] != "--pattern" or argv[4] != "--base-dir" or self._helper_root is None:
            return False
        interpreter, script = self.basename(argv[0]).lower(), argv[1]
        if interpreter not in INTERPRETERS and identity_key(Path(argv[0])) != identity_key(Path(sys.executable)):
            return False
        return self.basename(script) == GLOB_HELPER_NAME and is_within(workspace_path(script), self._helper_root)

    async def _run_glob_helper(self, argv: list[str], timeout: float) -> ExecResult:
        """Run the SDK's Glob helper in the requested snapshot directory and keep its registered matches."""
        try:
            pattern = self._glob_pattern(argv[3])
        except SnapshotDenied as exc:
            return self._blocked(str(exc))
        base = self.snapshot.directory(argv[5])
        if base is None:
            return self._blocked(f"Glob base directory is not in the registered snapshot: {argv[5]!r}")
        # The interpreter is this process's own: the SDK asks for ``python3`` on a
        # non-local backend, and that is not a name every host has.
        helper = workspace_path(argv[1])
        result, truncated = await self._spawn([sys.executable, str(helper), "--pattern", pattern, "--base-dir", str(base)], timeout, limit=MAX_GLOB_BYTES)
        if truncated:
            # A half-written array is not a path list; refuse instead of parsing it.
            return self._blocked("the Glob helper matched more paths than the snapshot backend returns")
        if not result.ok():
            return result
        try:
            matches = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return self._blocked("the Glob helper did not return the list of paths it matched")
        found, seen = [], set()
        for match in matches if isinstance(matches, list) else ():
            item = self.snapshot.registered(match) if isinstance(match, str) else None
            if item is None:
                continue
            path = str(self.snapshot.path_of(item))
            if path not in seen:
                seen.add(path)
                found.append(path)
        # Only paths the manifest produced are ever shown, so a name the helper walked
        # through — a link out of the snapshot, an unregistered file — is dropped here.
        return ExecResult(0, json.dumps(found).encode("utf-8"), b"")

    def _glob_pattern(self, pattern: str) -> str:
        """A Glob pattern that stays inside the base directory it is matched from."""
        text = self._pattern(pattern)
        parts = [part for part in re.split(r"[\\/]+", text) if part]
        if not parts or os.path.isabs(text) or ".." in parts:
            raise SnapshotDenied(f"Glob pattern is not relative to the snapshot: {pattern!r}")
        return text

    async def _spawn(self, argv: list[str], timeout: float, *, limit: int = MAX_STDOUT_BYTES) -> tuple[ExecResult, bool]:
        """Run *argv* in the snapshot root under a deadline, an output cap and a reaped child.

        Returns:
            (`ExecResult`, `bool`): the outcome, and whether more than *limit* bytes of
            standard output followed what was kept.
        """
        kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)} if os.name == "nt" else {}
        try:
            process = await asyncio.create_subprocess_exec(*argv, cwd=str(self.root), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs)
        except (OSError, NotImplementedError) as exc:
            return self._blocked(f"cannot start the search helper: {exc}"), False
        outgoing = asyncio.ensure_future(self._drain(process.stdout, limit))
        errors = asyncio.ensure_future(self._drain(process.stderr, MAX_STDERR_BYTES))
        waiting = asyncio.ensure_future(process.wait())
        cancelling = asyncio.ensure_future(self._watch_cancel())
        timed_out = cancelled = False
        try:
            done, _ = await asyncio.wait([waiting, cancelling], timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            timed_out, cancelled = waiting not in done, cancelling in done
        except asyncio.CancelledError:
            # The caller was cancelled: the child must not outlive this call.
            process.kill()
            cancelling.cancel()
            raise
        if timed_out or cancelled:
            process.kill()
        cancelling.cancel()
        await waiting                      # reaped before anything is returned
        stdout, truncated = await outgoing
        stderr = (await errors)[0]
        if cancelled:
            raise BudgetStopped("CANCELLED")
        if timed_out:
            return ExecResult(EXIT_TIMEOUT, b"", b"timed out"), False
        return ExecResult(process.returncode or 0, stdout, stderr), truncated

    async def _watch_cancel(self) -> None:
        """Return once the task's cancel flag is set.

        The flag is polled rather than awaited because the context outlives one event
        loop by design; an ``asyncio.Event`` would bind to the first loop that awaited it.
        """
        while not self.context.cancel_event.is_set():
            await asyncio.sleep(CANCEL_POLL_SECONDS)

    @staticmethod
    async def _drain(stream, limit: int) -> tuple[bytes, bool]:
        """Read *stream* to end of file, keeping the first *limit* bytes and saying if more followed."""
        if stream is None:
            return b"", False
        kept, size, truncated = [], 0, False
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                return b"".join(kept), truncated
            if size < limit:
                piece = chunk[: limit - size]
                kept.append(piece)
                size += len(piece)
                truncated = truncated or len(piece) < len(chunk)
            else:
                truncated = True
