"""Bounded setup observations; diagnosis does not assign root-cause responsibility."""
from pathlib import Path

from .review_context import RUN_ROOT, _root_spellings


def setup_failure_details(run, *, task_root, secrets=()):
    roots = {str(Path(task_root) / 'runs' / run.run_id)}
    recorded = run.observation.framework_details.get('run_root')
    if isinstance(recorded, str) and recorded:
        roots.add(recorded)

    def clean(value, *, tail=False):
        text = value if isinstance(value, str) else ''
        for secret in secrets:
            if secret:
                text = text.replace(secret, '[REDACTED]')
        for spelling in sorted({s for root in roots for s in _root_spellings(root)}, key=len, reverse=True):
            text = text.replace(spelling, RUN_ROOT)
        return text[-512:] if tail else text[:512]

    rows = []
    for item in run.observation.tests:
        if not isinstance(item, dict) or item.get('when') != 'setup' or item.get('outcome') != 'failed':
            continue
        crash = item.get('crash')
        message = crash.get('message') if isinstance(crash, dict) else None
        if not isinstance(message, str) or not message:
            message = clean(item.get('longrepr'), tail=True)
        else:
            message = clean(message)
        if not message:
            continue
        rows.append({'nodeid':clean(item.get('nodeid')),
                     'exception_type':clean(item.get('exception_type')), 'message':message})
    if not rows:
        return {}
    return {'setup_failures':rows[:4], 'omitted_setup_failures':max(0, len(rows)-4)}
