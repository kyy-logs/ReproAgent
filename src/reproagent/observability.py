"""A bounded, task-local trace of one reproduction run.

The recorder answers *how* a run spent its time and its budget; it never re-decides
what the run concluded.  It therefore keeps a whitelist of small metadata facts --
purpose, tool, controlled result code, ids, attempt numbers -- and never prose,
source or tool payloads.  HTTP, token and cost are charged exactly once, on the
leaf span that reached the wire, because the logical call and its retries would
otherwise each look like their own spend.

Everything here is observation only.  A fault inside the recorder is contained and
reported as an incomplete trace: it never replaces the domain exception travelling
through the span, and it never touches a budget or a deadline.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from contextvars import ContextVar
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = 1

#: A trace is a diagnostic aid, so it must never grow without bound and must never
#: be able to fail the task it describes.
MAX_SPANS = 1024
MAX_ATTRIBUTE_BYTES = 2048

#: The only attribute keys a span may carry.  Anything else is dropped rather than
#: redacted, so a new field cannot start leaking by accident.
ALLOWED_ATTRIBUTES = frozenset({
    "purpose", "tool", "result_code", "execution_role",
    "candidate_id", "run_id", "contract_id",
    "attempt", "output_limit", "budget", "usage", "cost", "unknown",
})

REDACTED = "<redacted>"

#: The spans the summary treats specially: only the wire attempt is charged.
LOGICAL_SPAN = "model.logical"
HTTP_ATTEMPT_SPAN = "model.http_attempt"

WARNING_SPAN_CAP = "trace span cap reached; later observations were dropped"
WARNING_ATTRIBUTE_TOO_LARGE = "an observation attribute exceeded the per-entry byte ceiling and was dropped"
WARNING_OBSERVATION_FAILED = "an observation fault occurred; the trace is incomplete"

TOKEN_KEYS = ("input_tokens", "output_tokens")

_recorder: ContextVar["TraceRecorder | None"] = ContextVar("reproagent_trace_recorder", default=None)
_current_span: ContextVar[str | None] = ContextVar("reproagent_trace_span", default=None)


class _NoopHandle:
    """What a span becomes while tracing is off: no clock, no state, no file."""

    span_id = ""

    def annotate(self, **_fields: Any) -> None:
        return None


class SpanHandle:
    """The writable end of one span, handed to the code being observed."""

    def __init__(self, recorder: "TraceRecorder", entry: dict, started_clock: float):
        self._recorder = recorder
        self._entry = entry
        self._started_clock = started_clock
        self._finished = False
        self.span_id: str = entry["span_id"]

    def annotate(self, **fields: Any) -> None:
        self._recorder._annotate(self._entry, fields)

    def _finish(self, status: str) -> None:
        if self._finished:
            return
        self._finished = True
        self._recorder._close(self._entry, self._started_clock, status)


class TraceRecorder:
    """Collects the spans of one task in memory, under fixed ceilings.

    Entering the recorder installs it as the current one for this context, so the
    module-level :func:`span` and :func:`mark` can reach it without threading a
    handle through every call.  Two tasks in two contexts never see each other.
    """

    def __init__(self, *, clock=time.monotonic, wall_clock=time.time, secrets: Iterable[str] = ()):
        self._clock = clock
        self._wall = wall_clock
        self._secrets = tuple(secret for secret in secrets if secret)
        self.trace_id = uuid.uuid4().hex
        self._spans: list[dict] = []
        self._warnings: list[str] = []
        self._partial = False
        self._failed = False
        self._active = False
        self._started_clock: float | None = None
        self._started_wall: float | None = None
        self._token = None

    def __enter__(self) -> "TraceRecorder":
        self._started_clock = self._clock()
        self._started_wall = self._wall()
        self._token = _recorder.set(self)
        self._active = True
        return self

    def __exit__(self, *_exc) -> bool:
        self._active = False
        if self._token is not None:
            _recorder.reset(self._token)
            self._token = None
        return False

    # -- observation ---------------------------------------------------------

    def _warn(self, message: str) -> None:
        if message not in self._warnings:
            self._warnings.append(message)

    def _fault(self) -> None:
        self._failed = True
        self._warn(WARNING_OBSERVATION_FAILED)

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, REDACTED)
            return value
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._redact(item) for item in value]
        return value

    def _annotate(self, entry: dict, fields: dict) -> None:
        try:
            for key, raw in fields.items():
                if key not in ALLOWED_ATTRIBUTES:
                    continue
                value = self._redact(raw)
                try:
                    encoded = json.dumps({key: value}, ensure_ascii=False).encode("utf-8")
                except (TypeError, ValueError):
                    # A value the whitelist admits but cannot be written down.
                    self._fault()
                    continue
                if len(encoded) > MAX_ATTRIBUTE_BYTES:
                    # Whole or not at all: a truncated fact would read as complete.
                    self._warn(WARNING_ATTRIBUTE_TOO_LARGE)
                    self._partial = True
                    continue
                entry["attributes"][key] = value
        except Exception:  # noqa: BLE001 - observation never escapes
            self._fault()

    def _new_entry(self, name: str, kind: str) -> dict:
        entry = {
            "span_id": uuid.uuid4().hex[:16],
            "parent_span_id": _current_span.get(),
            "name": name,
            "kind": kind,
            "offset_seconds": None,
            "duration_seconds": None,
            "status": "ok" if kind == "event" else "incomplete",
            "attributes": {},
        }
        try:
            if self._started_wall is not None:
                entry["offset_seconds"] = max(0.0, self._wall() - self._started_wall)
        except Exception:  # noqa: BLE001 - observation never escapes
            self._fault()
        return entry

    def _append(self, entry: dict) -> None:
        if len(self._spans) >= MAX_SPANS:
            self._warn(WARNING_SPAN_CAP)
            self._partial = True
            return
        self._spans.append(entry)

    def _begin(self, name: str, attributes: dict | None) -> SpanHandle:
        entry = self._new_entry(name, "span")
        try:
            started = self._clock()
        except Exception:  # noqa: BLE001 - observation never escapes
            self._fault()
            started = 0.0
        handle = SpanHandle(self, entry, started)
        if attributes:
            handle.annotate(**attributes)
        return handle

    def _close(self, entry: dict, started_clock: float, status: str) -> None:
        try:
            entry["duration_seconds"] = max(0.0, self._clock() - started_clock)
            entry["status"] = status
            self._append(entry)
        except Exception:  # noqa: BLE001 - observation never escapes
            self._fault()

    def _note_event(self, name: str, attributes: dict | None) -> None:
        try:
            entry = self._new_entry(name, "event")
            if attributes:
                self._annotate(entry, attributes)
            self._append(entry)
        except Exception:  # noqa: BLE001 - observation never escapes
            self._fault()

    # -- output --------------------------------------------------------------

    def finish(self, *, task_id: str, status: str, main_duration: float,
               learning: dict | None = None) -> dict:
        """Freeze the collected spans into a document.

        ``status`` is copied from the task result, not inferred here: the trace
        describes an outcome it never decides.
        """
        total = 0.0
        try:
            if self._started_clock is not None:
                total = max(0.0, self._clock() - self._started_clock)
        except Exception:  # noqa: BLE001 - observation never escapes
            self._fault()
        return {
            "schema_version": SCHEMA_VERSION,
            "trace_id": self.trace_id,
            "task_id": task_id,
            "status": status,
            "main_duration": main_duration,
            "total_duration": total,
            "partial": self._partial,
            "metrics_complete": not (self._partial or self._failed),
            "warnings": list(self._warnings),
            "spans": self._spans,
            "summary": self._summarise(learning),
        }

    def _summarise(self, learning: dict | None) -> dict:
        leaves = [entry for entry in self._spans if entry["name"] == HTTP_ATTEMPT_SPAN]
        return {
            "span_count": len(self._spans),
            "logical_calls": sum(1 for entry in self._spans if entry["name"] == LOGICAL_SPAN),
            "http_attempts": len(leaves),
            "usage": _sum_tokens(leaves),
            "cost": _sum_cost(leaves),
            "learning": learning,
        }


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


def tracing_enabled() -> bool:
    recorder = _recorder.get()
    return recorder is not None and recorder._active


@contextlib.contextmanager
def trace_session(*, enabled: bool, secrets: Iterable[str] = ()) -> Iterator[TraceRecorder | None]:
    """Install a recorder for this task, or nothing at all when tracing is off."""
    if not enabled:
        yield None
        return
    with TraceRecorder(secrets=secrets) as recorder:
        yield recorder


@contextlib.contextmanager
def span(name: str, *, attributes: dict | None = None) -> Iterator[Any]:
    """Time a region; the body's exception still wins."""
    recorder = _recorder.get()
    if recorder is None or not recorder._active:
        yield _NoopHandle()
        return
    handle = recorder._begin(name, attributes)
    token = _current_span.set(handle.span_id)
    try:
        yield handle
    except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised unchanged
        handle._finish("cancelled" if isinstance(exc, asyncio.CancelledError) else "error")
        raise
    else:
        handle._finish("ok")
    finally:
        _current_span.reset(token)


def mark(name: str, *, attributes: dict | None = None) -> None:
    """Record a point event -- something that happened, with no duration."""
    recorder = _recorder.get()
    if recorder is None or not recorder._active:
        return
    recorder._note_event(name, attributes)
