"""Standalone plugin: only pytest and Python's standard library are required."""
import json
import os
import sys
from pathlib import Path

import pytest

_seq = 0
_origins = {}
_calls = {}
_comparison = None
_targets = tuple(json.loads(os.environ.get('REPROAGENT_TARGET_MODULES', '[]')))


def emit(event, payload):
    global _seq
    record = dict(probe_version=1, run_id=os.environ['REPROAGENT_RUN_ID'], seq=_seq, event=event, payload=payload)
    path = Path(os.environ['REPROAGENT_PROBE_PATH'])
    line = json.dumps(record, ensure_ascii=False) + '\n'
    limit = int(os.environ.get('REPROAGENT_PROBE_LIMIT', '33554432'))
    if (path.stat().st_size if path.exists() else 0) + len(line.encode()) > limit:
        # No fabricated closing marker: collector must reject the partial stream.
        os._exit(86)
    with open(path, 'a', encoding='utf-8') as stream:
        stream.write(line)
        stream.flush()
    _seq += 1


def profile(frame, event, arg):
    if event != 'call':
        return
    name = frame.f_globals.get('__name__', '')
    if any(name == target or name.startswith(target + '.') for target in _targets):
        _origins[name] = str(Path(frame.f_code.co_filename).resolve())
        _calls[name] = _calls.get(name, 0) + 1


def pytest_sessionstart(session):
    Path(os.environ['REPROAGENT_PROBE_PATH']).write_text('', encoding='utf-8')
    emit('session_start', {'python': sys.version, 'pytest': pytest.__version__})


@pytest.hookimpl(hookwrapper=True)
def pytest_collection(session):
    previous = sys.getprofile()
    sys.setprofile(profile)
    try:
        yield
    finally:
        sys.setprofile(previous)


def pytest_collectreport(report):
    emit('collection', {'nodeid': report.nodeid, 'outcome': report.outcome, 'longrepr': str(report.longrepr) if report.failed else ''})


def pytest_collection_finish(session):
    emit('collected', {'nodeids': [item.nodeid for item in session.items]})


def pytest_deselected(items):
    emit('deselected', {'nodeids':[item.nodeid for item in items]})


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    global _comparison
    _comparison = None
    previous = sys.getprofile()
    sys.setprofile(profile)
    try:
        yield
    finally:
        sys.setprofile(previous)


def object_identities(value):
    # Traverse exact built-in containers only; do not invoke user iterators or
    # descriptors. A partial bounded index leaves unknown repr values strict.
    pending, seen, identities = [value], set(), []
    while pending and len(seen) < 256 and len(identities) < 64:
        current = pending.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        if type(current) in (str, bytes, int, float, complex, bool, type(None)):
            continue
        identities.append(identity)
        if type(current) in (list, tuple):
            pending.extend(current[:64])
        elif type(current) is dict:
            from itertools import islice
            for key, child in islice(current.items(), 64):
                pending.extend((key, child))
        elif type(current) in (set, frozenset):
            from itertools import islice
            pending.extend(islice(current, 64))
    return identities


def pytest_assertrepr_compare(config, op, left, right):
    global _comparison
    _comparison = None
    # Observe operands without overriding pytest's explanation. Its abbreviated
    # display can end in a different object's address in each process.
    frame = sys._getframe(1)
    while frame is not None:
        module = frame.f_globals.get('__name__', '')
        if module != __name__ and not module.startswith(('_pytest.', 'pluggy.')):
            break
        frame = frame.f_back
    if frame is None:
        return
    try:
        operands = {'operator': op, 'left': repr(left), 'right': repr(right)}
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return
    if any(len(value.encode('utf-8', errors='replace')) > 8192 for value in operands.values()):
        return
    operands['left_object_ids'] = object_identities(left)
    operands['right_object_ids'] = object_identities(right)
    _comparison = (str(Path(frame.f_code.co_filename).resolve()), frame.f_lineno, operands)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    global _comparison
    outcome = yield
    report = outcome.get_result()
    exception_type, frames = '', []
    if call.excinfo is not None:
        value = call.excinfo.value
        exception_type = type(value).__module__ + '.' + type(value).__qualname__
        traceback = value.__traceback__
        while traceback is not None:
            frame = traceback.tb_frame
            frames.append({'path': str(Path(frame.f_code.co_filename).resolve()),
                           'lineno': traceback.tb_lineno, 'module': frame.f_globals.get('__name__', '')})
            traceback = traceback.tb_next
    report._reproagent_exception_type = exception_type
    # Preserve the innermost frames needed for attribution without repeating a
    # whole framework stack in every phase's bounded semantic context.
    report._reproagent_exception_frames = frames[-8:]
    report._reproagent_assertion_comparison = None
    if exception_type == 'builtins.AssertionError' and frames and _comparison is not None:
        path, lineno, operands = _comparison
        if frames[-1]['path'] == path and frames[-1]['lineno'] == lineno:
            report._reproagent_assertion_comparison = operands
    _comparison = None


def pytest_runtest_logreport(report):
    location = getattr(report.longrepr, 'reprcrash', None)
    emit('test_phase', {
        'nodeid': report.nodeid, 'when': report.when, 'outcome': report.outcome,
        'wasxfail': getattr(report, 'wasxfail', ''),
        'longrepr': str(report.longrepr) if report.failed else '',
        'exception_type': getattr(report, '_reproagent_exception_type', ''),
        'exception_frames': getattr(report, '_reproagent_exception_frames', []),
        'assertion_comparison': getattr(report, '_reproagent_assertion_comparison', None),
        'crash': {'path': str(location.path), 'lineno': location.lineno, 'message': location.message} if location else {},
    })


def pytest_sessionfinish(session, exitstatus):
    for name, module in list(sys.modules.items()):
        if any(name == target or name.startswith(target + '.') for target in _targets):
            origin = getattr(module, '__file__', None)
            if origin:
                _origins[name] = str(Path(origin).resolve())
    emit('session_finish', {'exitstatus': int(exitstatus), 'target_origins': _origins, 'target_calls': _calls})
