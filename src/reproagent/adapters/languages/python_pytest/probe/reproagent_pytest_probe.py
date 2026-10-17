"""Standalone plugin: only pytest and Python's standard library are required."""
import json
import logging
import os
import sys
from functools import lru_cache
from pathlib import Path

import pytest

_seq = 0
_origins = {}
_calls = {}
_comparison = None
_targets = tuple(json.loads(os.environ.get('REPROAGENT_TARGET_MODULES', '[]')))
#: The paths the candidate published.  They are already in the copy when the test starts,
#: so naming them is the only way to tell which documents the candidate meant to add
#: apart from the ones the repository ships.
try:
    _candidate_files = tuple(json.loads(os.environ.get('REPROAGENT_CANDIDATE_FILES', '[]')))
except ValueError:
    _candidate_files = ()

#: How many distinct observations one test may record before the collection is declared
#: incomplete.  A truncated list must never be read as "nothing else happened".
FUNCTION_LIMIT = 256
#: Distinct functions one module may contribute.  A target is often a whole package, and
#: its test machinery runs before the code under test; without a per-module share the
#: fixtures would fill the budget and the module the report is about would never appear.
MODULE_FUNCTION_LIMIT = 16
DIAGNOSTIC_LIMIT = 64
_functions = {}
_module_functions = {}
_function_overflow = False
_diagnostics = []
_diagnostic_overflow = False


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
    global _function_overflow
    if event != 'call':
        return
    name = frame.f_globals.get('__name__', '')
    if any(name == target or name.startswith(target + '.') for target in _targets):
        source = _cached_origin(frame.f_code.co_filename)
        if source:
            _origins[name] = source
        _calls[name] = _calls.get(name, 0) + 1
        # Which function was entered, not merely that the module ran.  Python 3.9 has no
        # co_qualname, so the fallback is recorded as what it is: a plain name, which can
        # merge same-named functions.  The limit keeps one test from recording without end.
        function = getattr(frame.f_code, 'co_qualname', None) or getattr(frame.f_code, 'co_name', None)
        if function:
            key = name + '.' + function
            if key in _functions:
                _functions[key] += 1
            elif len(_functions) < FUNCTION_LIMIT and _module_functions.get(name, 0) < MODULE_FUNCTION_LIMIT:
                _functions[key] = 1
                _module_functions[name] = _module_functions.get(name, 0) + 1
            else:
                _function_overflow = True


class _Capture(logging.Handler):
    """Records the warnings one test emitted, with the line that emitted each one."""

    def __init__(self):
        super().__init__(level=logging.WARNING)

    def emit(self, record):
        global _diagnostic_overflow
        try:
            if len(_diagnostics) >= DIAGNOSTIC_LIMIT:
                _diagnostic_overflow = True
                return
            _diagnostics.append({'message': str(record.getMessage())[:512],
                'path': origin(record.pathname), 'lineno': int(record.lineno or 0),
                'level': str(record.levelname)})
        except Exception:
            pass          # a diagnostic capture may never end the test


def start_capture():
    """Begin recording this test's warnings; returns the handle :func:`stop_capture` takes."""
    global _diagnostics, _diagnostic_overflow
    _diagnostics, _diagnostic_overflow = [], False
    handler = _Capture()
    logging.getLogger().addHandler(handler)
    return handler


def stop_capture(handler):
    """Stop recording and return what was captured, plus whether the cap was exceeded."""
    logging.getLogger().removeHandler(handler)
    return tuple(_diagnostics), _diagnostic_overflow


def monitoring_status(inputs, diagnostics_overflow):
    """What each observation could establish, so an absence is never read as a fact.

    ``captured`` is the only value that permits "not reached" to be concluded.  A target
    that was never configured, or a collection that hit its cap, is ``not_configured`` or
    ``incomplete`` -- the run may simply not have been watching.
    """
    return {'inputs': inputs,
        'functions': 'not_configured' if not _targets else ('incomplete' if _function_overflow else 'captured'),
        'diagnostics': 'incomplete' if diagnostics_overflow else 'captured'}


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
    global _comparison, _functions, _module_functions, _diagnostics, _function_overflow, _diagnostic_overflow
    _comparison = None
    _functions, _module_functions, _function_overflow = {}, {}, False
    _diagnostics, _diagnostic_overflow = [], False
    previous = sys.getprofile()
    sys.setprofile(profile)
    handler = start_capture()
    try:
        yield
    finally:
        stop_capture(handler)
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


def assertion_context_ids(frame):
    """Observe only bounded pytest rewrite temporaries; never serialize locals."""
    local_values = frame.f_locals
    values = []
    for name in frame.f_code.co_varnames[:256]:
        if name.startswith('@py_assert') and name in local_values:
            values.append(local_values[name])
            if len(values) == 64:
                break
    return [identity for identity in object_identities(values) if identity != id(values)]


#: Suffixes a documentation tree uses for source documents.  A created file with one of
#: these that the testroot's ``source_suffix`` does not list cannot become a document, so
#: the build never reads it -- and whatever the test asserted about it is not evidence.
DOCUMENT_SUFFIXES = ('.rst', '.md', '.txt')


def _source_suffixes(config):
    """The suffixes *config* reads, whatever shape Sphinx was given them in."""
    raw = getattr(config, 'source_suffix', None)
    if isinstance(raw, dict):
        return {str(name) for name in raw}
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, (list, tuple)):
        return {str(name) for name in raw}
    return {'.rst'}              # Sphinx's own default when the testroot does not set it


def _sphinx_app(item):
    """The Sphinx application of this test, or None when it is not a Sphinx test."""
    for value in (getattr(item, 'funcargs', None) or {}).values():
        if hasattr(value, 'srcdir') and hasattr(value, 'config') and hasattr(value, 'env'):
            return value
    return None


def srcdir_listing(srcdir):
    """Every file under *srcdir*, as relative names, or None when it is not a directory."""
    try:
        srcdir = Path(srcdir)
    except TypeError:
        return None
    if not srcdir.is_dir():
        return None
    return {path.relative_to(srcdir).as_posix() for path in srcdir.rglob('*') if path.is_file()}


def sphinx_inputs(app, baseline):
    """Which files the test created and which of them the build never read.

    *baseline* is the source directory as it stood when the test body started, so what
    this reports is exactly what the test wrote -- not the testroot the repository ships
    and not the fixture's own build output.  Recorded as observed facts, never as a
    judgement.  ``None`` when no build ran, because then nothing was read and there is
    nothing to compare against.
    """
    srcdir, config, env = getattr(app, 'srcdir', None), getattr(app, 'config', None), getattr(app, 'env', None)
    if srcdir is None or config is None or baseline is None:
        return None
    try:
        srcdir = Path(srcdir)
    except TypeError:
        return None
    found = set(getattr(env, 'found_docs', ()) or ())
    if not found:
        return None
    suffixes = _source_suffixes(config)
    outdir = getattr(app, 'outdir', None)
    try:
        outdir = Path(outdir).resolve() if outdir is not None else None
    except (TypeError, OSError):
        outdir = None
    created = []
    for path in srcdir.rglob('*'):
        if not path.is_file():
            continue
        if outdir is not None and (path == outdir or outdir in path.parents):
            continue
        relative = path.relative_to(srcdir).as_posix()
        if relative in baseline or '__pycache__' in Path(relative).parts:
            continue
        created.append(relative)
    # Files the candidate published into this testroot are already in the copy before the
    # test body runs, so the before/after comparison cannot see them.  They are still
    # input the candidate added, and the build either read them or did not.
    prefix = 'tests/roots/' + srcdir.name + '/'
    for name in _candidate_files:
        if isinstance(name, str) and name.startswith(prefix):
            relative = name[len(prefix):]
            if relative not in created:
                created.append(relative)
    unread = [name for name in created if Path(name).suffix in DOCUMENT_SUFFIXES
              and Path(name).suffix not in suffixes]
    return {'testroot': srcdir.name, 'source_suffix': sorted(suffixes),
            'created': sorted(created), 'unread': sorted(unread)}


def pytest_runtest_call(item):
    """Capture the source directory before the test body can write into it."""
    application = _sphinx_app(item)
    if application is not None:
        item._reproagent_srcdir_baseline = srcdir_listing(getattr(application, 'srcdir', None))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    global _comparison
    outcome = yield
    report = outcome.get_result()
    exception_type, frames = '', []
    innermost_frame = None
    if call.excinfo is not None:
        value = call.excinfo.value
        exception_type = type(value).__module__ + '.' + type(value).__qualname__
        traceback = value.__traceback__
        while traceback is not None:
            frame = traceback.tb_frame
            innermost_frame = frame
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
    report._reproagent_assertion_context_object_ids = []
    if (exception_type == 'builtins.AssertionError' and innermost_frame is not None
            and report._reproagent_assertion_comparison is None):
        report._reproagent_assertion_context_object_ids = assertion_context_ids(innermost_frame)
    report._reproagent_sphinx_inputs = None
    report._reproagent_target_functions = dict(sorted(_functions.items()))
    report._reproagent_diagnostics = tuple(_diagnostics)
    report._reproagent_monitoring = None
    if report.when == 'call':
        application = _sphinx_app(item)
        inputs = 'not_configured'
        if application is not None:
            observed = sphinx_inputs(application, getattr(item, '_reproagent_srcdir_baseline', None))
            if observed is not None:
                report._reproagent_sphinx_inputs = observed
                inputs = 'captured'
            else:
                inputs = 'incomplete'
        report._reproagent_monitoring = monitoring_status(inputs, _diagnostic_overflow)
    _comparison = None


def captured_output(text, limit=4096):
    """Bound observed output, distinguishing an excerpt from a full transcript."""
    text = text if isinstance(text, str) else ''
    data = text.encode('utf-8', errors='replace')
    truncated = len(data) > limit
    if truncated:
        gap = '\n[...captured output omitted...]\n'
        half = (limit - len(gap.encode())) // 2
        text = data[:half].decode('utf-8', errors='ignore') + gap + data[-half:].decode('utf-8', errors='ignore')
    return {'text': text, 'original_bytes': len(data),
            'captured_bytes': len(text.encode('utf-8')), 'truncated': truncated,
            'excerpt_mode': 'head_tail' if truncated else 'complete'}


def pytest_runtest_logreport(report):
    location = getattr(report.longrepr, 'reprcrash', None)
    payload = {
        'nodeid': report.nodeid, 'when': report.when, 'outcome': report.outcome,
        'wasxfail': getattr(report, 'wasxfail', ''),
        'longrepr': str(report.longrepr) if report.failed else '',
        'exception_type': getattr(report, '_reproagent_exception_type', ''),
        'exception_frames': getattr(report, '_reproagent_exception_frames', []),
        'assertion_comparison': getattr(report, '_reproagent_assertion_comparison', None),
        'assertion_context_object_ids': getattr(report, '_reproagent_assertion_context_object_ids', []),
        'crash': {'path': str(location.path), 'lineno': location.lineno, 'message': location.message} if location else {},
    }
    if report.when == 'call' and report.outcome == 'passed':
        payload['captured_output'] = {
            'stdout': captured_output(getattr(report, 'capstdout', '')),
            'stderr': captured_output(getattr(report, 'capstderr', ''))}
    observed_inputs = getattr(report, '_reproagent_sphinx_inputs', None)
    if observed_inputs:
        payload['sphinx_inputs'] = observed_inputs
    observed_functions = getattr(report, '_reproagent_target_functions', None)
    if observed_functions:
        payload['target_functions'] = observed_functions
    observed_diagnostics = getattr(report, '_reproagent_diagnostics', None)
    if observed_diagnostics:
        payload['diagnostics'] = list(observed_diagnostics)
    observed_monitoring = getattr(report, '_reproagent_monitoring', None)
    if observed_monitoring:
        payload['monitoring'] = observed_monitoring
    emit('test_phase', payload)


def pytest_sessionfinish(session, exitstatus):
    for name, module in list(sys.modules.items()):
        if any(name == target or name.startswith(target + '.') for target in _targets):
            origin = getattr(module, '__file__', None)
            if origin:
                _origins[name] = str(Path(origin).resolve())
    emit('session_finish', {'exitstatus': int(exitstatus), 'target_origins': _origins, 'target_calls': _calls})
