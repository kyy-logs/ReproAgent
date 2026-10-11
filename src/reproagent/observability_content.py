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
MAX_CONTENT_BYTES = 256 << 10
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


class CaptureIssues:
    """Bounded loss metadata, independent of whether a body could be stored."""
    def __init__(self):
        self.items = []
        self.dropped = 0

    def add(self, code, *, span_id=None, source=None, content_id=None,
            limit_scope=None, limit_value=None, original_bytes=None, captured_bytes=None):
        item = dict(code=code, severity="info" if code in
                    ("PROVIDER_NOT_RETURNED", "CAPTURE_DISABLED") else "warning",
                    span_id=span_id, source=source, content_id=content_id,
                    limit_scope=limit_scope, limit_value=limit_value,
                    original_bytes=original_bytes, captured_bytes=captured_bytes)
        for existing in self.items:
            if all(existing[k] == v for k, v in item.items()):
                existing["count"] += 1
                return
        if len(self.items) >= 128:
            self.dropped += 1
        else:
            self.items.append({**item, "count": 1})


REASONS = dict(disabled="CAPTURE_DISABLED", not_returned="PROVIDER_NOT_RETURNED",
               unsupported="UNSUPPORTED_CONTENT", incomplete="STREAM_INCOMPLETE",
               omitted_limit="LEGACY_UNSPECIFIED", capture_error="CAPTURE_EXCEPTION")


class ContentStore:
    """Redacted copies, with explicit bounded loss records and full-copy deduplication."""
    def __init__(self, *, secrets: Iterable[str] = ()) -> None:
        self._secrets = tuple(secret for secret in secrets if secret)
        self._records = []
        self._ids = {}
        self._total_bytes = 2
        self.complete = True
        self.omissions = []
        self._issues = CaptureIssues()

    def capture(self, *, owner_span_id, kind, value, source, availability=CAPTURED,
                reason_code=None, limit_scope=None, limit_value=None, original_bytes=None):
        try:
            if availability in MISSING_STATES:
                record = self._empty_record(owner_span_id, kind, source, availability,
                    reason_code=reason_code, limit_scope=limit_scope, limit_value=limit_value,
                    original_bytes=original_bytes)
                key = None
            else:
                projected = redact(value, self._secrets)
                text = _as_text(projected)
                if not text:
                    record = self._empty_record(owner_span_id, kind, source, "not_returned")
                    key = None
                else:
                    record = dict(content_id="0"*16, owner_span_id=owner_span_id, kind=kind, source=source,
                        availability=CAPTURED, text=text, original_bytes=len(text.encode("utf-8")),
                        captured_bytes=len(text.encode("utf-8")), truncated=False, redacted=projected != value,
                        excerpt_mode=None, reason_code=None, limit_scope=None, limit_value=None)
                    key = hashlib.sha256((kind+"|"+source+"|"+text).encode("utf-8")).hexdigest()
                    record = self._fit(record)
                    record["captured_bytes"] = len(record["text"].encode("utf-8"))
            return self._store(record, key)
        except Exception:
            return self._store(self._empty_record(owner_span_id, kind, source, "capture_error"))

    def _empty_record(self, owner_span_id, kind, source, availability, **metadata):
        if availability not in MISSING_STATES:
            availability = "capture_error"
        return dict(content_id="", owner_span_id=owner_span_id, kind=kind, source=source,
            availability=availability, text=None, original_bytes=metadata.get("original_bytes"),
            captured_bytes=0, truncated=False, redacted=False, excerpt_mode=None,
            reason_code=metadata.get("reason_code") or REASONS[availability],
            limit_scope=metadata.get("limit_scope"), limit_value=metadata.get("limit_value"))

    def _fit(self, record):
        if _serialized_size(record) <= MAX_CONTENT_BYTES:
            return record
        original = record["text"].encode("utf-8")
        base = {**record, "text":"", "truncated":True, "reason_code":"RECORD_BYTE_LIMIT",
                "limit_scope":"record_bytes", "limit_value":MAX_CONTENT_BYTES}
        half = max(1, (MAX_CONTENT_BYTES-_serialized_size(base)-len(EXCERPT_MARKER.encode()))//2)
        while True:
            head = original[:half].decode("utf-8", "ignore")
            tail = original[-half:].decode("utf-8", "ignore") if 2*half<len(original) else ""
            candidate = {**base,"text":head+EXCERPT_MARKER+tail,
                         "captured_bytes":len((head+EXCERPT_MARKER+tail).encode("utf-8")),
                         "excerpt_mode":"head_tail" if tail else "head"}
            if _serialized_size(candidate)<=MAX_CONTENT_BYTES:
                return candidate
            if half <= 1:
                return self._empty_record(record["owner_span_id"], record["kind"], record["source"],
                    "omitted_limit", reason_code="RECORD_BYTE_LIMIT", limit_scope="record_bytes",
                    limit_value=MAX_CONTENT_BYTES, original_bytes=record["original_bytes"])
            half=max(1,half*3//4)

    def _issue(self, record, code=None, *, limit_scope=None, limit_value=None):
        reason=code or record.get("reason_code")
        if not reason:
            return
        if reason not in ("PROVIDER_NOT_RETURNED", "CAPTURE_DISABLED"):
            self.complete=False
        self._issues.add(reason, span_id=record["owner_span_id"], source=record["source"],
            content_id=record.get("content_id") or None,
            limit_scope=limit_scope or record.get("limit_scope"),
            limit_value=limit_value if limit_value is not None else record.get("limit_value"),
            original_bytes=record.get("original_bytes"), captured_bytes=record.get("captured_bytes"))

    def _store(self, record, key=None):
        key=key or json.dumps({k:v for k,v in record.items() if k not in
                              ("content_id","owner_span_id")},ensure_ascii=False,sort_keys=True)
        cid=hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        if cid in self._ids:
            record["content_id"]=cid
            self._issue(record)
            return cid
        size=_serialized_size({**record,"content_id":cid})+(2 if self._records else 0)
        if len(self._records)>=MAX_CONTENT_ITEMS or self._total_bytes+size>MAX_CONTENTS_BYTES:
            if "omitted_limit" not in self.omissions: self.omissions.append("omitted_limit")
            item_limit=len(self._records)>=MAX_CONTENT_ITEMS
            self._issue({**record,"content_id":None,"captured_bytes":0},
                "CONTENT_ITEM_LIMIT" if item_limit else "CONTENT_TOTAL_BYTE_LIMIT",
                limit_scope="content_items" if item_limit else "content_total_bytes",
                limit_value=MAX_CONTENT_ITEMS if item_limit else MAX_CONTENTS_BYTES)
            return None
        record["content_id"]=cid
        self._ids[cid]=cid
        self._records.append(record)
        self._total_bytes+=size
        self._issue(record)
        return cid

    def records(self):
        return list(self._records)

    def issues(self):
        return [dict(item) for item in self._issues.items]


__all__ = ["CAPTURED", "ContentStore", "MAX_CONTENT_BYTES", "MAX_CONTENT_ITEMS",
           "MAX_CONTENTS_BYTES", "MISSING_STATES", "redact"]
