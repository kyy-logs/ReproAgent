"""The whole thing, over a real SDK run and real pytest.

This is the acceptance the plan asks for: not that a recorder was called, but that a
run which actually happened can be read back -- what the model was sent, what it
returned, what a tool was given and gave back, and why the run was accepted -- with
the trace itself changing none of it.

The negative cases matter as much as the positive ones.  A trace that merely exists
proves nothing; what has to hold is that a model claiming reproduction is still not
believed over the program's own checks.
"""
import json

from reproagent.core.models import ModelResponse
from reproagent.observability import TRACE_DIRECTORY, TRACE_FILENAME, write_trace
from reproagent.trace_rendering import render_trace
from tests.integration.test_observability_lifecycle import (
    candidate_bytes,
    document_for,
    run_sdk_task,
)
from tests.unit.test_controller import ScriptedModel


class MisleadingModel(ScriptedModel):
    """Claims reproduction while contradicting the claim in its own semantic booleans.

    The provider saying "this reproduces" is a claim; only the program's checks can
    turn it into an accepted result, and this is the case where they must not.
    """

    async def complete(self, request, context):
        response = await super().complete(request, context)
        if request.response_kind == 'verdict':
            data = json.loads(response.text)
            data['classification'] = 'REPRODUCED'
            data['target_triggered'] = False
            return ModelResponse(json.dumps(data))
        return response


def test_a_run_can_be_read_back_afterwards(tmp_path, projects, facts):
    request, controller, result, recorder, seen, calls = run_sdk_task(
        tmp_path, projects, facts, trace=True)
    document = document_for(request, controller, result, recorder)

    sources = {record["source"] for record in document["contents"]}
    # What the model was sent, what it returned, and what the tools were given and gave.
    assert {"wire_request", "provider_response", "sdk_tool_input", "sdk_tool_result"} <= sources

    # The contract call is structured and carries no tools by design; the exploration
    # call does, and that is the request shape worth confirming here.
    bodies = [json.loads(record["text"]) for record in document["contents"]
              if record["source"] == "wire_request"]
    exploration = next(body for body in bodies if "tools" in body)
    assert exploration["messages"] and exploration["tools"]

    # A tool's arguments and its result are the same call, by key.
    permission = next(entry for entry in document["spans"] if entry["name"] == "permission")
    executed = next(entry for entry in document["spans"] if entry["name"].startswith("tool."))
    assert permission["attributes"]["tool_call_key"] == executed["attributes"]["tool_call_key"]

    # Why the run was accepted: the model's claim and the program's checks, kept apart.
    checks = {entry["attributes"]["check"]: entry["attributes"]["check_source"]
              for entry in document["spans"] if "check" in entry["attributes"]}
    assert checks["provider_semantic_claims"] == "provider_claim"
    assert checks["candidate_reproduced"] == "program_check"
    assert checks["binding_and_integrity_ok"] == "program_check"

    # ...and the page carries all of it, including the result the task reached.
    page = render_trace(document)
    for marker in ("wire_request", "sdk_tool_input", "sdk_tool_result", "provider_semantic_claims"):
        assert marker in page
    # The acceptance the program reached is on the page too.  The evidence level itself
    # is a domain result, not a trace field: the trace copies the task status.
    assert result.status.value in page
    assert "verdict" in page


def test_a_captured_run_reaches_the_same_result_as_an_uncaptured_one(tmp_path, projects, facts):
    plain = run_sdk_task(tmp_path / 'plain', projects, facts, trace=False)
    traced = run_sdk_task(tmp_path / 'traced', projects, facts, trace=True)

    # Same conclusion, same evidence, same delivered source.
    assert traced[2].status is plain[2].status
    assert traced[2].evidence_level is plain[2].evidence_level
    assert candidate_bytes(traced[0]) == candidate_bytes(plain[0])
    # Same work: the same model requests, in the same kinds, and no extra call to pay for.
    assert traced[5] == plain[5]
    assert len(traced[4]) == len(plain[4])


def test_a_model_claiming_reproduction_is_not_believed_over_the_program(tmp_path, projects, facts):
    """The expected outcome here is a rejection: that is what makes this a real check."""
    request, controller, result, recorder, seen, calls = run_sdk_task(
        tmp_path, projects, facts, trace=True, script=MisleadingModel())
    document = document_for(request, controller, result, recorder)

    checks = {entry["attributes"]["check"]: entry["attributes"]["result_code"]
              for entry in document["spans"] if "check" in entry["attributes"]}

    # The model said REPRODUCED; its own boolean said the target never triggered, and
    # the program recorded that as a failed claim rather than an acceptance.
    assert checks["provider_semantic_claims"] == "FAIL"
    assert result.evidence_level.value != "DIFFERENTIAL_VALIDATED"
    verdicts = [entry for entry in document["spans"] if entry["name"] == "verdict"]
    assert verdicts and all(entry["attributes"]["reason_origin"] == "program" for entry in verdicts)


def test_the_page_is_a_real_file_beside_the_task(tmp_path, projects, facts):
    request, controller, result, recorder, seen, calls = run_sdk_task(
        tmp_path, projects, facts, trace=True)
    document = document_for(request, controller, result, recorder)

    from reproagent.trace_rendering import write_trace_html

    write_trace(request.output_dir, document)
    page = write_trace_html(request.output_dir, document)

    assert page == request.output_dir / TRACE_DIRECTORY / 'trace.html'
    assert (request.output_dir / TRACE_DIRECTORY / TRACE_FILENAME).is_file()
    assert page.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
