"""Standalone reproduction installer/runner, using only Python's stdlib."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

MAX_PATH = 260
RESERVED_NAME = 12
PROCESS_CWD_LIMIT = 258


def long_path(path, *, directory=False):
    """Absolute long-path-safe form, mirroring ``reproagent.paths.workspace_path``.

    The reader may unpack this package deeper than the legacy Windows limit, and
    this file runs standalone: it must not import reproagent.  Interpreter paths
    do not come through here -- see the argv note in ``main``.
    """
    path = Path(path)
    if os.name != 'nt':
        return path
    text = os.path.abspath(path)
    limit = MAX_PATH - RESERVED_NAME if directory else MAX_PATH
    if text.startswith('\\\\?\\') or len(text) < limit:
        return Path(text)
    if text.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + text[2:])
    return Path('\\\\?\\' + text)


def directory_path(path):
    """Windows reserves name space inside a directory before the file limit."""
    return long_path(path, directory=True)


def location_key(path):
    """Key for the location a path names, in either long-path form.

    Mirrors ``reproagent.paths.identity_key``; this file runs standalone.  It is
    used for the containment check only, never to build the path that is opened.
    """
    text = os.path.realpath(path)
    if text.startswith('\\\\?\\UNC\\'):
        text = '\\\\' + text[8:]
    elif text.startswith('\\\\?\\'):
        text = text[4:]
    return os.path.normcase(text)


def child(root, relative):
    if not relative or '\\' in relative or ':' in relative or any(p in ('', '.', '..') for p in relative.split('/')):
        raise ValueError('unsafe package path')
    # Resolve the entry itself: a link inside the package (or the destination
    # repository) must not let a package file reach outside its root.  The
    # comparison is made in one representation because the two names can differ
    # in long-path form, not because the check was relaxed.
    path = os.path.realpath(os.path.join(os.path.realpath(root), *relative.split('/')))
    if not Path(location_key(path)).is_relative_to(Path(location_key(root))):
        raise ValueError('package path escapes root')
    return long_path(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--python', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    root = long_path(Path(__file__).resolve().parent)
    report = json.loads(long_path(root / 'report.json').read_text(encoding='utf-8'))
    manifest = json.loads(long_path(root / 'manifest.json').read_text(encoding='utf-8'))
    for entry in manifest['files']:
        if hashlib.sha256(child(root, entry['path']).read_bytes()).hexdigest() != entry['content_hash']:
            raise ValueError('package file changed: ' + entry['path'])
    if report['package_kind'] != 'reproduction':
        raise ValueError('diagnostic package has no accepted reproduction')
    repo = directory_path(args.repo.resolve())
    if os.name == 'nt' and len(location_key(repo)) > PROCESS_CWD_LIMIT:
        raise ValueError('PROCESS_CWD_TOO_LONG: Windows cannot start pytest in this repository; use a shorter repository path')
    for entry in report['candidate_files']:
        destination = child(args.repo, entry['path'])
        content = child(root / 'candidate', entry['path']).read_bytes()
        if args.install:
            if destination.exists():
                raise ValueError('refusing to overwrite: ' + entry['path'])
            directory_path(destination.parent).mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        elif not destination.exists() or destination.read_bytes() != content:
            raise ValueError('candidate not installed; pass --install')
    output = directory_path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    env = {key: value for key, value in os.environ.items() if key.upper() in ('SYSTEMROOT', 'WINDIR', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'HOME', 'USERPROFILE', 'LANG')}
    env.update({'PYTHONPATH': os.pathsep.join([str(long_path(root / 'probe')), *[str(long_path(args.repo.resolve() if r == '.' else child(args.repo, r))) for r in report['source_roots']]]),
        'PYTEST_ADDOPTS':'', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1', 'PYTHONDONTWRITEBYTECODE':'1', 'REPROAGENT_RUN_ID':'export-replay',
        'REPROAGENT_PROBE_PATH':str(long_path(output / 'probe.jsonl')), 'REPROAGENT_TARGET_MODULES':json.dumps(report['target_modules'])})
    # Absolute, but not link-resolved: resolving a venv's interpreter would run
    # the base interpreter and drop that venv's packages.
    argv = [os.path.abspath(args.python), '-m', 'pytest', *report['pytest_args'], '-p', 'reproagent_pytest_probe', *report['selectors']]
    return subprocess.call(argv, cwd=repo, env=env)


if __name__ == '__main__':
    sys.exit(main())
