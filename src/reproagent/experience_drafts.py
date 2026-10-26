"""Task-scoped, bounded lesson drafts written beside exploration.

A draft is untrusted model prose, not an experience card and not execution evidence.
The session only keeps the latest well-formed draft; publishing a candidate never depends
on whether the optional draft parses or can be stored.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .core.serialization import canonical_bytes
from .paths import workspace_path
from .store import atomic_write

DRAFT_BYTES = 1024
DRAFT_PATH = Path("learning") / "draft.json"
FIELDS = ("situation", "observation", "lesson", "source_ids")
DRAFT_CODES = ("received", "none", "omitted", "invalid_decision", "invalid",
               "invalid_sources", "store_error")
DRAFT_EVENT = "experience.draft"


@dataclass(frozen=True, slots=True)
class LessonDraft:
    situation: str
    observation: str
    lesson: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CapturedDraft:
    draft: LessonDraft
    call_id: str
    event_seq: int
    arguments_hash: str


def _redact(value: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


def _draft_payload(draft: LessonDraft) -> dict:
    return {"situation": draft.situation, "observation": draft.observation,
            "lesson": draft.lesson, "source_ids": list(draft.source_ids)}


def parse_lesson_draft(raw: object, *, secrets: tuple[str, ...] = ()) -> LessonDraft:
    """Parse exactly the four draft fields, redact, and enforce the UTF-8 ceiling."""
    if not isinstance(raw, dict) or set(raw) != set(FIELDS):
        raise ValueError("invalid lesson draft fields")
    values = {}
    for name in ("situation", "observation", "lesson"):
        value = raw[name]
        if type(value) is not str or not value.strip():
            raise ValueError("invalid lesson draft text")
        values[name] = _redact(value.strip(), secrets)
    source_ids = raw["source_ids"]
    if not isinstance(source_ids, (list, tuple)) or not 1 <= len(source_ids) <= 3:
        raise ValueError("invalid lesson draft source ids")
    if any(type(item) is not str or not item.strip() for item in source_ids):
        raise ValueError("invalid lesson draft source ids")
    ids = tuple(_redact(item.strip(), secrets) for item in source_ids)
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate lesson draft source id")
    draft = LessonDraft(values["situation"], values["observation"], values["lesson"], ids)
    if len(canonical_bytes(_draft_payload(draft))) > DRAFT_BYTES:
        raise ValueError("lesson draft exceeds byte limit")
    return draft


class ExperienceDraftSession:
    """The latest valid draft of one task; failures never replace a valid earlier draft."""

    def __init__(self, store, *, secrets: tuple[str, ...] = ()) -> None:
        self.store = store
        self.secrets = tuple(secret for secret in secrets if secret)
        self.latest: CapturedDraft | None = None
        self.saw_invalid = False
        self.saw_none = False
        self.last_invalid_code = ''
        from .experience_sources import LearningSourceRegistry
        self.sources = LearningSourceRegistry(store, secrets=self.secrets)
        self._load()

    def register_source(self, fact):
        return self.sources.register(fact)

    def _path(self) -> Path:
        return workspace_path(self.store.root / DRAFT_PATH)

    def _load(self) -> None:
        try:
            data = json.loads(self._path().read_text(encoding="utf-8"))
            draft = parse_lesson_draft(data["draft"], secrets=self.secrets)
            if not set(draft.source_ids) <= self.sources.sources().keys():
                self.saw_invalid = True
                self.last_invalid_code = 'invalid_sources'
                return
            self.latest = CapturedDraft(draft, str(data["call_id"]), int(data["event_seq"]),
                                        str(data["arguments_hash"]))
        except (OSError, ValueError, TypeError, KeyError):
            self.latest = None

    def offer(self, raw: object, *, status: str | None = None,
              call_id: str, arguments_hash: str) -> str:
        if status not in (None, "draft", "none"):
            self.saw_invalid = True
            self.last_invalid_code = "invalid_decision"
            # The decision is model-supplied text: keep only controlled labels and a
            # bounded length, never the value itself.
            return self._record("invalid_decision", call_id=call_id,
                                arguments_hash=arguments_hash, decision="invalid",
                                decision_code="invalid_status",
                                status_length=len(status) if isinstance(status, str) else 0)
        if status == "none":
            if raw is not None:
                self.saw_invalid = True
                self.last_invalid_code = "invalid_decision"
                return self._record("invalid_decision", call_id=call_id,
                                    arguments_hash=arguments_hash, decision="none")
            self.saw_none = True
            return self._record("none", call_id=call_id, arguments_hash=arguments_hash)
        if raw is None:
            if status == "draft":
                self.saw_invalid = True
                self.last_invalid_code = "invalid_decision"
                return self._record("invalid_decision", call_id=call_id,
                                    arguments_hash=arguments_hash, decision="draft")
            return self._record("omitted", call_id=call_id, arguments_hash=arguments_hash)
        try:
            draft = parse_lesson_draft(raw, secrets=self.secrets)
        except ValueError:
            self.saw_invalid = True
            self.last_invalid_code = 'invalid'
            size = len(canonical_bytes(raw).decode("utf-8") if isinstance(raw, dict) else "")
            if status == "draft":
                # An explicit draft decision with an unusable draft is a bad decision
                # (spec 3.1), not the older missing-status format/source failure.
                return self._record("invalid_decision", call_id=call_id,
                    arguments_hash=arguments_hash, format_state='invalid',
                    source_state='not_checked', bytes=size,
                    source_count=self._source_count(raw))
            return self._record("invalid", call_id=call_id, arguments_hash=arguments_hash,
                                bytes=size, source_count=self._source_count(raw))
        if not set(draft.source_ids) <= self.sources.sources().keys():
            self.saw_invalid = True
            self.last_invalid_code = 'invalid_sources'
            if status == "draft":
                return self._record("invalid_decision", call_id=call_id,
                    arguments_hash=arguments_hash, format_state='valid',
                    source_state='unknown_source', source_ids=list(draft.source_ids))
            return self._record('invalid_sources', call_id=call_id, arguments_hash=arguments_hash,
                format_state='valid', source_state='unknown_source', source_ids=list(draft.source_ids))
        event = self._append("received", call_id=call_id, arguments_hash=arguments_hash,
                             format_state='valid', source_state='verified', source_ids=list(draft.source_ids),
                             bytes=len(canonical_bytes(_draft_payload(draft))),
                             source_count=len(draft.source_ids))
        if event is None:
            return "store_error"
        captured = CapturedDraft(draft, call_id, event.seq, arguments_hash)
        content = canonical_bytes({"draft": _draft_payload(draft), "call_id": call_id,
                                   "event_seq": event.seq, "arguments_hash": arguments_hash})
        try:
            atomic_write(self._path(), content)
        except OSError:
            self._append("store_error", call_id=call_id, arguments_hash=arguments_hash)
            return "store_error"
        self.latest = captured
        return "received"

    def _source_count(self, raw):
        if not isinstance(raw, dict):
            return 0
        ids = raw.get("source_ids")
        return len(ids) if isinstance(ids, (list, tuple)) else 0

    def _append(self, code: str, *, call_id: str, arguments_hash: str, **fields):
        payload = {"code": code, "call_id": call_id, "arguments_hash": arguments_hash, **fields}
        try:
            return self.store.append_event(DRAFT_EVENT, (), payload)
        except (OSError, ValueError, TypeError):
            return None

    def _record(self, code: str, *, call_id: str, arguments_hash: str, **fields) -> str:
        return code if self._append(code, call_id=call_id, arguments_hash=arguments_hash, **fields) else "store_error"


__all__ = ["CapturedDraft", "DRAFT_BYTES", "DRAFT_CODES", "ExperienceDraftSession", "LessonDraft",
           "parse_lesson_draft"]
