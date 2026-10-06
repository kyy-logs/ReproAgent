"""Standalone reproduction installer/runner, using only Python's stdlib."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def child(root, relative):
    if not relative or '\\' in relative or ':' in relative or any(p in ('', '.', '..') for p in relative.split('/')):
        raise ValueError('unsafe package path')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('package path escapes root')
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--python', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    for entry in manifest['files']:
        if hashlib.sha256(child(root, entry['path']).read_bytes()).hexdigest() != entry['content_hash']:
            raise ValueError('package file changed: ' + entry['path'])
    if report['package_kind'] != 'reproduction':
        raise ValueError('diagnostic package has no accepted reproduction')
    for entry in report['candidate_files']:
        destination = child(args.repo, entry['path'])
        content = child(root / 'candidate', entry['path']).read_bytes()
        if args.install:
            if destination.exists():
                raise ValueError('refusing to overwrite: ' + entry['path'])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        elif not destination.exists() or destination.read_bytes() != content:
            raise ValueError('candidate not installed; pass --install')
    args.output.mkdir(parents=True, exist_ok=False)
    env = {key: value for key, value in os.environ.items() if key.upper() in ('SYSTEMROOT', 'WINDIR', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'HOME', 'USERPROFILE', 'LANG')}
    env.update({'PYTHONPATH': os.pathsep.join([str(root / 'probe'), *[str(args.repo.resolve() if r == '.' else child(args.repo, r)) for r in report['source_roots']]]),
        'PYTEST_ADDOPTS':'', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1', 'PYTHONDONTWRITEBYTECODE':'1', 'REPROAGENT_RUN_ID':'export-replay',
        'REPROAGENT_PROBE_PATH':str(args.output.resolve() / 'probe.jsonl'), 'REPROAGENT_TARGET_MODULES':json.dumps(report['target_modules'])})
    argv = [str(args.python.resolve()), '-m', 'pytest', *report['pytest_args'], '-p', 'reproagent_pytest_probe', *report['selectors']]
    return subprocess.call(argv, cwd=args.repo.resolve(), env=env)


if __name__ == '__main__':
    sys.exit(main())
