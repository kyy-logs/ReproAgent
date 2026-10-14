"""Shared best-effort trace finalization for CLI and evaluation entry points."""
import sys
from dataclasses import asdict

from .observability import write_trace
from .trace_rendering import write_trace_html

TRACE_WRITE_WARNING = 'reproagent: could not write the activity trace; the task result is unchanged.'
TRACE_PAGE_WARNING = 'reproagent: could not write the trace page; the trace itself is intact.'
TRACE_FINISH_WARNING = 'reproagent: could not finalize the activity trace; the task result is unchanged.'


def finish_trace_or_warn(recorder, result, *, learning=None):
    if recorder is None:
        return None
    try:
        return recorder.finish(task_id=result.task_id, status=result.status.value,
                               main_duration=result.duration,
                               learning=asdict(learning) if learning is not None else None)
    except Exception:
        print(TRACE_FINISH_WARNING, file=sys.stderr)
        return None


def write_trace_or_warn(task_dir, document) -> bool:
    """Never write a page from a JSON document that failed to land."""
    try:
        write_trace(task_dir, document)
    except Exception:
        print(TRACE_WRITE_WARNING, file=sys.stderr)
        return False
    try:
        write_trace_html(task_dir, document)
    except Exception:
        print(TRACE_PAGE_WARNING, file=sys.stderr)
    return True
