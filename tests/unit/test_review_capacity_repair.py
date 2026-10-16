"""Independent review capacity and lossless evidence references."""
import asyncio
from dataclasses import asdict
import json

import pytest

from reproagent.core.models import BudgetLimits, CandidateClass, TaskRequest
from reproagent.core.serialization import encode_record, decode_record
from reproagent.core.review_context import build_review_context
from tests.unit.test_review_context import candidate_reader
from tests.unit.test_verifier import prepare


@pytest.mark.parametrize("tool_cap", [1024, 32768])
def test_default_review_capacity_is_independent_of_tool_returns(tmp_path, projects, facts, tool_cap):
    text = ("from example.parser import parse\n# " + "padding" * 5000
            + "\ndef test_empty():\n    assert parse([]) == []\n")
    contract, candidate, run, verifier, model, *_ = prepare(tmp_path, projects, facts, text)
    before = asdict(run)
    limits = BudgetLimits(tool_response_bytes=tool_cap)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context(limits=limits)))
    assert verdict.classification == CandidateClass.REPRODUCED
    sent = json.loads(model.calls[0].messages[-1]["content"])
    assert 32768 < len(model.calls[0].messages[-1]["content"].encode()) <= 65536
    assert sent["candidate"][0]["content"] == text
    assert sent["failure_evidence"] and sent["assertion_sites"]
    assert sent["response_schema"] and sent["available_refs"]
    assert limits.tool_response_bytes == tool_cap and limits.review_context_bytes == 65536
    assert asdict(run) == before


def test_review_limit_is_configurable_and_round_trips(tmp_path, projects, facts):
    limits = BudgetLimits(review_context_bytes=1024)
    request = TaskRequest(repo=tmp_path, output_dir=tmp_path / "out", issue_file=tmp_path / "issue.md", limits=limits)
    assert decode_record("request", encode_record(request)).limits == limits
    legacy = encode_record(request)
    del legacy["limits"]["review_context_bytes"]
    assert decode_record("request", legacy).limits.review_context_bytes == 65536
    contract, candidate, run, verifier, model, *_ = prepare(tmp_path, projects, facts)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context(limits=limits)))
    assert verdict.classification is None and not model.calls
    issue = verdict.issues[0]
    assert issue.code == "REVIEW_CONTEXT_TOO_LARGE"
    assert issue.measurements["budget_bytes"] == 1024
    assert issue.measurements["needed_bytes"] > 1024


@pytest.mark.parametrize("invalid", [True, None, 0, -1, 65536.5, float("inf")])
def test_review_capacity_requires_a_positive_integer(invalid):
    with pytest.raises(ValueError):
        BudgetLimits(review_context_bytes=invalid)


def test_matching_failure_details_are_referenced_even_when_capacity_is_ample(tmp_path, projects, facts):
    contract, candidate, run, verifier, *_ = prepare(tmp_path, projects, facts)
    before = asdict(run)
    review = build_review_context(contract, candidate, run, read_ref=verifier.resolve,
        read_file=candidate_reader(candidate), max_bytes=65536)
    phase = next(p for p in review.payload["observation"]["tests"] if p["outcome"] == "failed")
    assert "longrepr" not in phase
    record = json.loads(review.payload["failure_evidence"][phase["failure_evidence_ref"]]["text"])
    assert record["payload"]["longrepr"] == next(p["longrepr"] for p in run.observation.tests if p["outcome"] == "failed")
    assert "longrepr" in phase["referenced_fields"]
    assert asdict(run) == before
