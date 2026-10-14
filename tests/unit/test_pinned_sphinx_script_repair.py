"""A diagnostic derivative must preserve patches and refuse other script layouts."""
import pytest


BASE = 'a' * 40
PATCH = "git apply -v - <<'EOF_114329324912'\n+frozen patch bytes\nEOF_114329324912\n"


def script():
    return ('#!/bin/bash\nset -uxo pipefail\ncd /testbed\n'
        f'git diff HEAD {BASE} >> /root/pre_state.patch\n'
        + PATCH + 'tox -epy39 -v -- tests/test_repro.py\n'
        f'git checkout {BASE}\ngit apply /root/pre_state.patch\n')


def test_diagnostic_derivative_keeps_patches_and_test_exit_separate_from_cleanup():
    from evals.repairs.pinned_sphinx_script import repair_script
    result = repair_script(script(), BASE)
    assert PATCH in result
    assert f'test "$(git rev-parse HEAD)" = {BASE}' in result
    assert 'git diff --binary HEAD > /root/pre_state.patch' in result
    assert 'if tox -epy39 -v -- tests/test_repro.py; then' in result
    assert 'test_status=$?' in result
    assert f'git reset --hard {BASE}' in result
    assert 'if [ -s /root/pre_state.patch ]; then' in result
    assert 'cmp /root/pre_state.patch /root/restored_state.patch' in result
    assert 'ROLLBACK_FAILED' in result
    assert result.endswith('exit "$test_status"\n')


@pytest.mark.parametrize('change', [
    lambda s: s.replace('tox -epy39 -v -- ', 'pytest '),
    lambda s: s.replace(' >> /root/pre_state.patch', ' > /root/pre_state.patch'),
    lambda s: s + 'echo later\n',
    lambda s: s.replace('cd /testbed', 'cd /other'),
    lambda s: s.replace('set -uxo pipefail', 'set -euxo pipefail'),
])
def test_unknown_or_already_modified_layout_is_refused(change):
    from evals.repairs.pinned_sphinx_script import repair_script
    with pytest.raises(ValueError):
        repair_script(change(script()), BASE)


def test_commit_must_be_a_full_hash():
    from evals.repairs.pinned_sphinx_script import repair_script
    with pytest.raises(ValueError):
        repair_script(script(), 'HEAD; echo untrusted')
