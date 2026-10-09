"""Bounded, redacted copies of what the model saw and produced.

The content layer is the part of a trace a person reads directly, so it has two jobs
at once: show enough to review a decision, and never show a credential, never grow
without bound, and never touch the object it copied from.
"""
from __future__ import annotations

import json

import pytest

from reproagent.observability_content import (
    MAX_CONTENT_BYTES,
    MAX_CONTENT_ITEMS,
    MAX_CONTENTS_BYTES,
    ContentStore,
    redact,
)


def capture(store, value, *, kind="wire_request", source="wire_request", availability="captured",
            owner="a" * 16):
    return store.capture(owner_span_id=owner, kind=kind, value=value,
                         source=source, availability=availability)


def test_a_captured_entry_is_a_redacted_copy():
    store = ContentStore(secrets=("sk-live-abcdef0123456789",))
    payload = {"messages": [{"role": "user", "content": "compare with sk-live-abcdef0123456789"}],
               "tools": [{"name": "Read"}]}

    content_id = capture(store, payload)

    assert content_id
    record = store.records()[0]
    assert "sk-live-abcdef0123456789" not in json.dumps(record)
    assert "<redacted>" in json.dumps(record)
    # The caller's object is untouched: the capture is a copy.
    assert payload["messages"][0]["content"] == "compare with sk-live-abcdef0123456789"
    assert record["availability"] == "captured"
    assert record["source"] == "wire_request"
    assert record["owner_span_id"] == "a" * 16


def test_escaped_and_keyed_credentials_are_redacted():
    # A credential can arrive JSON-escaped, or under a name that always means secret.
    escaped = 'api key: sk-live-"quoted"'
    payload = {"Authorization": "Bearer tok_123", "note": json.dumps({"k": escaped}),
               "nested": {"api_key": "abc123", "password": "hunter2"}}

    store = ContentStore(secrets=(escaped,))
    assert capture(store, payload) is not None

    blob = json.dumps(store.records()[0])
    assert escaped not in blob
    assert "tok_123" not in blob and "abc123" not in blob and "hunter2" not in blob


def test_the_same_content_is_stored_once():
    store = ContentStore()
    payload = {"messages": [{"role": "user", "content": "identical"}]}

    first = capture(store, payload, owner="a" * 16)
    second = capture(store, dict(payload), owner="b" * 16)

    # The same bytes under a different span is still one stored entry...
    assert first == second
    assert len(store.records()) == 1


def test_an_oversized_entry_keeps_a_marked_head_and_tail():
    store = ContentStore()
    text = "HEAD" + ("x" * 100_000) + "TAIL"

    capture(store, {"content": text})

    record = store.records()[0]
    assert record["truncated"] is True
    # original_bytes measures the projected content before redaction and truncation, so
    # it covers the text plus the JSON envelope the projection puts around it.
    assert record["original_bytes"] >= len(text.encode("utf-8"))
    assert record["captured_bytes"] < record["original_bytes"]
    assert record["excerpt_mode"] in ("head_tail", "head")
    # Both ends survive: a head-only excerpt hides the newest tool message.
    assert "HEAD" in json.dumps(record) and "TAIL" in json.dumps(record)
    # ...and the whole entry still fits the per-entry ceiling.
    assert len(json.dumps(record, ensure_ascii=False).encode("utf-8")) <= MAX_CONTENT_BYTES


def test_byte_limits_are_measured_in_utf8_not_characters():
    store = ContentStore()
    # 2 bytes per character: a character count would pass what a byte count must cut.
    capture(store, {"content": "中" * 40_000})

    record = store.records()[0]
    # 40_000 characters, three bytes each: a character count would report ~40k here.
    assert record["original_bytes"] >= 120_000
    assert len(json.dumps(record, ensure_ascii=False).encode("utf-8")) <= MAX_CONTENT_BYTES


@pytest.mark.parametrize("availability", ["disabled", "not_returned", "unsupported", "incomplete",
                                          "capture_error", "omitted_limit"])
def test_every_missing_state_is_recorded_rather_than_an_empty_string(availability):
    store = ContentStore()
    payload = {"content": "<script>alert(1)</script>"} if availability == "capture_error" else None

    capture(store, payload, availability=availability, source="provider_response")

    record = store.records()[0]
    assert record["availability"] == availability
    assert record["text"] in (None, "")
    # A missing figure is never dressed up as content that was simply empty.
    assert record["captured_bytes"] == 0
    assert store.complete is False


def test_capture_never_raises_and_marks_its_own_failure():
    class Exploding:
        def __repr__(self):
            raise RuntimeError("cannot be represented")

    store = ContentStore()
    capture(store, {"content": Exploding()}, source="provider_response")

    record = store.records()[0]
    assert record["availability"] == "capture_error"
    assert store.complete is False


def test_the_item_ceiling_drops_later_entries_and_says_so():
    store = ContentStore()
    for index in range(MAX_CONTENT_ITEMS + 5):
        capture(store, {"content": f"entry-{index}"})

    assert len(store.records()) == MAX_CONTENT_ITEMS
    assert store.complete is False
    assert "omitted_limit" in store.omissions


def test_the_total_ceiling_stops_capture_and_is_reported():
    store = ContentStore()
    # Each entry is under the per-entry ceiling but the run cannot hold many of them.
    chunk = {"content": "y" * (MAX_CONTENT_BYTES - 200)}
    for index in range(400):
        capture(store, {"content": f"{index}-" + chunk["content"]})

    total = sum(len(json.dumps(record, ensure_ascii=False).encode("utf-8")) for record in store.records())
    assert total <= MAX_CONTENTS_BYTES
    assert store.complete is False
    assert "omitted_limit" in store.omissions


def test_redaction_leaves_ordinary_text_alone():
    value = {"content": "the parser raises IndexError on an empty list"}

    assert redact(value, ()) == value
