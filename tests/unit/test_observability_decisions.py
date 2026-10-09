"""What the program decided, kept apart from what the model claimed.

The trace may show the acceptance reasoning, but it must never blur the two: a
provider saying "this reproduces" is a claim, and only the program's own checks make
it an accepted result.
"""
from __future__ import annotations

import pytest

from reproagent.adapters.runtimes.local import process_health
from reproagent.observability import TraceRecorder, record_check, span


@pytest.mark.parametrize(("stop_reason", "cleanup_ok", "expected"), [
    ("EXITED", True, "passed"),
    ("COMMAND_TIMEOUT", True, "failed"),
    ("CANCELLED", True, "blocked"),
    ("LOG_LIMIT", True, "blocked"),
    ("EXITED", False, "degraded"),
    ("COMMAND_TIMEOUT", False, "degraded"),
    ("SOMETHING_NEW", True, "unknown"),
    (None, True, "unknown"),
])
def test_process_health_uses_only_the_runs_own_signals(stop_reason, cleanup_ok, expected):
    assert process_health(stop_reason, cleanup_ok) == expected


def test_a_candidates_failing_tests_are_not_a_broken_environment():
    """Exit code 1 is the reproduction being observed, not a fault.

    Health is a judgement about the process, so the candidate's own result must not
    move it: an environment reported as broken because the bug reproduced would be a
    lie about the run.
    """
    assert process_health("EXITED", True) == "passed"


def test_program_checks_and_provider_claims_keep_their_sources():
    with TraceRecorder() as recorder:
        with span("verify"):
            record_check("binding_and_integrity_ok", True)
            record_check("provider_semantic_claims", True, source="provider_claim")
            record_check("citations_cover_expectation_and_failure", False)

    document = recorder.finish(task_id="task-1", status="DONE", main_duration=1.0)
    recorded = {entry["attributes"]["check"]: entry["attributes"]["check_source"]
                for entry in document["spans"] if "check" in entry["attributes"]}

    assert recorded == {"binding_and_integrity_ok": "program_check",
                        "provider_semantic_claims": "provider_claim",
                        "citations_cover_expectation_and_failure": "program_check"}
