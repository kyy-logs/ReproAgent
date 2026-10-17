"""Closed, program-observed replay facts; never a semantic tie-breaker."""

EXECUTION_CHECKS = frozenset({'independent_bound_integral_runs', 'preconditions_repeatable',
    'test_inventory_matches', 'target_failure_present', 'failure_signature_matches'})


def valid_replay_observation(value):
    if not isinstance(value, dict) or set(value) != {'origin', 'kind', 'first_run_id', 'second_run_id',
            'execution_consistent', 'execution_unmet_checks', 'semantic_confirmed', 'verdict_classes'}:
        return False
    if (value['origin'] != 'program_observation'
            or value['kind'] not in ('semantic_disagreement', 'execution_unconfirmed', 'verdict_unconfirmed', 'confirmed')
            or any(type(value[k]) is not str or not 1 <= len(value[k]) <= 128 for k in ('first_run_id', 'second_run_id'))
            or any(type(value[k]) is not bool for k in ('execution_consistent', 'semantic_confirmed'))):
        return False
    checks, classes = value['execution_unmet_checks'], value['verdict_classes']
    if not (isinstance(checks, (list, tuple)) and len(checks) <= 5
            and all(type(c) is str and c in EXECUTION_CHECKS for c in checks)
            and isinstance(classes, (list, tuple)) and len(classes) == 2
            and all(type(c) is str and c in {'REPRODUCED','NOT_REPRODUCED','INVALID_CANDIDATE',
                'ENVIRONMENT_BLOCKED','UNCLASSIFIED'} for c in classes)):
        return False
    if value['semantic_confirmed'] and list(classes) != ['REPRODUCED', 'REPRODUCED']:
        return False
    if value['execution_consistent'] != (not checks):
        return False
    if value['kind'] == 'semantic_disagreement':
        return value['execution_consistent'] and not value['semantic_confirmed'] and classes[0] == 'REPRODUCED' and classes[1] != 'REPRODUCED'
    if value['kind'] == 'execution_unconfirmed':
        return not value['execution_consistent']
    if value['kind'] == 'confirmed':
        return value['execution_consistent'] and value['semantic_confirmed'] and list(classes) == ['REPRODUCED', 'REPRODUCED']
    return value['execution_consistent'] and not value['semantic_confirmed']
