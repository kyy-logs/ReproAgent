"""Standalone plugin: only pytest and Python's standard library are required."""
import ast
import json
import os
import re
import sys
import tokenize
import types
from functools import lru_cache
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


def origin(filename):
    """The resolved path of a frame's source file, or '' when it has no path at all.

    A tracer is handed frames the compiler named rather than wrote: attrs generates
    ``_pytest.fixtures.FixtureFunctionMarker.__init__`` and names that frame
    ``'<attrs generated init ...>'``, and ``'<string>'`` and ``'<frozen ...>'`` arrive the
    same way.  There is no file to vouch for, and on Python 3.9 ``Path.resolve()`` raises
    OSError for such a name -- Windows rejects the angle brackets -- which ended collection
    and cost the whole run, so a candidate that defined a fixture was ruled invalid before
    its test ever ran.  The call is still counted; only the origin is withheld, because
    provenance is exactly what a label cannot establish.
    """
    if not filename or filename.startswith('<'):
        return ''
    try:
        return str(Path(filename).resolve())
    except OSError:
        return ''


_cached_origin = lru_cache(maxsize=4096)(origin)


def profile(frame, event, arg):
    if event != 'call':
        return
    name = frame.f_globals.get('__name__', '')
    if any(name == target or name.startswith(target + '.') for target in _targets):
        source = _cached_origin(frame.f_code.co_filename)
        if source:
            _origins[name] = source
        _calls[name] = _calls.get(name, 0) + 1


def pytest_sessionstart(session):
    _cached_origin.cache_clear()
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
        kind = type(current)
        # Class equality may be user-defined by a metaclass; exact-type dispatch
        # must not execute that code while observing a failed assertion.
        if any(kind is builtin for builtin in (str, bytes, int, float, complex, bool, type(None))):
            continue
        identities.append(identity)
        if kind is list or kind is tuple:
            pending.extend(current[:64])
        elif kind is dict:
            from itertools import islice
            for key, child in islice(current.items(), 64):
                pending.extend((key, child))
        elif kind is set or kind is frozenset:
            from itertools import islice
            pending.extend(islice(current, 64))
    return identities


_HEX_ADDRESS = re.compile(r'0x[0-9a-fA-F]+')
_QUOTED = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' ''', re.VERBOSE)
_MAX_SOURCE_BYTES = 4 * 1024 * 1024
_MAX_DISPLAY_BYTES = 1024 * 1024


@lru_cache(maxsize=256)
def _plain_assert_at(filename, lineno):
    """Whether this exact line is an ``assert`` without a custom message.

    A rewritten temporary is only evidence for the assertion pytest rewrote.  A
    custom message means the displayed text is not solely pytest's explanation, and
    an unparseable, oversized or missing source gets no proof at all.
    """
    try:
        with tokenize.open(filename) as stream:
            source = stream.read(_MAX_SOURCE_BYTES + 1)
    except (OSError, SyntaxError, UnicodeError, LookupError):
        return False
    if len(source) > _MAX_SOURCE_BYTES:
        return False
    try:
        tree = ast.parse(source, filename=filename)
    except (SyntaxError, UnicodeError, ValueError):
        return False
    assertions = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Assert) and node.lineno == lineno]
    # Older supported Python versions give us a traceback line, not an exact
    # expression position. With multiple asserts there, no assertion can vouch
    # for another's display (especially a custom error message).
    return len(assertions) == 1 and assertions[0].msg is None


def _unquoted_addresses(text):
    quoted = [(match.start(), match.end()) for match in _QUOTED.finditer(text)]
    addresses = []
    for match in _HEX_ADDRESS.finditer(text):
        if any(start <= match.start() < end for start, end in quoted):
            continue
        try:
            address = int(match.group(0), 16)
        except ValueError:                         # pragma: no cover - regex guarantees hex
            continue
        if address > 0:
            addresses.append(address)
    return addresses


def assertion_display_identities(item, call, limit=64):
    """Return the rewritten temporaries that produced this failure display, or None.

    Pytest's rewritten ``assert`` keeps its intermediate values in ``@py_assert*``
    locals.  We record only identities that are actually present in the failure
    message, so an unrelated earlier assert cannot lend proof to this one.  Values
    are never repr'd, iterated, or otherwise executed; only ``id`` and the
    ``__self__`` of exact bound-method types are read.
    """
    excinfo = getattr(call, 'excinfo', None)
    value = getattr(excinfo, 'value', None)
    if value is None or type(value).__module__ + '.' + type(value).__qualname__ != 'builtins.AssertionError':
        return None
    traceback, frame, lineno = value.__traceback__, None, 0
    while traceback is not None:
        frame, lineno = traceback.tb_frame, traceback.tb_lineno
        traceback = traceback.tb_next
    if frame is None:
        return None
    module = frame.f_globals.get('__name__', '')
    if module.startswith(('_pytest.', 'pluggy.')):
        return None
    if not _plain_assert_at(frame.f_code.co_filename, lineno):
        return None
    values = []
    for key, temporary in frame.f_locals.items():
        if type(key) is str and key.startswith('@py_assert'):
            values.append(temporary)
            if len(values) >= 256:
                break
    if not values:
        return None
    candidates = set()
    for temporary in values:
        candidates.add(id(temporary))
        kind = type(temporary)
        if kind is types.MethodType or kind is types.BuiltinMethodType:
            bound = temporary.__self__
            if bound is not None:
                candidates.add(id(bound))
        elif any(kind is builtin for builtin in (list, tuple, dict, set, frozenset)):
            candidates.update(object_identities(temporary))
    display = str(value)
    if len(display) > _MAX_DISPLAY_BYTES:
        display = display[:_MAX_DISPLAY_BYTES]     # bounded; a proof past the cap is withheld
    addresses = _unquoted_addresses(display)
    observed = []
    for address in addresses:
        if address in candidates and address not in observed:
            observed.append(address)
    if not observed:
        return None
    if type(limit) is not int or limit <= 0:
        limit = 64
    return {'provenance': 'pytest_rewrite', 'object_ids': observed[:min(limit, 64)]}


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
    report._reproagent_assertion_display = assertion_display_identities(item, call)
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
        'assertion_display': getattr(report, '_reproagent_assertion_display', None),
        'crash': {'path': str(location.path), 'lineno': location.lineno, 'message': location.message} if location else {},
    })


def pytest_sessionfinish(session, exitstatus):
    for name, module in list(sys.modules.items()):
        if any(name == target or name.startswith(target + '.') for target in _targets):
            origin = getattr(module, '__file__', None)
            if origin:
                _origins[name] = str(Path(origin).resolve())
    emit('session_finish', {'exitstatus': int(exitstatus), 'target_origins': _origins, 'target_calls': _calls})
