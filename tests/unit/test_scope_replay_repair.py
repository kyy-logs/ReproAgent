import asyncio
import json
from dataclasses import replace

import pytest

from reproagent.core.models import CandidateClass, EvidenceRef
from reproagent.core.review_context import build_review_context
from reproagent.core.serialization import bytes_hash
from reproagent.store import safe_child
from tests.unit.test_verifier import prepare, EvidenceModel
from tests.integration.test_runner import execute


def test_review_retains_original_issue_when_selected_contract_sources_omit_it(tmp_path, projects, facts):
    contract, candidate, run, verifier, *_ = prepare(tmp_path, projects, facts)
    text = "Reported target is a table; section behavior is not requested.\n"
    path = verifier.store.root / "input/issue.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf8")
    contract = replace(contract, description_hash=bytes_hash(path.read_bytes()))
    context = build_review_context(contract, candidate, run, read_ref=verifier.resolve,
        read_file=lambda e: safe_child(candidate.storage_root, e.path).read_bytes(), max_bytes=65536)
    original = context.payload["original_issue"]
    assert original["text"] == path.read_bytes().decode("utf8") and original["content_hash"] == contract.description_hash
    assert original["path"] == "input/issue.md"
    assert all(s["text"] != text for s in context.payload["expectation_sources"])
    path.write_text("changed", encoding="utf8")
    with pytest.raises(ValueError, match="hash mismatch"):
        build_review_context(contract, candidate, run, read_ref=verifier.resolve,
            read_file=lambda e: safe_child(candidate.storage_root, e.path).read_bytes(), max_bytes=65536)


def test_bound_issue_cannot_be_silently_missing_at_review(tmp_path, projects, facts):
    contract, candidate, run, verifier, model, *_ = prepare(tmp_path, projects, facts)
    contract = replace(contract, description_hash="a" * 64)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (run,), facts.context()))
    assert verdict.classification is None and not model.calls
    assert verdict.issues[0].code == "EVIDENCE_UNAVAILABLE"


@pytest.mark.parametrize("change", ["semantic", "signature", "binding"])
def test_replay_diagnosis_separates_semantic_disagreement_from_execution_differences(tmp_path, projects, facts, change):
    contract, candidate, first, verifier, _, runner, snapshot, request = prepare(tmp_path, projects, facts)
    second = execute(request, snapshot, candidate, runner, facts.context())
    first_verdict = asyncio.run(verifier.evaluate(contract, candidate, (first,), facts.context()))
    verifier.gateway = EvidenceModel("NOT_REPRODUCED")
    second_verdict = asyncio.run(verifier.evaluate(contract, candidate, (second,), facts.context()))
    assert first_verdict.classification == CandidateClass.REPRODUCED
    if change == "signature":
        second = replace(second, observation=replace(second.observation, tests=tuple(
            dict(t, crash={**t.get("crash", {}), "message":"different target failure"})
            if t.get("outcome") == "failed" else t for t in second.observation.tests)))
    if change == "binding":
        second = replace(second, manifest_hash="wrong")
    data = verifier.replay_diagnostics(contract, candidate, first, second, (first_verdict, second_verdict))
    assert data["execution_consistent"] is (change == "semantic")
    assert data["kind"] == ("semantic_disagreement" if change == "semantic" else "execution_unconfirmed")
    assert data["semantic_confirmed"] is False
    assert data["first_run_id"] == first.run_id and data["second_run_id"] == second.run_id
    assert data["verdict_classes"] == ["REPRODUCED", "NOT_REPRODUCED"]
    assert not verifier.confirm(contract, candidate, first, second, (first_verdict, second_verdict))


def test_original_issue_references_identical_complete_source_instead_of_duplicate_text(tmp_path, projects, facts):
    contract, candidate, run, verifier, *_ = prepare(tmp_path, projects, facts)
    data = safe_child(verifier.store.root, contract.sources[0].path).read_bytes()
    path = verifier.store.root / "input/issue.md"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)
    contract = replace(contract, description_hash=bytes_hash(data))
    review = build_review_context(contract, candidate, run, read_ref=verifier.resolve,
        read_file=lambda e: safe_child(candidate.storage_root, e.path).read_bytes(), max_bytes=65536)
    issue = review.payload["original_issue"]
    assert "text" not in issue
    source = review.payload["expectation_sources"][issue["expectation_source_index"]]
    assert source["content_hash"] == issue["content_hash"]
    assert source["text"] == "Empty input must return an empty list; parse([]) raises IndexError."


def test_replay_observation_rejects_contradictory_semantic_confirmation():
    from reproagent.core.replay_feedback import valid_replay_observation
    data = {"origin":"program_observation", "kind":"execution_unconfirmed", "first_run_id":"run-1", "second_run_id":"run-2",
        "execution_consistent":False, "execution_unmet_checks":["preconditions_repeatable"], "semantic_confirmed":True,
        "verdict_classes":["REPRODUCED", "NOT_REPRODUCED"]}
    assert not valid_replay_observation(data)
