"""Program-observed test exceptions, separate from semantic reviewer claims."""
from .execution_diagnostics import diagnose_runtime
from .setup_diagnostics import call_failure_details


def execution_observation(verdict, store, *, secrets=()):
    if not verdict.run_ids:
        return None
    try:
        run = store.load_record('runs',verdict.run_ids[-1])
    except (OSError,ValueError,UnicodeError):
        return None  # Legacy/unavailable records cannot become new execution facts.
    if (run.run_id != verdict.run_ids[-1] or run.execution_role != 'original'
            or any(getattr(run,key)!=getattr(verdict,key) for key in
                   ('candidate_id','manifest_hash','contract_id','contract_version'))
            or diagnose_runtime(run)[0] is not None):
        return None
    indices = tuple(dict.fromkeys(row['test_index'] for issue in verdict.issues
        for row in issue.measurements.get('failure_sizes',())))[:4]
    details = call_failure_details(run,task_root=store.root,secrets=secrets,prioritized_indices=indices)
    return {'origin':'program_observation','run_id':run.run_id,**details} if details else None


def valid_execution_observation(value):
    if (not isinstance(value,dict) or set(value)!={'origin','run_id','failed_calls','omitted_failed_calls'}
            or value['origin']!='program_observation' or type(value['run_id']) is not str
            or not 1<=len(value['run_id'])<=128 or type(value['omitted_failed_calls']) is not int
            or value['omitted_failed_calls']<0):
        return False
    rows = value['failed_calls']
    return (isinstance(rows,(tuple,list)) and 1<=len(rows)<=4 and all(
        isinstance(row,dict) and set(row)=={'test_index','nodeid','exception_type','message','path','lineno'}
        and type(row['test_index']) is int and row['test_index']>=0
        and (row['lineno'] is None or type(row['lineno']) is int and row['lineno']>0)
        and all(type(row[key]) is str and len(row[key])<=512 for key in ('nodeid','exception_type','message','path'))
        for row in rows))
