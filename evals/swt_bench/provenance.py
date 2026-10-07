"""Pin the tool source a round ran from, so a changed tree cannot be compared."""
import subprocess
from pathlib import Path

import reproagent
from reproagent.core.serialization import bytes_hash, canonical_hash

CODE_TREES = ('src/reproagent', 'evals')
DATA_TREES = ('src/reproagent/prompts', 'src/reproagent/resources')
SECRET_MARKERS = ('secret', 'credential', '.env', '.key', '.pem', '.pfx')
DIRTY_LIMIT = 64


class ToolSourceChanged(Exception):
    """The recorded fingerprint no longer describes the source that would run."""


def _fingerprinted(path):
    if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
        return False
    name = path.name.lower()
    # Secret-bearing files are never read, let alone hashed into a receipt.
    return not any(marker in name for marker in SECRET_MARKERS)


def _covered_files(root):
    root = Path(root)
    found = []
    for base in CODE_TREES:
        directory = root / base
        if directory.is_dir(): found.extend(directory.rglob('*.py'))
    for base in DATA_TREES:
        directory = root / base
        if directory.is_dir(): found.extend(directory.rglob('*'))
    pyproject = root / 'pyproject.toml'
    if pyproject.is_file(): found.append(pyproject)
    covered = {path.relative_to(root).as_posix():path for path in found if path.is_file() and _fingerprinted(path)}
    return [(relative, covered[relative]) for relative in sorted(covered)]


def _fingerprint(root):
    files = {relative:bytes_hash(path.read_bytes()) for relative, path in _covered_files(root)}
    if not files: raise ValueError('tool source root has no covered files')
    return {'files':files, 'source_hash':canonical_hash(files)}


def _git(root, *arguments):
    return subprocess.run(['git','-C',str(root),*arguments], capture_output=True, text=True, encoding='utf-8',
        timeout=30, check=True).stdout


def _repository_state(root):
    """Commit and uncommitted paths; informational, and never used to invalidate a round."""
    try:
        commit = _git(root,'rev-parse','HEAD').strip()
        dirty = sorted(line[3:].strip() for line in _git(root,'status','--porcelain').splitlines() if line[3:].strip())
    except (OSError, subprocess.SubprocessError):
        return '', [], 0
    return commit, dirty[:DIRTY_LIMIT], len(dirty)


def capture_tool_source(root: Path) -> dict:
    """Fingerprint the code, prompts, resources, harness and configuration a round runs from."""
    root = Path(root).resolve()
    commit, dirty, dirty_count = _repository_state(root)
    return {**_fingerprint(root), 'git_commit':commit, 'git_dirty_paths':dirty, 'git_dirty_count':dirty_count,
            'imported_from':Path(reproagent.__file__).resolve().as_posix()}


def assert_tool_source(root: Path, receipt: dict) -> None:
    """Re-check a round's own fingerprint; only the covered files can invalidate it."""
    if not isinstance(receipt, dict) or not isinstance(receipt.get('files'), dict):
        raise ToolSourceChanged('this round recorded no tool source fingerprint')
    if _fingerprint(Path(root).resolve()) != {'files':receipt['files'], 'source_hash':receipt.get('source_hash')}:
        raise ToolSourceChanged('tool source changed since the round was recorded')
