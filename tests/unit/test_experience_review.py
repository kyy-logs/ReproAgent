import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from reproagent.core.models import EvidenceRef, TaskRequest
from reproagent.core.protocol import learning_schema
from reproagent.core.serialization import bytes_hash, canonical_bytes
from reproagent.experience import INPUT_BYTES, LearningInput, LearningValidationError
from reproagent.experience_drafts import LessonDraft
from reproagent.experience_review import (
    extract_fallback_draft, fallback_draft_schema, prepare_fallback_review_input,
    review_experience, review_schema, validate_review)
from reproagent.store import TaskStore, atomic_write


def test_review_schema_is_a_closed_decision_envelope():
    schema = review_schema()
    assert set(schema["properties"]) == {"decision", "reason_code", "experience"}
    assert schema["required"] == ["decision", "reason_code", "experience"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["decision"]["enum"] == ["accept", "revise", "reject"]
    assert schema["properties"]["reason_code"]["enum"] == [
        "SUPPORTED", "SCOPE_NARROWED", "UNSUPPORTED", "CONTRADICTED", "NO_REUSABLE_LESSON"]
    assert schema["properties"]["experience"] == learning_schema()["properties"]["experience"]


def material_with(tmp_path, kind="tool_error"):
    store = TaskStore(tmp_path / "task")
    line = canonical_bytes({"id": "E1", "kind": kind, "text": "observed failure"}) + b"\n"
    atomic_write(store.root / "learning/evidence.jsonl", line)
    ref = EvidenceRef("learning/evidence.jsonl", bytes_hash(line), 1, 1)
    payload = {"evidence": [{"id": "E1", "kind": kind, "text": "observed failure"}],
               "draft_evidence_ids": ["E1"], "attempts": [], "omissions": []}
    return store, LearningInput(payload, {"E1": ref}, "task", 1)


def card_response(evidence_ids=("E1",)):
    return {"category": "framework", "tags": ["pytest"],
            "name": "check path type", "description": "when a fixture rejects a path object",
            "summary": "observed setup failure", "detail": "check the accepted path type",
            "evidence_ids": list(evidence_ids)}


def test_review_pairs_are_closed_and_require_independent_support(tmp_path):
    store, material = material_with(tmp_path)
    accepted = validate_review({"decision": "accept", "reason_code": "SUPPORTED",
                                "experience": card_response()}, material, store)
    assert accepted.decision == "accept" and accepted.card.id
    rejected = validate_review({"decision": "reject", "reason_code": "UNSUPPORTED",
                                "experience": None}, material, store)
    assert rejected.card is None
    for response in [
        {"decision": "accept", "reason_code": "UNSUPPORTED", "experience": card_response()},
        {"decision": "revise", "reason_code": "SUPPORTED", "experience": card_response()},
        {"decision": "reject", "reason_code": "UNSUPPORTED", "experience": card_response()},
        {"decision": "accept", "reason_code": "SUPPORTED", "experience": card_response(("E9",))},
    ]:
        with pytest.raises(LearningValidationError):
            validate_review(response, material, store)


def test_review_is_one_learning_request_without_tools(tmp_path):
    import asyncio
    import json

    store, material = material_with(tmp_path)
    seen = []
    class Gateway:
        async def complete(self, request, context):
            seen.append(request)
            return type("Response", (), {"text": json.dumps({
                "decision": "accept", "reason_code": "SUPPORTED", "experience": card_response()})})()
    decision = asyncio.run(review_experience(material, Gateway(), None, store))
    assert decision.decision == "accept"
    assert len(seen) == 1
    assert seen[0].response_kind == "learning"
    assert len(seen[0].messages) == 2
    assert "draft_evidence_ids" in seen[0].messages[1]["content"]


def test_agent_hypothesis_alone_cannot_support_a_review(tmp_path):
    store, material = material_with(tmp_path, kind="agent_hypothesis")
    with pytest.raises(LearningValidationError):
        validate_review({"decision": "accept", "reason_code": "SUPPORTED",
                         "experience": card_response()}, material, store)


def fallback_response(source_ids):
    return json.dumps({"lesson_draft": {
        "situation": "when a fixture rejects a path",
        "observation": "setup failed before the target",
        "lesson": "check the accepted path type", "source_ids": list(source_ids)}})


class FallbackGateway:
    def __init__(self, text):
        self.text = text
        self.seen = []

    async def complete(self, request, context):
        self.seen.append(request)
        return SimpleNamespace(text=self.text)


def test_fallback_draft_uses_material_eids_and_rejects_paths(tmp_path):
    import asyncio

    store, material = material_with(tmp_path)
    gateway = FallbackGateway(fallback_response(["E1"]))
    draft = asyncio.run(extract_fallback_draft(material, gateway, None, store))
    assert draft.source_ids == ("E1",)
    assert len(gateway.seen) == 1
    assert gateway.seen[0].response_kind == "learning"
    payload = json.loads(gateway.seen[0].messages[-1]["content"])
    assert payload["response_schema"] == fallback_draft_schema()
    assert set(fallback_draft_schema()["properties"]) == {"lesson_draft"}

    for bad in (["src/parser.py"], ["E1", "deadbeef"]):
        with pytest.raises(LearningValidationError) as caught:
            asyncio.run(extract_fallback_draft(
                material, FallbackGateway(fallback_response(bad)), None, store))
        assert caught.value.code == "invalid_evidence_ids"


def test_fallback_draft_requires_an_independent_fact(tmp_path):
    import asyncio

    store, material = material_with(tmp_path, kind="agent_hypothesis")
    with pytest.raises(LearningValidationError) as caught:
        asyncio.run(extract_fallback_draft(
            material, FallbackGateway(fallback_response(["E1"])), None, store))
    assert caught.value.code == "invalid_evidence_ids"


def test_prepare_fallback_review_input_attaches_draft_without_mutating_material(tmp_path):
    store, material = material_with(tmp_path)
    draft = LessonDraft("situation", "observation", "lesson", ("E1",))
    prepared = prepare_fallback_review_input(material, draft)
    assert prepared is not None
    assert prepared.payload["draft"] == {
        "situation": "situation", "observation": "observation",
        "lesson": "lesson", "source_ids": ["E1"]}
    assert prepared.payload["draft_evidence_ids"] == ["E1"]
    assert prepared.payload["response_schema"] == review_schema()
    assert "draft" not in material.payload
    oversized = LearningInput({**material.payload, "padding": "x" * (INPUT_BYTES + 1)},
                              material.evidence_map, material.source_task_id, material.event_cutoff)
    assert prepare_fallback_review_input(oversized, draft) is None


def test_experience_draft_fallback_defaults_off_and_validates_boolean(tmp_path):
    request = TaskRequest(tmp_path, tmp_path / "out", tmp_path / "issue.md")
    assert request.experience_draft_fallback is False
    for invalid in (1, "true", None):
        with pytest.raises(ValueError, match="experience_draft_fallback"):
            replace(request, experience_draft_fallback=invalid)
