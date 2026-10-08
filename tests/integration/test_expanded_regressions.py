"""Real pytest regressions exposed by the expanded historical evaluation."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from reproagent.core.models import BudgetLimits, TaskState
from tests.unit.test_verifier import prepare
from tests.unit.test_controller import ScriptedModel


def source_project(projects, source):
    def plain(root):
        repo = projects.plain(root)
        (repo / 'example/parser.py').write_text(source, encoding='utf-8')
        return repo
    return SimpleNamespace(plain=plain)


def test_same_assertion_with_address_bearing_object_repr_confirms_replay(tmp_path, projects, facts):
    project = source_project(projects,
        'class Tag:\n'
        '    def __repr__(self): return f"<cp311-cp311-win_amd64 @ {id(self)}>"\n'
        'def parse(values): return [Tag() for _ in range(12)]\n')
    contract, candidate, first, verifier, _, runner, snapshot, request = prepare(tmp_path, project, facts)
    from tests.integration.test_runner import execute
    second = execute(request, snapshot, candidate, runner, facts.context())
    verdicts = tuple(asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context())) for run in (first, second))
    first_failure = next(t for t in first.observation.tests if t['outcome'] == 'failed')
    second_failure = next(t for t in second.observation.tests if t['outcome'] == 'failed')
    assert '@ ' in first_failure['crash']['message']
    assert first_failure['crash']['message'] != second_failure['crash']['message']
    assert verifier.confirm(contract, candidate, first, second, verdicts)
    # Address normalization must still reject a change in the actual platform value.
    assert first_failure['assertion_comparison']['operator'] == '=='
    changed = tuple({**t, 'crash': {**t['crash'], 'message': t['crash']['message'].replace('win_amd64', 'linux_x86_64')},
                     'assertion_comparison': {**t['assertion_comparison'], 'left': t['assertion_comparison']['left'].replace('win_amd64', 'linux_x86_64')}}
                    if t['outcome'] == 'failed' else t for t in second.observation.tests)
    assert not verifier.confirm(contract, candidate, first, replace(second, observation=replace(second.observation, tests=changed)), verdicts)


def test_address_like_string_values_are_different_failures(tmp_path, projects, facts):
    project = source_project(projects, 'import os\ndef parse(values): return f"<Tag @ {os.getpid()}>"\n')
    contract, candidate, first, verifier, _, runner, snapshot, request = prepare(tmp_path, project, facts,
        text='from example.parser import parse\ndef test_string(): assert parse([]) == "expected"\n')
    from tests.integration.test_runner import execute
    second = execute(request, snapshot, candidate, runner, facts.context())
    verdicts = tuple(asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context())) for run in (first, second))
    assert not verifier.confirm(contract, candidate, first, second, verdicts)


def test_business_numbers_in_object_repr_are_different_failures(tmp_path, projects, facts):
    project = source_project(projects, 'import os\nclass Quote:\n    def __repr__(self): return f"<Quote @ {os.getpid()}>"\ndef parse(values): return Quote()\n')
    contract, candidate, first, verifier, _, runner, snapshot, request = prepare(tmp_path, project, facts)
    from tests.integration.test_runner import execute
    second = execute(request, snapshot, candidate, runner, facts.context())
    verdicts = tuple(asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context())) for run in (first, second))
    assert not verifier.confirm(contract, candidate, first, second, verdicts)


@pytest.mark.parametrize('source,test,eligible', [
    ('def parse(values): return compile("def broken(:", "<generated>", "exec")\n',
     'from example.parser import parse\ndef test_generated(): assert parse([]) == []\n', True),
    ('def parse(values): return []\n',
     'from example.parser import parse\ndef test_generated():\n    parse([])\n    compile("def broken(:", "<candidate>", "exec")\n', False),
    ('def parse(values): return []\n',
     'from example.parser import parse\ndef test_generated():\n    parse([])\n    assert False, "SyntaxError is the reported symptom"\n', True),
])
def test_runtime_syntax_error_is_attributed_to_its_innermost_frame(tmp_path, projects, facts, source, test, eligible):
    contract, candidate, run, verifier, _, runner, *_ = prepare(tmp_path, source_project(projects, source), facts, text=test)
    checks = runner.adapter.check_framework(candidate, run.observation)
    assert bool(not checks.invalid) is eligible
    failure = next(t for t in run.observation.tests if t['outcome'] == 'failed')
    assert failure['exception_type'] in ('builtins.SyntaxError', 'builtins.AssertionError')
    assert failure['exception_frames'][-1]['path'].endswith('parser.py' if eligible and 'compile' in source else 'test_repro.py')


def test_exploration_cannot_spend_the_actions_reserved_for_first_execution(tmp_path, projects, facts):
    """A phase may spend every exploration step, and the confirmation still happens.

    The action loop used to reserve four steps for write/run/submit and dropped read and
    search from the schema once they ran out, so the first execution could not be crowded
    out by exploration.  The phases are not the confirmation any more, which makes that
    reservation structural instead of a budget heuristic: executing, verifying and repeating
    a published candidate spend no exploration step, so phases that spent theirs on nothing
    actionable cannot starve the candidate the last one published.
    """
    from tests.unit.test_controller import controller_for, fail, publish, report
    plan = [fail(ValueError('the phase could not answer')),
            report('candidate-never-published'),
            publish()]
    request, controller, context = controller_for(tmp_path, projects, facts, ScriptedModel(),
                                                  BudgetLimits(agent_steps=3), plan)
    result = asyncio.run(controller.run(request, context))
    assert result.status == TaskState.DONE
    # Every exploration step was spent, two of them on a phase that produced nothing the
    # Controller could act on, and the candidate of the last phase was still executed and
    # repeated.
    assert context.budget.steps_used == 3
    assert len(list((request.output_dir / 'runs').glob('*/execution.json'))) == 2
