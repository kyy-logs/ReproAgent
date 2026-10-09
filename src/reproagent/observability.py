"""Stable observability facade over OpenTelemetry and bounded local projection."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterable, Iterator

from .observability_content import ContentStore

SCHEMA_VERSION = 2

#: A trace is a diagnostic aid, so it must never grow without bound and must never
#: be able to fail the task it describes.
MAX_SPANS = 1024
MAX_ATTRIBUTE_BYTES = 2048
#: The whole stored file, metadata and captured content together -- the ceilings above
#: bound the parts, this one bounds their sum, and it is what the usage guide states.
MAX_DOCUMENT_BYTES = 6 << 20
MAX_HTML_BYTES = 8 << 20
#: The viewer's own input bound.  A stored trace is an untrusted file -- hand-editable,
#: and convertible from another sink -- so it is refused by size before it is ever read.
#: It matches what the writer will produce, so a file written here can be read back.
MAX_READ_BYTES = 6 << 20

#: Where a traced task keeps its trace, relative to the task's own output directory.
TRACE_DIRECTORY = "observability"
TRACE_FILENAME = "trace.json"
TRACE_HTML_FILENAME = "trace.html"
TEMPLATE_MARKER = "<!--TRACE-CONTENT-->"
UNKNOWN_TEXT = "unknown"
WARNING_DOCUMENT_TOO_LARGE = "the trace exceeded the document ceiling and was written marked incomplete"
WARNING_PAGE_TOO_LARGE = "the rendered page exceeded the viewer ceiling and was refused"
WARNING_TRACE_PATH_UNSAFE = "the trace destination is not a plain task-local directory and was refused"

#: The only attribute keys a span may carry.  Anything else is dropped rather than
#: redacted, so a new field cannot start leaking by accident.
ALLOWED_ATTRIBUTES = frozenset({
    "purpose", "tool", "tool_call_key", "tool_set", "result_code", "execution_role",
    "candidate_id", "run_id", "contract_id", "contract_version", "check", "check_source",
    "permission_result", "error_category", "classification_source",
    "checks", "reason_origin", "exit_code", "stop_reason", "cleanup_ok", "health",
    "attempt", "output_limit", "budget", "usage", "cost", "unknown",
})

#: The one capture mode this version has: metadata plus the content it can copy.
CAPTURE_MODE = "content"

#: The span a tool call belongs to, when no phase span is open for it.
ROOT_SCOPE = "root"

REDACTED = "<redacted>"

#: The spans the summary treats specially: only the wire attempt is charged.
LOGICAL_SPAN = "model.logical"
HTTP_ATTEMPT_SPAN = "model.http_attempt"

WARNING_SPAN_CAP = "trace span cap reached; later observations were dropped"
WARNING_ATTRIBUTE_TOO_LARGE = "an observation attribute exceeded the per-entry byte ceiling and was dropped"
WARNING_OBSERVATION_FAILED = "an observation fault occurred; the trace is incomplete"

TOKEN_KEYS = ("input_tokens", "output_tokens")

_recorder: ContextVar["TraceRecorder | None"] = ContextVar("reproagent_trace_recorder", default=None)


class _NoopHandle:
    """What a span becomes while tracing is off: no clock, no state, no file."""

    span_id = ""

    def annotate(self, **_fields: Any) -> None:
        return None

    def capture(self, *_args: Any, **_kwargs: Any) -> None:
        return None


#: One handle is enough: it holds no state, and tracing off must not allocate per span.
_NOOP_HANDLE = _NoopHandle()


class SpanHandle:
    """A bounded local view of an actual OTel span or point."""
    def __init__(self, recorder, entry, otel_span=None):
        self._recorder, self._entry, self._otel_span = recorder, entry, otel_span
        self.span_id = entry["span_id"]
    def annotate(self, **fields):
        self._recorder._annotate(self._entry, fields)
    def capture(self, kind, value, *, source, availability="captured"):
        return self._recorder._capture(self._entry, kind, value, source=source, availability=availability)

class TraceRecorder:
    """Compatibility facade; OTel owns all IDs and parent contexts."""
    def __init__(self, *, clock=time.monotonic, wall_clock=time.time, secrets=()):
        from .otel_projection import TaskTraceSink
        self._sink=TaskTraceSink(clock=clock,wall_clock=wall_clock,secrets=secrets)
    def __getattr__(self, key):
        return getattr(self._sink,key)
    def __enter__(self):
        from .otel_backend import open_task_trace
        self._cm=open_task_trace(self._sink)
        self._cm.__enter__()
        return self
    def __exit__(self,*exc):
        return self._cm.__exit__(*exc)
    def finish(self, **kwargs):
        return self._sink.finish(**kwargs)

def _active_sink():
    sink=_recorder.get()
    return sink if sink is not None and sink._active and not sink._frozen else None

def tracing_enabled():
    return _active_sink() is not None

@contextlib.contextmanager
def trace_session(*, enabled=True, secrets=()):
    if not enabled:
        token=_recorder.set(None)
        try: yield None
        finally: _recorder.reset(token)
        return
    try:
        recorder=TraceRecorder(secrets=secrets)
        recorder.__enter__()
    except Exception:
        import sys
        print("ReproAgent tracing is unavailable; the task will continue without tracing.", file=sys.stderr)
        token=_recorder.set(None)
        try: yield None
        finally: _recorder.reset(token)
        return
    try:
        yield recorder
    finally:
        recorder.__exit__(None,None,None)

def current_handle():
    sink=_active_sink()
    if sink is None: return _NOOP_HANDLE
    entry=sink.current_entry()
    if entry is None: return _NOOP_HANDLE
    return SpanHandle(sink,entry)

@contextlib.contextmanager
def span(name, *, attributes=None, expected=()):
    sink=_active_sink()
    if sink is None:
        yield _NOOP_HANDLE
        return
    from opentelemetry import trace, context
    from .otel_backend import context_owner
    owner=context_owner()
    active=None;token=None
    try:
        active=trace.get_tracer("reproagent").start_span(name)
        token=context.attach(trace.set_span_in_context(active))
        handle=current_handle()
        handle.annotate(**(attributes or {}))
    except Exception:
        sink._fault()
        handle=_NOOP_HANDLE
    try:
        yield handle
    except BaseException as exc:
        if handle is not _NOOP_HANDLE:
            handle._entry['_display_status']='cancelled' if isinstance(exc,asyncio.CancelledError) else (
                'ok' if isinstance(exc,GeneratorExit) or (expected and isinstance(exc,expected)) else 'error')
            from .otel_projection import classify_error
            handle.annotate(**classify_error(exc))
        raise
    else:
        if handle is not _NOOP_HANDLE: handle._entry['_display_status']='ok'
    finally:
        if active is not None:
            try: active.end()
            except Exception: sink._fault()
        if token is not None and context_owner()==owner:
            try: context.detach(token)
            except (ValueError,RuntimeError): sink._fault()

def capture(kind,value,*,source,availability="captured"):
    return current_handle().capture(kind,value,source=source,availability=availability)

def mark(name, *, attributes=None):
    sink=_active_sink()
    if sink is None: return _NOOP_HANDLE
    try:
        entry=sink.note_event(name,attributes)
        return SpanHandle(sink,entry) if entry is not None else _NOOP_HANDLE
    except Exception:
        sink._fault()
        return _NOOP_HANDLE

def record_check(name,value,*,source="program_check"):
    mark("check."+name,attributes={"check":name,"result_code":"PASS" if value else "FAIL","check_source":source})
    return value

def tool_call_key(*,sdk_call_id,step_index):
    sink=_active_sink()
    return sink._tool_call_key(sdk_call_id,step_index) if sink is not None else None

def _sum_tokens(spans: list[dict]) -> dict:
    totals: dict[str, Any] = {key: None for key in TOKEN_KEYS}
    complete = True
    for entry in spans:
        usage = entry["attributes"].get("usage")
        if not isinstance(usage, dict):
            complete = False
            continue
        for key in TOKEN_KEYS:
            value = usage.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                complete = False
                continue
            totals[key] = value if totals[key] is None else totals[key] + value
    if any(totals[key] is None for key in TOKEN_KEYS):
        complete = False
    return {**totals, "complete": complete}


def _sum_cost(spans: list[dict]) -> dict:
    amount = None
    complete = bool(spans)
    for entry in spans:
        value = entry["attributes"].get("cost")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            complete = False
            continue
        amount = value if amount is None else amount + value
    if amount is None:
        complete = False
    return {"amount": amount, "complete": complete}


def _encode_document(document: dict) -> bytes:
    return json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _plain_destination(path: Path) -> bool:
    """Whether this path is a real file or directory rather than a link to elsewhere.

    ``is_symlink`` alone is not enough: on Windows a *directory junction* is not a
    symlink, needs no elevation to create, and would quietly move "the task directory"
    somewhere else -- including into the delivered artifacts tree.
    """
    if path.is_symlink():
        return False
    try:
        stats = path.lstat()
    except OSError:
        # A name that does not exist yet cannot be a link; the writer creates it.
        return True
    return not getattr(stats, "st_reparse_tag", 0)


def _trace_destination(task_dir: Path, filename: str) -> Path:
    """The one path a task's trace may occupy, or the reason it may not.

    Both names are fixed, and both are checked: the directory the trace lives in and
    the file itself may be neither a link nor a location outside the task, and the
    trace never joins the delivered package.

    Raises:
        ValueError: the destination is a link, is outside the task, or is under artifacts.
    """
    from .paths import is_within, workspace_path

    root = workspace_path(Path(task_dir))
    directory = workspace_path(root / TRACE_DIRECTORY)
    target = workspace_path(directory / filename)
    artifacts = workspace_path(root / "artifacts")
    if not _plain_destination(directory) or not _plain_destination(target):
        raise ValueError(WARNING_TRACE_PATH_UNSAFE)
    if not is_within(directory, root) or not is_within(target, root):
        raise ValueError(WARNING_TRACE_PATH_UNSAFE)
    if is_within(target, artifacts):
        raise ValueError(WARNING_TRACE_PATH_UNSAFE)
    return target


def write_trace(task_dir: Path, document: dict) -> Path:
    """Write one task's trace beside the task, atomically, or refuse.

    The trace is a local diagnostic: it belongs to the task's own output directory and
    never to the sealed package.  A document over the ceiling is written *marked*
    incomplete rather than silently kept whole, and a destination that is a link -- so
    that "the task directory" would mean somewhere else -- is refused outright.

    Raises:
        ValueError: the destination is not inside the task directory, or is a link.
        OSError: the task directory could not be written.
    """
    from .store import atomic_write

    target = _trace_destination(task_dir, TRACE_FILENAME)
    encoded = _encode_document(document)
    if len(encoded) > MAX_DOCUMENT_BYTES:
        # Marked in place, not on a copy: the caller renders the same object to the page,
        # and a page that claimed completeness while the JSON beside it said otherwise
        # would make the two files disagree about the same run.
        document.update(partial=True, metrics_complete=False,
                        warnings=[*document.get("warnings", ()), WARNING_DOCUMENT_TOO_LARGE])
        encoded = _encode_document(document)
    atomic_write(target, encoded)
    return target
