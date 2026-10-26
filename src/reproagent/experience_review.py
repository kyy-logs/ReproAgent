"""The learning-call decision envelope for a task-scoped lesson draft."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from typing import TYPE_CHECKING

from .core.models import ModelRequest
from .core.protocol import learning_schema
from .core.serialization import canonical_bytes, parse_json

if TYPE_CHECKING:
    from .experience_drafts import LessonDraft

DECISIONS = ("accept", "revise", "reject")
REASON_CODES = ("SUPPORTED", "SCOPE_NARROWED", "UNSUPPORTED", "CONTRADICTED", "NO_REUSABLE_LESSON")
PAIRS = {
    "accept": ("SUPPORTED",),
    "revise": ("SCOPE_NARROWED",),
    "reject": ("UNSUPPORTED", "CONTRADICTED", "NO_REUSABLE_LESSON"),
}


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    decision: str
    reason_code: str
    card: object | None


def validate_review(response: dict, material, store, *, secrets=()) -> ReviewDecision:
    from .experience import LearningValidationError, validate_experience
    if not isinstance(response, dict) or set(response) != {"decision", "reason_code", "experience"}:
        raise LearningValidationError("invalid_structure")
    decision, reason = response["decision"], response["reason_code"]
    if decision not in DECISIONS or reason not in PAIRS[decision]:
        raise LearningValidationError("invalid_fields")
    card_response = response["experience"]
    if decision == "reject":
        if card_response is not None:
            raise LearningValidationError("invalid_fields")
        return ReviewDecision(decision, reason, None)
    if card_response is None:
        raise LearningValidationError("invalid_fields")
    card = validate_experience({"experience": card_response}, material, store, secrets=secrets)
    if card is None:
        raise LearningValidationError("invalid_fields")
    ids = card_response["evidence_ids"]
    draft_ids = set(material.payload.get("draft_evidence_ids", ()))
    kinds = {entry.get("id"): entry.get("kind") for entry in material.payload.get("evidence", [])}
    if not draft_ids.intersection(ids):
        raise LearningValidationError("invalid_evidence_ids")
    if not any(kinds.get(identifier) not in (None, "agent_hypothesis") for identifier in ids):
        raise LearningValidationError("invalid_evidence_ids")
    return ReviewDecision(decision, reason, card)


async def review_experience(material, gateway, context, store, *, secrets=()) -> ReviewDecision:
    prompt = files("reproagent").joinpath("prompts/review_experience.md").read_text(encoding="utf-8")
    response = await gateway.complete(
        ModelRequest(({"role": "system", "content": prompt},
                      {"role": "user", "content": canonical_bytes(material.payload).decode("utf-8")}),
                     response_kind="learning"), context)
    try:
        parsed = parse_json(response.text)
    except ValueError:
        from .experience import LearningValidationError
        raise LearningValidationError("invalid_json") from None
    return validate_review(parsed, material, store, secrets=secrets)


def review_schema() -> dict:
    experience = learning_schema()["properties"]["experience"]
    return {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": list(DECISIONS)},
            "reason_code": {"type": "string", "enum": list(REASON_CODES)},
            "experience": experience,
        },
        "required": ["decision", "reason_code", "experience"],
        "additionalProperties": False,
    }


def fallback_draft_schema() -> dict:
    """The one draft shape the optional fallback request may return."""
    return {"type": "object", "properties": {"lesson_draft": {
        "type": "object", "properties": {
            "situation": {"type": "string"}, "observation": {"type": "string"},
            "lesson": {"type": "string"},
            "source_ids": {"type": "array", "minItems": 1, "maxItems": 3,
                           "uniqueItems": True, "items": {"type": "string"}}},
        "required": ["situation", "observation", "lesson", "source_ids"],
        "additionalProperties": False}}, "required": ["lesson_draft"],
        "additionalProperties": False}


async def extract_fallback_draft(material, gateway, context, store, *,
                                 secrets=()) -> LessonDraft | None:
    """One bounded request that turns existing material into a draft, or None.

    The fallback is a draft like any other: every cited id must be an EID already
    present in this material, and at least one citation must be an independent fact.
    There is no retry or format correction here.
    """
    import copy
    from .core.models import ModelRequest
    from .core.serialization import ObjectRootError, canonical_bytes
    from .experience import LearningValidationError, _check, _redact
    from .experience_drafts import parse_lesson_draft
    _check(context)
    payload = copy.deepcopy(material.payload)
    payload["response_schema"] = fallback_draft_schema()
    text = _redact(canonical_bytes(payload).decode("utf-8"), secrets)
    prompt = files("reproagent").joinpath("prompts/extract_fallback_draft.md").read_text(encoding="utf-8")
    response = await gateway.complete(ModelRequest(({"role": "system", "content": prompt},
        {"role": "user", "content": text}), response_kind="learning"), context)
    _check(context)
    try:
        parsed = parse_json(response.text)
    except ObjectRootError:
        raise LearningValidationError("invalid_structure") from None
    except (ValueError, TypeError, RecursionError):
        raise LearningValidationError("invalid_json") from None
    if not isinstance(parsed, dict) or set(parsed) != {"lesson_draft"}:
        raise LearningValidationError("invalid_structure")
    raw = parsed["lesson_draft"]
    if raw is None:
        return None
    try:
        draft = parse_lesson_draft(raw, secrets=secrets)
    except (ValueError, TypeError):
        raise LearningValidationError("invalid_fields") from None
    evidence = {entry.get("id"): entry for entry in material.payload.get("evidence", ())
                if isinstance(entry, dict)}
    if any(identifier not in material.evidence_map for identifier in draft.source_ids):
        raise LearningValidationError("invalid_evidence_ids")
    if not any(evidence.get(identifier, {}).get("kind") not in (None, "agent_hypothesis")
               for identifier in draft.source_ids):
        raise LearningValidationError("invalid_evidence_ids")
    return draft


def prepare_fallback_review_input(material, draft):
    """Attach a synthesized draft to the material without mutating the original."""
    import copy
    from .experience import INPUT_BYTES, LearningInput
    payload = copy.deepcopy(material.payload)
    payload["draft"] = {"situation": draft.situation, "observation": draft.observation,
                        "lesson": draft.lesson, "source_ids": list(draft.source_ids)}
    payload["draft_evidence_ids"] = list(draft.source_ids)
    payload["response_schema"] = review_schema()
    if len(canonical_bytes(payload)) > INPUT_BYTES:
        return None
    return LearningInput(payload, material.evidence_map, material.source_task_id,
                         material.event_cutoff)


__all__ = ["DECISIONS", "REASON_CODES", "extract_fallback_draft", "fallback_draft_schema",
           "prepare_fallback_review_input", "review_schema"]
