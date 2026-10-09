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
import hashlib
import html
import json
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterable, Iterator

from .observability_content import ContentStore

SCHEMA_VERSION = 1

#: A trace is a diagnostic aid, so it must never grow without bound and must never
#: be able to fail the task it describes.
MAX_SPANS = 1024
MAX_ATTRIBUTE_BYTES = 2048
MAX_DOCUMENT_BYTES = 1 << 20
MAX_HTML_BYTES = 4 << 20
#: The viewer's own input bound.  The writer *targets* one megabyte and marks anything
#: past it incomplete, but a stored trace is an untrusted file -- hand-editable, and
#: convertible from another sink -- so it is refused by size before it is ever read.
MAX_READ_BYTES = 8 << 20

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
    "permission_result",
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
_current_span: ContextVar[str | None] = ContextVar("reproagent_trace_span", default=None)


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
    """The writable end of one span, handed to the code being observed."""

    def __init__(self, recorder: "TraceRecorder", entry: dict, started_clock: float):
        self._recorder = recorder
        self._entry = entry
        self._started_clock = started_clock
        self._finished = False
        self.span_id: str = entry["span_id"]

    def annotate(self, **fields: Any) -> None:
        self._recorder._annotate(self._entry, fields)

    def capture(self, kind: str, value: Any, *, source: str,
                availability: str = "captured") -> str | None:
        """Copy one piece of content for this span.

        Returns:
            The content id, or None when nothing could be stored -- in which case the
            span's content status says why rather than leaving the silence unexplained.
        """
        return self._recorder._capture(self._entry, kind, value, source=source,
                                       availability=availability)

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
        self._content = ContentStore(secrets=self._secrets)
        self._spans: list[dict] = []
        self._span_ids: set[str] = set()
        self._open: list[tuple[str, str]] = []
        self._entries: dict[str, dict] = {}
        self._warnings: list[str] = []
        self._partial = False
        self._failed = False
        self._active = False
        self._frozen = False
        self._started_clock: float | None = None
        self._started_wall: float | None = None
        self._started_at: float | None = None
        self._token = None

    def __enter__(self) -> "TraceRecorder":
        self._started_clock = self._clock()
        self._started_wall = self._wall()
        # The wall clock is kept for the header only: ordering and budgets stay monotonic.
        self._started_at = time.time()
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
        if self._frozen:
            return
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

    def _owns(self, span_id: str) -> bool:
        """Whether this recorder wrote that span, so it can be cited as a parent."""
        return span_id in self._span_ids

    def _new_entry(self, name: str, kind: str) -> dict:
        parent = _current_span.get()
        if parent is not None and not self._owns(parent):
            # A span left open by another recorder would be a parent this document does
            # not contain: a dangling edge no reader could resolve.
            parent = None
        entry = {
            "span_id": uuid.uuid4().hex[:16],
            "parent_span_id": parent,
            "name": name,
            "kind": kind,
            "offset_seconds": None,
            "duration_seconds": None,
            "status": "ok" if kind == "event" else "incomplete",
            "attributes": {},
            # None until a capture is attempted: "nothing was asked for" is not the same
            # claim as any of the reasons a capture can be missing.
            "content_refs": [],
            "content_status": None,
        }
        self._span_ids.add(entry["span_id"])
        # Open entries are reachable by id before they close, so a hook deep inside a
        # call can attach content to the span that is running.
        self._entries[entry["span_id"]] = entry
        if kind == "span":
            self._open.append((entry["span_id"], name))
        try:
            if self._started_wall is not None:
                entry["offset_seconds"] = max(0.0, self._wall() - self._started_wall)
        except Exception:  # noqa: BLE001 - observation never escapes
            self._fault()
        return entry

    def _append(self, entry: dict) -> None:
        if self._frozen:
            # The document has been handed out; a span that closes afterwards must not
            # add a row its own summary never counted.
            return
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
            span_id = entry["span_id"]
            self._open = [item for item in self._open if item[0] != span_id]
            self._append(entry)
        except Exception:  # noqa: BLE001 - observation never escapes
            self._fault()

    # -- content and correlation ---------------------------------------------

    def _capture(self, entry: dict, kind: str, value: Any, *, source: str,
                 availability: str = "captured") -> str | None:
        """Copy one piece of content, attributing it to the span that produced it."""
        content_id = self._content.capture(owner_span_id=entry["span_id"], kind=kind, value=value,
                                           source=source, availability=availability)
        if content_id is not None:
            entry["content_refs"].append(content_id)
        else:
            availability = "omitted_limit"
        # The first capture decides the span's status: a later successful copy must not
        # overwrite the record of something that was missing.
        if entry["content_status"] is None:
            entry["content_status"] = availability
        elif availability in ("omitted_limit", "capture_error"):
            entry["content_status"] = availability
        return content_id

    def _explore_scope(self) -> str:
        for span_id, name in reversed(self._open):
            if name == "explore":
                return span_id
        return self._spans[0]["span_id"] if self._spans else ROOT_SCOPE

    def _tool_call_key(self, sdk_call_id: str, step_index: int) -> str:
        """A stable key joining one call's permission with its execution.

        Derived and one-way: a provider that reuses one call id across steps still gets
        distinct keys, and the raw SDK id is not what is written down.
        """
        material = f"{self.trace_id}|{self._explore_scope()}|{step_index}|{sdk_call_id}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]

    def _note_event(self, name: str, attributes: dict | None) -> SpanHandle:
        try:
            entry = self._new_entry(name, "event")
            if attributes:
                self._annotate(entry, attributes)
            self._append(entry)
            handle = SpanHandle(self, entry, 0.0)
            handle._finished = True      # a point event has no end to record later
            return handle
        except Exception:  # noqa: BLE001 - observation never escapes
            self._fault()
            return _NOOP_HANDLE

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
        # Frozen before the document is built: what a reader gets is what the summary
        # counted, however late anything still in flight finishes.
        self._frozen = True
        return {
            "schema_version": SCHEMA_VERSION,
            "trace_id": self.trace_id,
            "task_id": task_id,
            "status": status,
            "capture_mode": CAPTURE_MODE,
            "started_at": self._started_at,
            "main_duration": main_duration,
            "total_duration": total,
            "partial": self._partial,
            "metrics_complete": not (self._partial or self._failed),
            "content_complete": self._content.complete and not self._failed,
            "warnings": list(self._warnings),
            "spans": list(self._spans),
            "contents": self._content.records(),
            "summary": self._summarise(learning),
        }

    def _summarise(self, learning: dict | None) -> dict:
        leaves = [entry for entry in self._spans if entry["name"] == HTTP_ATTEMPT_SPAN]
        return {
            "span_count": len(self._spans),
            "logical_calls": sum(1 for entry in self._spans if entry["name"] == LOGICAL_SPAN),
            "http_attempts": len(leaves),
            "tool_executions": sum(1 for entry in self._spans
                                   if entry["kind"] == "span" and entry["name"].startswith("tool.")),
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
def trace_session(*, enabled: bool = True, secrets: Iterable[str] = ()) -> Iterator[TraceRecorder | None]:
    """Install a recorder for this task, or nothing at all when tracing is off."""
    if not enabled:
        yield None
        return
    with TraceRecorder(secrets=secrets) as recorder:
        yield recorder


@contextlib.contextmanager
def span(name: str, *, attributes: dict | None = None, expected: tuple = ()) -> Iterator[Any]:
    """Time a region; the body's exception still propagates unchanged.

    ``expected`` names exceptions that are how this region *ends* rather than how it
    fails -- a phase that turns the next call away because it already has its result,
    for instance.  They are still raised to the caller; they are simply not reported
    as a failure of the region, because a clean ending is not one.
    """
    recorder = _recorder.get()
    if recorder is None or not recorder._active:
        yield _NOOP_HANDLE
        return
    handle = recorder._begin(name, attributes)
    previous = _current_span.get()
    token = _current_span.set(handle.span_id)
    try:
        yield handle
    except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised unchanged
        if isinstance(exc, asyncio.CancelledError):
            handle._finish("cancelled")
        elif isinstance(exc, GeneratorExit) or (expected and isinstance(exc, expected)):
            # A generator closed by whoever was iterating it is an end, not a failure,
            # and so is the exception the caller nominated.  The span's own result code,
            # recorded before the final item was handed on, is what says how the
            # observed work actually turned out.
            handle._finish("ok")
        else:
            handle._finish("error")
        raise
    else:
        handle._finish("ok")
    finally:
        try:
            _current_span.reset(token)
        except ValueError:
            # A span inside an async generator can be finalized by the event loop from a
            # different context than the one that opened it.  The token cannot be reset
            # there, and that must never replace what is travelling through the span.
            _current_span.set(previous)


def capture(kind: str, value: Any, *, source: str, availability: str = "captured") -> str | None:
    """Copy content onto whatever span is currently open.

    The hook that holds the content is often several frames below the code that opened
    the span -- an HTTP callback inside a model attempt, for instance -- so the span is
    found through the context rather than passed down.
    """
    recorder = _recorder.get()
    if recorder is None or not recorder._active:
        return None
    entry = recorder._entries.get(_current_span.get())
    if entry is None:
        return None
    return recorder._capture(entry, kind, value, source=source, availability=availability)


def mark(name: str, *, attributes: dict | None = None) -> Any:
    """Record a point event -- something that happened, with no duration."""
    recorder = _recorder.get()
    if recorder is None or not recorder._active:
        return _NOOP_HANDLE
    return recorder._note_event(name, attributes)


def record_check(name: str, value: bool, *, source: str = "program_check") -> bool:
    """Record one program check where it actually ran, and return it unchanged.

    Returning the value is the whole point: the caller keeps its own ``and``/``or``
    short-circuit, so this observes which checks were reached without ever deciding
    that.  A check the program never got to leaves no record, which is the only way it
    can avoid reading as one that passed.
    """
    recorder = _recorder.get()
    if recorder is not None and recorder._active:
        mark("check", attributes={"check": name, "check_source": source,
                                  "result_code": "PASS" if value else "FAIL"})
    return value


def tool_call_key(*, sdk_call_id: str, step_index: int) -> str | None:
    """The key joining one tool call's permission point with its execution.

    None while tracing is off, so a caller can use the result to decide whether it has
    anything to correlate at all.
    """
    recorder = _recorder.get()
    if recorder is None or not recorder._active:
        return None
    return recorder._tool_call_key(sdk_call_id, step_index)


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
        document = {**document, "partial": True, "metrics_complete": False,
                    "warnings": [*document.get("warnings", ()), WARNING_DOCUMENT_TOO_LARGE]}
        encoded = _encode_document(document)
    atomic_write(target, encoded)
    return target


def _read_trace(task_dir: Path) -> tuple[Path, dict]:
    """The stored trace of one task, or the reason there is nothing to show."""
    from .paths import workspace_path

    root = workspace_path(Path(task_dir))
    source = workspace_path(root / TRACE_DIRECTORY / TRACE_FILENAME)
    if not source.is_file():
        raise FileNotFoundError(f"this task has no {TRACE_FILENAME}: it was not run with tracing enabled")
    if source.stat().st_size > MAX_READ_BYTES:
        # Refused before it is loaded: the stored file is untrusted input.
        raise ValueError(f"the trace at {TRACE_DIRECTORY}/{TRACE_FILENAME} is too large to display")
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError, OSError):
        raise ValueError(f"the trace at {TRACE_DIRECTORY}/{TRACE_FILENAME} is not readable JSON") from None
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("the trace is not a document this viewer understands")
    if not _shaped_like_a_trace(document):
        # Checked up front: the renderer walks these by shape, and a wrong one would
        # otherwise surface as a traceback and the wrong exit code.
        raise ValueError(f"the trace at {TRACE_DIRECTORY}/{TRACE_FILENAME} is not shaped like a trace")
    return root, document


def _shaped_like_a_trace(document: dict) -> bool:
    """Whether the parts the viewer walks have the shapes it walks them as."""
    spans = document.get("spans", [])
    summary = document.get("summary", {})
    if not isinstance(spans, list) or any(
            not isinstance(entry, dict) or not isinstance(entry.get("attributes", {}), dict)
            for entry in spans):
        return False
    if not isinstance(summary, dict):
        return False
    return all(not isinstance(summary.get(key, {}), list) for key in ("usage", "cost"))


def _esc(value: Any) -> str:
    """Every value on the page is untrusted text, whoever wrote the trace."""
    return html.escape(str(value), quote=True)


def _seconds(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return UNKNOWN_TEXT
    return f"{float(value):.3f}s"


def _figure(value: Any) -> str:
    """A number, or a plain admission that nobody reported one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f'<span class="unknown">{UNKNOWN_TEXT}</span>'
    return _esc(f"{value:g}")


def _depths(spans) -> dict:
    """How far each span sits below the root, so the timeline reads as a shape."""
    by_id = {entry.get("span_id"): entry for entry in spans}
    depths: dict = {}
    for entry in spans:
        depth, parent, seen = 0, entry.get("parent_span_id"), set()
        while parent and parent in by_id and parent not in seen:
            seen.add(parent)
            depth += 1
            parent = by_id[parent].get("parent_span_id")
        depths[entry.get("span_id")] = depth
    return depths


def _render_summary(summary: dict) -> str:
    usage = summary.get("usage") or {}
    cost = summary.get("cost") or {}
    rows = [
        ("logical model calls", _figure(summary.get("logical_calls"))),
        ("HTTP attempts", _figure(summary.get("http_attempts"))),
        ("tool executions", _figure(summary.get("tool_executions"))),
        ("input tokens", _figure(usage.get("input_tokens"))),
        ("output tokens", _figure(usage.get("output_tokens"))),
        ("cost", _figure(cost.get("amount"))),
    ]
    if not usage.get("complete", False):
        rows.append(("token accounting", '<span class="unknown">incomplete</span>'))
    if not cost.get("complete", False):
        rows.append(("cost accounting", '<span class="unknown">incomplete</span>'))
    learning = summary.get("learning")
    if isinstance(learning, dict):
        rows.append(("learning", _esc(learning.get("code") or learning.get("status") or UNKNOWN_TEXT)))
    body = "".join(f"<tr><th>{_esc(label)}</th><td>{value}</td></tr>" for label, value in rows)
    return f"<h2>What it cost</h2><table>{body}</table>"


def _render_timeline(spans) -> str:
    depths = _depths(spans)
    ordered = sorted(spans, key=lambda entry: (entry.get("offset_seconds") is None,
                                               entry.get("offset_seconds") or 0.0))
    rows = []
    for entry in ordered:
        status = str(entry.get("status") or UNKNOWN_TEXT)
        attributes = entry.get("attributes") or {}
        rendered = " ".join(f"{_esc(key)}={_esc(value)}" for key, value in sorted(attributes.items()))
        depth = min(depths.get(entry.get("span_id"), 0), 6)
        rows.append(
            f'<tr><td class="depth-{depth}">{_esc(entry.get("name"))}'
            f'<div class="attrs">{rendered}</div></td>'
            f'<td class="kind-{_esc(entry.get("kind"))}">{_esc(entry.get("kind"))}</td>'
            f'<td>{_seconds(entry.get("offset_seconds"))}</td>'
            f'<td>{_seconds(entry.get("duration_seconds"))}</td>'
            f'<td class="status-{_esc(status)}">{_esc(status)}</td></tr>')
    head = "<tr><th>span</th><th>kind</th><th>offset</th><th>duration</th><th>status</th></tr>"
    return f"<h2>Timeline</h2><table>{head}{''.join(rows)}</table>"


def _render_body(document: dict) -> str:
    parts = [
        "<h1>ReproAgent activity trace</h1>",
        f'<p class="meta">task {_esc(document.get("task_id") or UNKNOWN_TEXT)}'
        f' &middot; status {_esc(document.get("status") or UNKNOWN_TEXT)}'
        f' &middot; trace {_esc(document.get("trace_id") or UNKNOWN_TEXT)}</p>',
        f'<p class="meta">main duration {_seconds(document.get("main_duration"))}'
        f' &middot; total duration {_seconds(document.get("total_duration"))}'
        " (includes export and learning)</p>",
    ]
    if document.get("partial") or not document.get("metrics_complete", True):
        parts.append('<p class="warn">This trace is incomplete: some observations were dropped or failed. '
                     "Every figure below covers only what was recorded.</p>")
    for warning in document.get("warnings") or ():
        parts.append(f'<p class="warn">{_esc(warning)}</p>')
    parts.append(_render_summary(document.get("summary") or {}))
    parts.append(_render_timeline(document.get("spans") or ()))
    return "\n".join(parts)


def render_trace(document: dict) -> str:
    """Render one trace document as a single, self-contained page.

    Nested spans overlap in time, so the page shows each span's own duration and never
    a sum of them: adding them up would describe a wall-clock time that never happened.
    """
    from importlib.resources import files

    template = files("reproagent").joinpath("resources/trace.html.template").read_text(encoding="utf-8")
    page = template.replace(TEMPLATE_MARKER, _render_body(document))
    if len(page.encode("utf-8")) > MAX_HTML_BYTES:
        # Refused rather than cut: half a timeline reads as a whole one.
        raise ValueError(WARNING_PAGE_TOO_LARGE)
    return page


def write_trace_html(task_dir: Path) -> Path:
    """Render the trace this task already stored; nothing else is read or written."""
    from .store import atomic_write

    _, document = _read_trace(task_dir)
    target = _trace_destination(task_dir, TRACE_HTML_FILENAME)
    atomic_write(target, render_trace(document).encode("utf-8"))
    return target
