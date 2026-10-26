"""Bounded completion of one omitted lesson decision.

This module only shapes and validates the small protocol object exchanged when a
``write_candidate`` call omitted both lesson fields.  The middleware owns the
budget, model call, source registry and candidate mutation; this module stays
pure so those boundaries remain easy to test.
"""
from __future__ import annotations

import json

from ...core.serialization import canonical_bytes, parse_json
from ...experience_drafts import LessonDraft, parse_lesson_draft
from ...experience_sources import source_notice

COMPLETION_SECONDS = 10
COMPLETION_MAX_TOKENS = 768

COMPLETION_SCHEMA = {
    "type": "object",
    "properties": {
        "lesson_status": {"type": "string", "enum": ["draft", "none"]},
        "lesson_draft": {
            "type": "object",
            "properties": {
                "situation": {"type": "string"},
                "observation": {"type": "string"},
                "lesson": {"type": "string"},
                "source_ids": {"type": "array", "minItems": 1, "maxItems": 3,
                               "items": {"type": "string"}},
            },
            "required": ["situation", "observation", "lesson", "source_ids"],
            "additionalProperties": False,
        },
    },
    "required": ["lesson_status"],
    "additionalProperties": False,
}


class CompletionInvalid(ValueError):
    """The completion reply is not a usable decision; no model text is retained."""


def build_messages(original: dict, sources: dict) -> tuple[str, str, tuple[str, ...]]:
    """Build the no-tools completion prompt from the original call and visible sources.

    The source notices are separate raw messages, matching what the explorer saw.
    Keeping them out of the JSON envelope avoids double-escaping the copyable IDs.
    These bounded, already-redacted registry objects supplement the latest exploration
    input retained by the middleware. References such as original_run IDs do not
    replace the execution feedback and other observations in that context.
    """
    notices = tuple(source_notice(identity) for identity in sources)
    facts = [{"learning_source_id": identity, **sources[identity]} for identity in sources]
    user = canonical_bytes({
        "write_candidate": original,
        "learning_sources": facts,
        "response_schema": COMPLETION_SCHEMA,
    }).decode("utf-8")
    system = (
        "One write_candidate call omitted both lesson_status and lesson_draft. "
        "For this completion only, do not call tools; return exactly one JSON object "
        "and no prose. Use the preceding exploration context to interpret observations, "
        "but cite only learning_source_id values "
        "shown in the separate learning_source notices and learning_sources. Set "
        "lesson_status=\"none\" when no displayed source supports a reusable, conditional "
        "pitfall. Set lesson_status=\"draft\" and include lesson_draft only when a displayed "
        "source supports the observed condition, attempted operation, actual result and a "
        "concrete next check. A source-code fact or verified workaround is not required; "
        "mark untested advice as unverified. Do not include files, hypothesis, paths, "
        "hashes, tool-call ids or any other field. Do not invent a source."
    )
    return system, user, notices


def parse_completion(text: str, sources: dict) -> LessonDraft | None:
    """Return an explicit none (None) or a validated draft, or raise controlled invalid.

    The reply may contain only ``lesson_status`` and ``lesson_draft``.  A draft must
    use currently registered source IDs and pass the existing draft size/field rules.
    """
    try:
        payload = parse_json(text)
    except (ValueError, TypeError, RecursionError):
        raise CompletionInvalid("invalid_json") from None
    if not isinstance(payload, dict) or "lesson_status" not in payload:
        raise CompletionInvalid("invalid_structure")
    if set(payload) - {"lesson_status", "lesson_draft"}:
        raise CompletionInvalid("invalid_structure")
    status = payload["lesson_status"]
    if status not in ("draft", "none"):
        raise CompletionInvalid("invalid_status")
    raw = payload.get("lesson_draft")
    if status == "none":
        if raw is not None:
            raise CompletionInvalid("invalid_status")
        return None
    if raw is None:
        raise CompletionInvalid("invalid_structure")
    try:
        draft = parse_lesson_draft(raw)
    except (ValueError, TypeError):
        raise CompletionInvalid("invalid_draft") from None
    if not set(draft.source_ids) <= set(sources):
        raise CompletionInvalid("invalid_sources")
    return draft


def merge_decision(original: dict, decision: LessonDraft | None) -> dict:
    """Add only the decision fields; preserve files, hypothesis and every original key."""
    merged = dict(original)
    if decision is None:
        merged["lesson_status"] = "none"
        merged["lesson_draft"] = None
    else:
        merged["lesson_status"] = "draft"
        merged["lesson_draft"] = {
            "situation": decision.situation,
            "observation": decision.observation,
            "lesson": decision.lesson,
            "source_ids": list(decision.source_ids),
        }
    return merged


def serialized(merged: dict) -> str:
    return json.dumps(merged, ensure_ascii=False, separators=(",", ":"))


__all__ = ["COMPLETION_SECONDS", "COMPLETION_MAX_TOKENS", "COMPLETION_SCHEMA",
           "CompletionInvalid", "build_messages", "parse_completion", "merge_decision",
           "serialized"]
