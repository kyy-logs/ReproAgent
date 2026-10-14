import asyncio
from dataclasses import replace

from tests.integration.test_runner import execute
from tests.unit.test_verifier import prepare


def test_owned_external_temp_is_normalized_without_erasing_different_filenames(tmp_path, projects, facts):
    contract, candidate, first, verifier, _, runner, snapshot, request = prepare(tmp_path, projects, facts)
    second = execute(request, snapshot, candidate, runner, facts.context())
    verdicts = tuple(asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context())) for run in (first, second))

    def with_temp(run, root, name='same.txt'):
        phases = tuple(dict(test, assertion_comparison=None,
            crash={'path': 'pathlib.py', 'lineno': 1, 'message': f"FileNotFoundError: '{root}/{name}'"})
            if test['outcome'] == 'failed' else test for test in run.observation.tests)
        details = dict(run.observation.framework_details, temp_root=root)
        return replace(run, observation=replace(run.observation, tests=phases, framework_details=details))

    one = with_temp(first, str(tmp_path / 'owned-one'))
    two = with_temp(second, str(tmp_path / 'owned-two'))
    assert verifier.confirm(contract, candidate, one, two, verdicts)
    assert not verifier.confirm(contract, candidate, one,
        with_temp(second, str(tmp_path / 'owned-two'), 'different.txt'), verdicts)
