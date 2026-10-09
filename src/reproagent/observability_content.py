"""Bounded, redacted copies of what a model saw and produced.

This is the part of a trace a person reads directly, so it carries the opposite risk
from the metadata layer: it holds prose, and prose can hold credentials and can grow
without limit.  Two rules follow, and everything here serves them.

*Nothing is redacted in place.*  Every record is built from copies, so capturing a
request cannot change the request the model was actually sent.

*A missing figure is never dressed up.*  Content that was never returned, that the
budget dropped, or that could not be copied is recorded with the availability that
says so -- never as an empty string that reads like an empty answer.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

#: How much content one run may keep, and how much room it has to keep it in.
MAX_CONTENT_ITEMS = 512
MAX_CONTENT_BYTES = 32768
MAX_CONTENTS_BYTES = 4 << 20

CAPTURED = "captured"

#: The states a capture can be missing in.  Each says something different about why.
MISSING_STATES = frozenset({
    "disabled",          # the operator turned content capture off
    "not_returned",      # the provider never sent it -- normal, not a failure
    "unsupported",       # a kind this recorder does not copy (binary blocks, streams)
    "incomplete",        # a stream ended before a final result existed
    "omitted_limit",     # it existed, and the budget dropped it
    "capture_error",     # copying it failed
})

#: Keys whose value is a credential whatever it looks like.
SENSITIVE_KEYS = frozenset({
    "authorization", "api_key", "apikey", "api-key", "password", "passwd", "secret",
    "client_secret", "cookie", "set-cookie", "token", "access_token", "refresh_token",
})

REDACTED = "<redacted>"
EXCERPT_MARKER = "\n[... excerpt: content truncated; the head and tail are shown ...]\n"


def redact(value: Any, secrets: Iterable[str] = ()) -> Any:
    """A copy of ``value`` with known credentials removed.

    Redaction happens on copies and covers the JSON-escaped form of a secret as well
    as the literal one: a credential that reached the provider inside a JSON string
    comes back escaped, and a literal-only search would walk straight past it.
    """
    known = tuple(secret for secret in secrets if secret)
    return _redact(value, known)


def _redact(value: Any, secrets: tuple, *, key: str | None = None) -> Any:
    if key is not None and key.lower() in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, REDACTED)
            escaped = json.dumps(secret)[1:-1]
            if escaped != secret:
                value = value.replace(escaped, REDACTED)
        return value
    if isinstance(value, dict):
        return {str(item_key): _redact(item, secrets, key=str(item_key))
                for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, secrets) for item in value]
    return value


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _serialized_size(record: dict) -> int:
    return len(json.dumps(record, ensure_ascii=False).encode("utf-8"))


class ContentStore:
    """Collects the bounded, redacted content of one task.

    Deduplicated by what the content *is*: a message that appears in two requests is
    stored once, and two spans that saw it refer to the same id.
    """

    def __init__(self, *, secrets: Iterable[str] = ()) -> None:
        self._secrets = tuple(secret for secret in secrets if secret)
        self._records: list[dict] = []
        self._ids: dict[str, str] = {}
        self._total_bytes = 0
        self.complete = True
        self.omissions: list[str] = []

    # -- capture -------------------------------------------------------------

    def capture(self, *, owner_span_id: str, kind: str, value: Any, source: str,
                availability: str = CAPTURED) -> str | None:
        """Store one redacted copy, or record why there is none.

        Returns:
            The content id, or None when nothing was stored.
        """
        try:
            return self._capture(owner_span_id, kind, value, source, availability)
        except Exception:  # noqa: BLE001 - observation never escapes
            # Even the failure is recorded as a state, not as silence.
            self._fail(availability="capture_error", owner_span_id=owner_span_id,
                       kind=kind, source=source)
            return None

    def _capture(self, owner_span_id: str, kind: str, value: Any, source: str,
                 availability: str) -> str | None:
        if availability in MISSING_STATES:
            return self._store(self._empty_record(owner_span_id, kind, source, availability))

        projected = redact(value, self._secrets)
        text = _as_text(projected)
        if not text:
            return self._store(self._empty_record(owner_span_id, kind, source, "not_returned"))

        record = {
            "content_id": "",
            "owner_span_id": owner_span_id,
            "kind": kind,
            "source": source,
            "availability": CAPTURED,
            "text": text,
            "original_bytes": len(text.encode("utf-8")),
            "captured_bytes": 0,
            "truncated": False,
            "redacted": projected != value,
            "excerpt_mode": None,
        }
        record = self._fit(record)
        record["captured_bytes"] = len(record["text"].encode("utf-8"))
        return self._store(record)

    def _empty_record(self, owner_span_id: str, kind: str, source: str, availability: str) -> dict:
        self.complete = False
        if availability not in MISSING_STATES:  # pragma: no cover - callers pass known states
            availability = "capture_error"
        if availability == "omitted_limit":
            self._omit(availability)
        return {"content_id": "", "owner_span_id": owner_span_id, "kind": kind, "source": source,
                "availability": availability, "text": None, "original_bytes": 0,
                "captured_bytes": 0, "truncated": False, "redacted": False, "excerpt_mode": None}

    def _fit(self, record: dict) -> dict:
        """Shrink the text until the whole record, envelope included, fits the ceiling."""
        if _serialized_size(record) <= MAX_CONTENT_BYTES:
            return record
        status = record["availability"]
        original = record["text"]
        raw = original.encode("utf-8")
        room = MAX_CONTENT_BYTES - _serialized_size({**record, "text": ""}) - len(EXCERPT_MARKER.encode("utf-8"))
        half = max(1, room // 2)
        while True:
            head = raw[:half].decode("utf-8", "ignore")
            tail = raw[-half:].decode("utf-8", "ignore") if half * 2 < len(raw) else ""
            excerpt = head + EXCERPT_MARKER + tail
            candidate = {**record, "availability": status, "text": excerpt, "truncated": True,
                         "excerpt_mode": "head_tail" if tail else "head"}
            if _serialized_size(candidate) <= MAX_CONTENT_BYTES or half <= 1:
                self.complete = False
                return candidate
            half = half * 3 // 4

    def _omit(self, reason: str) -> None:
        if reason not in self.omissions:
            self.omissions.append(reason)
        self.complete = False

    def _fail(self, *, availability: str, owner_span_id: str, kind: str, source: str) -> None:
        self.complete = False
        self._store(self._empty_record(owner_span_id, kind, source, availability))

    def _store(self, record: dict) -> str | None:
        key = json.dumps({k: record[k] for k in ("kind", "source", "availability", "text",
                                                 "truncated", "excerpt_mode")},
                         ensure_ascii=False, sort_keys=True)
        content_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        if content_id in self._ids:
            return content_id
        record["content_id"] = content_id
        size = _serialized_size(record)
        if len(self._records) >= MAX_CONTENT_ITEMS or self._total_bytes + size > MAX_CONTENTS_BYTES:
            # Over budget: the entry is not stored at all, and the run says so.
            self._omit("omitted_limit")
            return None
        self._ids[content_id] = content_id
        self._total_bytes += size
        self._records.append(record)
        if record["availability"] in MISSING_STATES or record["truncated"]:
            self.complete = False
        return content_id

    # -- output --------------------------------------------------------------

    def records(self) -> list[dict]:
        return list(self._records)


__all__ = ["CAPTURED", "ContentStore", "MAX_CONTENT_BYTES", "MAX_CONTENT_ITEMS",
           "MAX_CONTENTS_BYTES", "MISSING_STATES", "redact"]
