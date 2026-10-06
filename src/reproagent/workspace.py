from __future__ import annotations

import shutil
import uuid
from dataclasses import replace
from pathlib import Path

from .core.models import Candidate, CandidateDraft, CodeSnapshot, FileEntry, ProtectionCheck, RunWorkspace
from .core.serialization import bytes_hash, canonical_hash, encode_record
from .paths import identity_key, is_within, workspace_path
from .store import TaskStore, atomic_write, safe_child

EXCLUDED_NAMES = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}


def sensitive_path(path: str) -> bool:
    name = Path(path).name.lower()
    return name == ".env" or name.startswith(".env.") or name in {"credentials.json", "id_rsa", "id_ed25519"} or name.endswith((".pem", ".key"))


def inventory(root: Path, exclusions: tuple[Path, ...] = (), context=None) -> dict[str, bytes]:
    root = workspace_path(root)
    excluded = tuple(Path(excluded).resolve() for excluded in exclusions)
    result = {}
    def walk(directory, prefix, ancestors):
        key = identity_key(directory)
        if not is_within(directory, root):
            raise ValueError(f"link escapes outside repository: {directory}")
        if key in ancestors:
            raise ValueError(f"cyclic repository link: {directory}")
        for path in sorted(directory.iterdir()):
            if context:
                context.budget.check()
            # An entry can be deeper than its root, so each one is represented on
            # its own before it is stat-ed, read or compared.
            path = workspace_path(path)
            real = path.resolve()
            if any(is_within(real, item) for item in excluded):
                continue
            relative = f"{prefix}/{path.name}" if prefix else path.name
            if path.name in EXCLUDED_NAMES or sensitive_path(relative):
                continue
            if not is_within(real, root):
                raise ValueError(f"link escapes outside repository: {relative}")
            if path.is_dir():
                walk(path, relative, ancestors | {key})
            elif path.is_file():
                result[relative] = path.read_bytes()
    walk(root, "", set())
    return result


def candidate_hash(candidate: Candidate) -> str:
    payload = encode_record(candidate)
    payload.pop("manifest_hash")
    payload.pop("storage_root")
    return canonical_hash(payload)


class Workspace:
    def __init__(self, task_root: Path, store: TaskStore):
        self.root = workspace_path(Path(task_root).resolve())
        self.store = store
        self.mutable_paths = (".pytest_cache", "__pycache__")
        self.candidate_parent = 'tests'

    def freeze(self, request, context) -> CodeSnapshot:
        repo = workspace_path(request.repo.resolve())
        if not repo.is_dir():
            raise ValueError(f"repository directory does not exist: {repo}")
        output = workspace_path(request.output_dir.resolve())
        if output == repo or repo.is_relative_to(output):
            raise ValueError("output directory cannot be the repository or its ancestor")
        exclusions = (self.root, output)
        for _ in range(2):
            contents = inventory(repo, exclusions, context)
            hashes = {name: bytes_hash(data) for name, data in contents.items()}
            after = inventory(repo, exclusions, context)
            if hashes == {name: bytes_hash(data) for name, data in after.items()}:
                break
        else:
            raise ValueError("repository changed during snapshot creation")
        self.mutable_paths = request.language.mutable_paths
        self.candidate_parent = request.language.candidate_parent
        for relative in self.mutable_paths:
            safe_child(repo, relative)
            if any(name == relative or name.startswith(relative + "/") for name in contents):
                raise ValueError(f"mutable path overlaps protected files: {relative}")
        snapshot_id = "snapshot-" + uuid.uuid4().hex
        root = workspace_path(self.root / "snapshots" / snapshot_id / "code")
        files = []
        for name, content in contents.items():
            context.budget.check()
            atomic_write(safe_child(root, name), content)
            files.append(FileEntry(name, hashes[name], len(content), "source"))
        manifest_hash = canonical_hash({"files": [{"path": f.path, "content_hash": f.content_hash, "size": f.size} for f in files]})
        snapshot = CodeSnapshot(snapshot_id, root, manifest_hash, tuple(files), tuple(sorted(EXCLUDED_NAMES)))
        ref = self.store.save_record("snapshots", snapshot_id, snapshot)
        self.store.append_event("snapshot.created", (ref.path,), {"snapshot_id": snapshot_id})
        return snapshot

    def publish(self, draft: CandidateDraft, snapshot: CodeSnapshot) -> Candidate:
        if draft.snapshot_id != snapshot.snapshot_id:
            raise ValueError("candidate snapshot mismatch")
        if not draft.files or not any(f.role == "test" for f in draft.files):
            raise ValueError("candidate requires a test file")
        candidate_id = draft.candidate_id or "candidate-" + uuid.uuid4().hex
        manifest_path = self.store.record_path("candidates", candidate_id)
        if manifest_path.exists():
            raise ValueError("immutable candidate ID already published")
        protected = {f.path for f in snapshot.files}
        names = set()
        entries = []
        for draft_file in draft.files:
            safe_child(snapshot.root, draft_file.path)
            if not draft_file.path.startswith(self.candidate_parent.rstrip('/') + '/'):
                raise ValueError('candidate files must remain in configured test area')
            if Path(draft_file.path).name.lower() in {'conftest.py', 'pytest.ini', 'pyproject.toml', 'setup.cfg', 'setup.py', 'tox.ini', 'sitecustomize.py', 'usercustomize.py', 'requirements.txt', 'pipfile', 'poetry.lock'} or draft_file.path.endswith('.pth'):
                raise ValueError('candidate cannot add configuration/plugins/dependency files')
            if draft_file.path in protected or draft_file.path in names or sensitive_path(draft_file.path):
                raise ValueError(f"candidate would overwrite a protected/duplicate/sensitive file: {draft_file.path}")
            if draft_file.role not in ("test", "data"):
                raise ValueError("unsupported candidate file role")
            if draft_file.role == "test" and not (Path(draft_file.path).name.startswith("test_") and draft_file.path.endswith(".py")):
                raise ValueError("test file must use test_*.py name")
            names.add(draft_file.path)
            entries.append(FileEntry(draft_file.path, bytes_hash(draft_file.content), len(draft_file.content), draft_file.role))
        selectors = draft.selectors or tuple(f.path for f in entries if f.role == "test")
        for selector in selectors:
            if selector.split("::", 1)[0] not in names:
                raise ValueError("selector must reference a published candidate file")
        storage = workspace_path(manifest_path.parent / "files")
        candidate = Candidate(candidate_id, snapshot.snapshot_id, draft.contract_id, draft.contract_version, tuple(entries), storage, "", draft.hypothesis, draft.parent_id, draft.language_id, selectors, draft.run_options, draft.expectation_sources, draft.fixture_refs, draft.preconditions)
        candidate = replace(candidate, manifest_hash=candidate_hash(candidate))
        for draft_file in draft.files:
            atomic_write(safe_child(storage, draft_file.path), draft_file.content)
        ref = self.store.save_record("candidates", candidate_id, candidate)
        self.store.append_event("candidate.published", (ref.path,), {"candidate_id": candidate_id, "manifest_hash": candidate.manifest_hash})
        return candidate

    def validate_candidate(self, candidate: Candidate):
        if candidate_hash(candidate) != candidate.manifest_hash:
            raise ValueError("candidate manifest hash mismatch")
        for entry in candidate.files:
            path = safe_child(candidate.storage_root, entry.path)
            if not path.is_file() or bytes_hash(path.read_bytes()) != entry.content_hash:
                raise ValueError(f"candidate file hash mismatch: {entry.path}")

    def fresh_run(self, snapshot: CodeSnapshot, candidate: Candidate | None = None, context=None) -> RunWorkspace:
        if candidate:
            self.validate_candidate(candidate)
        run_id = "run-" + uuid.uuid4().hex
        root = workspace_path(self.root / "runs" / run_id / "code")
        root.mkdir(parents=True)
        for entry in snapshot.files:
            if context: context.budget.check()
            data = safe_child(snapshot.root, entry.path).read_bytes()
            if bytes_hash(data) != entry.content_hash:
                raise ValueError("frozen snapshot has changed")
            atomic_write(safe_child(root, entry.path), data)
        for entry in candidate.files if candidate else ():
            if context: context.budget.check()
            destination = safe_child(root, entry.path)
            if destination.exists():
                raise ValueError("candidate would overwrite fixed/snapshot file")
            atomic_write(destination, safe_child(candidate.storage_root, entry.path).read_bytes())
        temp_root = workspace_path(root.parent / "tmp")
        temp_root.mkdir()
        return RunWorkspace(run_id, root, temp_root, snapshot.snapshot_id, candidate.candidate_id if candidate else '')

    def check(self, run: RunWorkspace, snapshot: CodeSnapshot, candidate: Candidate, context=None) -> ProtectionCheck:
        actual = inventory(run.root, context=context)
        baseline = {f.path: f.content_hash for f in snapshot.files}
        candidate_files = {f.path: f.content_hash for f in candidate.files}
        modified = tuple(name for name, digest in baseline.items() if name in actual and bytes_hash(actual[name]) != digest)
        deleted = tuple(name for name in baseline if name not in actual)
        added = tuple(name for name in actual if name not in baseline and name not in candidate_files and not any(name == allowed or name.startswith(allowed + "/") for allowed in self.mutable_paths))
        candidate_ok = all(name in actual and bytes_hash(actual[name]) == digest for name, digest in candidate_files.items())
        return ProtectionCheck(modified, deleted, added, candidate_ok)
