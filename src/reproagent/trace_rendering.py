"""Reading a stored trace, checking its links, and rendering it as one page.

A stored trace is untrusted input: it is hand-editable, it is documented as
convertible from another sink, and it is the one artefact a person opens directly.
So it is bounded before it is read, its internal links are checked before it is
walked, and everything on the page is escaped as text -- captured content is data
to look at, never markup to run.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .observability import (
    MAX_HTML_BYTES,
    MAX_READ_BYTES,
    SCHEMA_VERSION,
    TEMPLATE_MARKER,
    TRACE_DIRECTORY,
    TRACE_FILENAME,
    TRACE_HTML_FILENAME,
    UNKNOWN_TEXT,
    WARNING_PAGE_TOO_LARGE,
    WARNING_TRACE_PATH_UNSAFE,
    _trace_destination,
)

#: The sections a reader moves between, in the order the plan asks for them.
SECTIONS = ("overview", "calls", "decisions")


def read_trace(task_dir: Path) -> dict:
    """Load the trace a task already stored, refusing anything out of bounds.

    Raises:
        FileNotFoundError: the task was not run with tracing on.
        ValueError: the file is too large, unreadable, or not shaped like a trace.
    """
    from .paths import workspace_path

    root = workspace_path(Path(task_dir))
    source = workspace_path(root / TRACE_DIRECTORY / TRACE_FILENAME)
    if not source.is_file():
        raise FileNotFoundError(f"this task has no {TRACE_FILENAME}: it was not run with tracing enabled")
    if source.stat().st_size > MAX_READ_BYTES:
        # Refused before it is loaded: the stored file is untrusted input.
        raise ValueError(f"the trace at {TRACE_DIRECTORY}/{TRACE_FILENAME} is too large to display")
    from .core.serialization import parse_json

    try:
        # The repository's own hardened reader rather than the stdlib default.
        document = parse_json(source.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError, OSError, RecursionError):
        # RecursionError is in the list because the file is untrusted: nesting is bounded
        # only by the writer, and a hand-edited one can be far deeper.
        raise ValueError(f"the trace at {TRACE_DIRECTORY}/{TRACE_FILENAME} is not readable JSON") from None
    return validate_trace(document)


def validate_trace(document: dict) -> dict:
    """Check the trace is the shape this viewer walks, and that its links resolve.

    Raises:
        ValueError: the version is unknown, a part has the wrong shape, a span points
            at a parent the trace does not contain (or at itself, in a cycle), or a
            span cites content that is missing or belongs to another span.
    """
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("the trace is not a document this viewer understands")
    spans, contents = document.get("spans", []), document.get("contents", [])
    if not isinstance(spans, list) or not isinstance(contents, list):
        raise ValueError("the trace is not shaped like a trace")
    summary = document.get("summary", {})
    if not isinstance(summary, dict):
        raise ValueError("the trace is not shaped like a trace")
    # The page reads these as mappings; anything else would surface as a traceback.
    # Absent is not the same as wrong: a run with no learning step legitimately has none.
    if any(summary.get(key) is not None and not isinstance(summary[key], dict)
           for key in ("usage", "cost", "learning")):
        raise ValueError("the trace is not shaped like a trace")

    by_id: dict = {}
    for entry in spans:
        if not isinstance(entry, dict) or not isinstance(entry.get("attributes", {}), dict):
            raise ValueError("the trace is not shaped like a trace")
        by_id[entry.get("span_id")] = entry
    for entry in spans:
        parent = entry.get("parent_span_id")
        if parent is None:
            continue
        if parent not in by_id:
            raise ValueError("a span refers to a parent this trace does not contain")
        # Walk up: a cycle would leave the reader looping for the same reason, and a
        # missing ancestor is a broken chain even when the direct parent is present.
        seen, walker = set(), parent
        while walker is not None:
            if walker in seen:
                raise ValueError("the trace contains a parent cycle")
            seen.add(walker)
            node = by_id.get(walker)
            if node is None:
                raise ValueError("a span refers to a parent this trace does not contain")
            walker = node.get("parent_span_id")

    for record in contents:
        if not isinstance(record, dict):
            raise ValueError("the trace is not shaped like a trace")
    stored = {record.get("content_id") for record in contents}
    for entry in spans:
        for ref in entry.get("content_refs") or ():
            # Every reference has to resolve.  Which span owns the record is not checked:
            # content is deduplicated by id and cited by every span that saw it, so a
            # shared record legitimately has more than one citing span.
            if ref not in stored:
                raise ValueError("a span cites content this trace does not contain")
    return document


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


#: A group of content larger than this opens collapsed: the wire request alone runs to
#: tens of kilobytes, and a page that opens everything is as unreadable as one that
#: opens nothing.
OPEN_INLINE_BYTES = 6000


def _order(entry: dict):
    return (entry.get("offset_seconds") is None, entry.get("offset_seconds") or 0.0)


def _render_contents(document: dict) -> str:
    """The captured calls, grouped so a call's input and result stay together.

    A flat list of content ids cannot answer "what was this tool given, and what did it
    return": the permission point owns the arguments and the execution owns the result,
    and only the shared call key puts them back in one place.  Small groups open by
    default, so the page shows the model's calls rather than a list of hashes to click.
    """
    records = {record.get("content_id"): record for record in (document.get("contents") or ())}
    spans = document.get("spans") or ()
    parts = ["<h2>Calls and content</h2>"]

    arguments: dict = {}
    for entry in spans:
        key = (entry.get("attributes") or {}).get("tool_call_key")
        if key and entry.get("kind") == "event":
            arguments[key] = list(entry.get("content_refs") or ())

    shown, emitted = set(), 0
    for entry in sorted(spans, key=_order):
        if entry.get("kind") != "span":
            continue
        attributes = entry.get("attributes") or {}
        key = attributes.get("tool_call_key")
        refs = list(entry.get("content_refs") or ())
        if key:
            refs = [ref for ref in arguments.get(key, ()) if ref not in refs] + refs
        refs = [ref for ref in refs if ref in records]
        if not refs:
            continue
        emitted += 1
        size = sum(len((records[ref].get("text") or "").encode("utf-8")) for ref in refs)
        # What is inside is named in the summary: a collapsed block labelled only with a
        # byte count reads as empty, and the request is the thing people look for.
        holds = ", ".join(dict.fromkeys(str(records[ref].get("source")) for ref in refs))
        label = _esc(entry.get("name"))
        if key:
            label += f' &middot; call {_esc(str(key)[:8])}'
        status = str(entry.get("status") or UNKNOWN_TEXT)
        opener = " open" if size <= OPEN_INLINE_BYTES else ""
        parts.append(f'<details{opener}><summary>{label} '
                     f'<span class="status-{_esc(status)}">{_esc(status)}</span>'
                     f' &middot; {_esc(holds)}'
                     f' <span class="unknown">{_esc(size)} bytes</span></summary>')
        for ref in refs:
            record = records[ref]
            shown.add(ref)
            flags = ", ".join(name for name in ("truncated", "redacted") if record.get(name))
            head = (f'<h4>{_esc(record.get("source"))} &middot; {_esc(record.get("availability"))}'
                    + (f' &middot; {_esc(flags)}' if flags else '') + "</h4>")
            text = record.get("text")
            parts.append(head + (f"<pre>{_esc(text)}</pre>" if text
                                 else f'<p class="unknown">{_esc(record.get("availability"))}</p>'))
        parts.append("</details>")

    orphans = [record for cid, record in records.items() if cid not in shown]
    for record in orphans:
        text = record.get("text")
        parts.append(f'<details><summary>{_esc(record.get("source"))} '
                     f'&middot; {_esc(record.get("availability"))}</summary>'
                     + (f"<pre>{_esc(text)}</pre>" if text else "") + "</details>")
    if not emitted and not orphans:
        parts.append('<p class="unknown">No content was captured for this run.</p>')
    return "\n".join(parts)


def _render_decisions(document: dict) -> str:
    """Why the run was accepted or refused, from the program's own points.

    The check points and the verdict are the acceptance reasoning.  They are shown with
    the source that produced them, so "the model said so" and "the program accepted it"
    never read as the same statement.
    """
    decided = [entry for entry in (document.get("spans") or ())
               if "check" in (entry.get("attributes") or {}) or entry.get("name") == "verdict"]
    parts = ["<h2>Decisions</h2>"]
    if not decided:
        parts.append('<p class="unknown">This run recorded no acceptance decision.</p>')
        return "\n".join(parts)
    rows = []
    for entry in decided:
        attributes = entry.get("attributes") or {}
        rows.append(f"<tr><td>{_esc(attributes.get('check') or entry.get('name'))}</td>"
                    f"<td>{_esc(attributes.get('result_code'))}</td>"
                    f"<td>{_esc(attributes.get('check_source') or attributes.get('reason_origin') or UNKNOWN_TEXT)}</td></tr>")
    head = "<tr><th>what was decided</th><th>result</th><th>who decided it</th></tr>"
    return f"{parts[0]}<table>{head}{''.join(rows)}</table>"


def _render_body(document: dict) -> str:
    parts = [
        f'<section id="overview"><h1>ReproAgent activity trace</h1>',
        f'<p class="meta">task {_esc(document.get("task_id") or UNKNOWN_TEXT)}'
        f' &middot; status {_esc(document.get("status") or UNKNOWN_TEXT)}'
        f' &middot; trace {_esc(document.get("trace_id") or UNKNOWN_TEXT)}'
        f' &middot; capture {_esc(document.get("capture_mode") or UNKNOWN_TEXT)}</p>',
        f'<p class="meta">main duration {_seconds(document.get("main_duration"))}'
        f' &middot; total duration {_seconds(document.get("total_duration"))}'
        " (includes export and learning)</p>",
    ]
    if document.get("partial") or not document.get("metrics_complete", True):
        parts.append('<p class="warn">This trace is incomplete: some observations were dropped or failed. '
                     "Every figure below covers only what was recorded.</p>")
    if not document.get("content_complete", True):
        parts.append('<p class="warn">Some captured content is missing or was cut short; '
                     "each entry below says which, and why.</p>")
    for warning in document.get("warnings") or ():
        parts.append(f'<p class="warn">{_esc(warning)}</p>')
    parts.append(_render_summary(document.get("summary") or {}))
    parts.append(_render_timeline(document.get("spans") or ()))
    parts.append("</section>")
    parts.append(f'<section id="calls">{_render_contents(document)}</section>')
    parts.append(f'<section id="decisions">{_render_decisions(document)}</section>')
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


def write_trace_html(task_dir: Path, document: dict) -> Path:
    """Render one document beside its task; nothing else is read or written.

    The document is passed in so the automatic path renders the very object it just
    wrote, instead of reading back a file it already holds.
    """
    from .store import atomic_write

    target = _trace_destination(task_dir, TRACE_HTML_FILENAME)
    if not isinstance(document, dict):
        raise ValueError(WARNING_TRACE_PATH_UNSAFE)
    atomic_write(target, render_trace(document).encode("utf-8"))
    return target


__all__ = ["SECTIONS", "read_trace", "render_trace", "validate_trace", "write_trace_html"]
