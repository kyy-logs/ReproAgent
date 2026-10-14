"""Derive an explicitly modified diagnostic script; never edit a pinned harness.

Only the recorded SWT Sphinx unit-test layout is supported. Execute the output
only in a fresh disposable container with /testbed in its writable layer.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path


def repair_script(text: str, base_commit: str) -> str:
    """Keep frozen patch bodies intact, preserve test exit, verify tracked rollback."""
    if not re.fullmatch(r'[0-9a-f]{40}', base_commit):
        raise ValueError('full base commit required')
    header = '#!/bin/bash\nset -uxo pipefail\n'
    snapshot = f'git diff HEAD {base_commit} >> /root/pre_state.patch\n'
    suffix = f'git checkout {base_commit}\ngit apply /root/pre_state.patch\n'
    if (not text.startswith(header) or text.count(snapshot) != 1 or not text.endswith(suffix)
            or '\ncd /testbed\n' not in text):
        raise ValueError('unsupported pinned Sphinx script layout')
    body, separator, command = text[:-len(suffix)].rstrip('\n').rpartition('\n')
    if not separator or not re.fullmatch(r'tox -epy39 -v -- [A-Za-z0-9_./: -]+', command):
        raise ValueError('unsupported pinned Sphinx test command')
    body = body.replace(header, '#!/bin/bash\nset -euxo pipefail\n', 1)
    body = body.replace(snapshot,
        f'test "$(git rev-parse HEAD)" = {base_commit}\n'
        'git diff --binary HEAD > /root/pre_state.patch\n', 1)
    return (body + '\n'
        f'if {command}; then\n    test_status=0\nelse\n    test_status=$?\nfi\n'
        # /testbed must belong to the disposable container, never a host bind mount.
        'cd /testbed\n'
        'if (\n'
        f'    git reset --hard {base_commit} &&\n'
        '    if [ -s /root/pre_state.patch ]; then git apply /root/pre_state.patch; fi &&\n'
        '    git diff --binary HEAD > /root/restored_state.patch &&\n'
        '    cmp /root/pre_state.patch /root/restored_state.patch\n'
        '); then\n    printf "REPROAGENT_DIAGNOSTIC test_exit=%s rollback=ok\\n" "$test_status"\n'
        'else\n    echo ROLLBACK_FAILED >&2\n    exit 125\nfi\n'
        'exit "$test_status"\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-commit', required=True)
    args = parser.parse_args()
    original = args.input.read_bytes()
    derived = repair_script(original.decode('utf-8'), args.base_commit).encode('utf-8')
    # Exclusive output means an original or an earlier diagnostic cannot be overwritten.
    with args.output.open('xb') as output:
        output.write(derived)
    print(json.dumps({'kind': 'modified-environment-diagnostic-not-official-score',
        'input_sha256': hashlib.sha256(original).hexdigest(),
        'output_sha256': hashlib.sha256(derived).hexdigest()}))


if __name__ == '__main__':
    main()
