import importlib
import json


def modules(): return importlib.import_module('evals.schema'), importlib.import_module('evals.run')


def test_eval_input_excludes_fix_and_new_regression_tests(tmp_path):
    schema, run = modules()
    case = schema.EvalCase('c', 'https://example.org/issue', 'hash', tmp_path / 'buggy', 'old', tmp_path / 'fixed-secret', 'new', 'python', 'python', 'empty input', forbidden_materials=('new regression test',))
    text = json.dumps(run.generation_input(case))
    assert 'fixed_repo' not in text and 'fixed-secret' not in text and 'new regression test' not in text


def test_blocked_cases_remain_in_total_denominator():
    schema, run = modules()
    results = (schema.EvalResult('a', environment_status='runnable', reproduced=True, human_judgement=True,
                                 export_replayed=True, fix_validation_status='passed'),
        schema.EvalResult('b', environment_status='blocked'), schema.EvalResult('c', environment_status='runnable', reproduced=True, human_judgement=False),
        schema.EvalResult('d', environment_status='runnable'))
    summary = run.summarize(results)
    assert summary['all_tasks'] == 4 and summary['runnable_tasks'] == 3
    assert summary['effective_reproductions'] == 1 and summary['false_positives'] == 1
    assert summary['total_rate'] == .25
    assert run.summarize(())['total_rate'] is None


def test_effective_reproduction_requires_the_fixed_version_to_pass():
    """The effective-success count is the differential one: 评测有效成功 needs the supplied
    fixed version to pass, so a repeated observation whose fix failed (or whose fix was never
    validated) is not counted, however well it was human-reviewed and independently replayed."""
    schema, run = modules()
    reviewed = {'environment_status':'runnable', 'reproduced':True, 'human_judgement':True, 'export_replayed':True}
    cases = [schema.EvalResult(name, fix_validation_status=status, **reviewed)
             for name, status in (('failed-fix','failed'), ('passed-fix','passed'),
                                  ('legacy','not_provided'), ('blocked-fix','blocked'))]
    summary = run.summarize(tuple(cases))
    assert summary['effective_reproductions'] == 1 and summary['total_rate'] == .25
    assert summary['differential_successes'] == 1 and summary['fix_validation_failed'] == 1
    assert run.summarize((cases[0],))['effective_reproductions'] == 0
    assert run.summarize((cases[3],))['effective_reproductions'] == 0


def test_unknown_cost_is_not_counted_as_zero():
    schema, run = modules()
    summary = run.summarize((schema.EvalResult('a', cost_kind='estimated', cost_value=.2), schema.EvalResult('b', cost_kind='unknown')))
    assert summary['costs_unknown_count'] == 1 and summary['cost_samples'] == [.2]


def test_same_model_environment_and_budget_are_recorded_for_comparison():
    schema, run = modules()
    result = schema.EvalResult('a', model='same', model_base_url='https://model.example/v1', budget={'agent_steps':20}, environment={'python':'3.12','pytest':'9.1.1'}, description_hash='issue-hash')
    from dataclasses import asdict
    data = asdict(result)
    assert data['model'] == 'same' and data['budget']['agent_steps'] == 20 and data['environment']['pytest'] == '9.1.1'
