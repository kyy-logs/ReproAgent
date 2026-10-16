"""Bounded setup observations; diagnosis does not assign root-cause responsibility."""
import json
from pathlib import Path

from .review_context import RUN_ROOT, _root_spellings


def _failure_details(run, *, task_root, secrets=(), when="setup", prioritized_indices=()):
    roots = {str(Path(task_root) / 'runs' / run.run_id)}
    recorded = run.observation.framework_details.get('run_root')
    if isinstance(recorded, str) and recorded:
        roots.add(recorded)

    # repr(filename) chooses its quotes using the whole filename, not the secret.
    def python_body(secret, quote):
        return ''.join(('\\' + char) if char == quote else repr(char)[1:-1] for char in secret)

    forms = {form for secret in secrets if isinstance(secret,str) and secret for form in
             (secret, python_body(secret, "'"), python_body(secret, '"'),
              json.dumps(secret,ensure_ascii=False)[1:-1], json.dumps(secret,ensure_ascii=True)[1:-1])}

    def clean(value, *, tail=False, excerpt=True):
        text = value if isinstance(value, str) else ''
        for form in sorted(forms,key=len,reverse=True):
            text = text.replace(form, '[REDACTED]')
        for spelling in sorted({s for root in roots for s in _root_spellings(root)}, key=len, reverse=True):
            text = text.replace(spelling, RUN_ROOT)
        return (text[-512:] if tail else text[:512]) if excerpt else text

    rows = []
    for index, item in enumerate(run.observation.tests):
        if not isinstance(item, dict) or item.get('when') != when or item.get('outcome') != 'failed':
            continue
        crash = item.get('crash')
        message = crash.get('message') if isinstance(crash, dict) else None
        if not isinstance(message, str) or not message:
            # Pytest fixture reports put the actual error before a potentially long
            # fixture inventory. Redact before selecting lines, so extraction cannot
            # split an escaped/multiline secret before redaction has seen it.
            report = clean(item.get('longrepr'), excerpt=False)
            error_lines = [line for line in report.splitlines()
                           if line.startswith('E') and line[1:2].isspace()] if when == 'setup' else []
            message = '\n'.join(error_lines)[:512] if error_lines else report[-512:]
        else:
            message = clean(message)
        if not message:
            continue
        row = {'nodeid':clean(item.get('nodeid')), 'exception_type':clean(item.get('exception_type')), 'message':message}
        if when == 'call':
            crash = crash if isinstance(crash,dict) else {}
            line = crash.get('lineno')
            row.update(test_index=index, path=clean(crash.get('path')),
                       lineno=line if type(line) is int and line>0 else None)
        rows.append(row)
    if not rows:
        return {}
    if when == 'call':
        priority = {index:rank for rank,index in enumerate(prioritized_indices)}
        rows.sort(key=lambda row:priority.get(row['test_index'],len(priority)))
        return {'failed_calls':rows[:4], 'omitted_failed_calls':max(0,len(rows)-4)}
    return {'setup_failures':rows[:4], 'omitted_setup_failures':max(0, len(rows)-4)}


def setup_failure_details(run, *, task_root, secrets=()):
    return _failure_details(run,task_root=task_root,secrets=secrets)


def call_failure_details(run, *, task_root, secrets=(), prioritized_indices=()):
    if len(prioritized_indices)>4 or any(type(i) is not int or i<0 for i in prioritized_indices):
        raise ValueError('invalid prioritized test indices')
    return _failure_details(run,task_root=task_root,secrets=secrets,when='call',prioritized_indices=prioritized_indices)
