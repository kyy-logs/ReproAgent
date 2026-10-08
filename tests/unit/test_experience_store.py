import importlib
import json
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from reproagent.core.models import TaskRequest, EvidenceRef
from reproagent.core.serialization import canonical_bytes, canonical_hash, encode_record, decode_record


def exp():
    return importlib.import_module("reproagent.experience")


def card(summary="advice", detail="Observe this only in the documented situation."):
    key = {"category": "model", "tags": ["pytest"], "summary": " ".join(summary.casefold().split())}
    return exp().ExperienceCard("exp_" + canonical_hash(key), "model", ("pytest",),
        summary, detail, "round-task@0123",
        (EvidenceRef("learning/evidence.jsonl", "a" * 64, 1, 1),))


def library(path, *cards):
    path.write_bytes(canonical_bytes({"schema_version": 1, "items": [asdict(c) for c in cards]}))


def test_old_request_defaults_disable_experience(tmp_path):
    request = decode_record("request", encode_record(TaskRequest(tmp_path, tmp_path / "out", tmp_path / "issue")))
    assert request.experience_file is None
    assert request.learn_experience is True


def test_relative_experience_path_uses_config_directory(tmp_path, monkeypatch):
    from reproagent.cli import load_request
    config = tmp_path / "config.json"
    data = encode_record(TaskRequest(tmp_path / "repo", tmp_path / "out", tmp_path / "issue"))
    data.update(experience_file="../shared/experiences.json", learn_experience=False)
    config.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)
    loaded = load_request(config)
    assert loaded.experience_file == (tmp_path / "../shared/experiences.json").resolve()
    assert loaded.learn_experience is False


def test_missing_and_valid_library(tmp_path):
    path = tmp_path / "experiences.json"
    snapshot = exp().load_experience_snapshot(path)
    assert snapshot.state == "missing" and not path.exists()
    library(path, card())
    snapshot = exp().load_experience_snapshot(path)
    assert snapshot.state == "ready" and len(snapshot.cards) == 1
    assert snapshot.cards[0].summary == "advice"
    assert len(snapshot.content_hash) == 64


@pytest.mark.parametrize("raw", [
    '{"schema_version":1,"schema_version":1,"items":[]}',
    '{"schema_version":true,"items":[]}', '{"schema_version":2,"items":[]}',
    '{"schema_version":1,"items":[],"extra":1}', '{"schema_version":1,"items":[NaN]}',
    '{"schema_version":1,"items":[{}]}',
])
def test_category_and_json_are_strict(tmp_path, raw):
    path = tmp_path / "experiences.json"
    path.write_text(raw, encoding="utf-8")
    before = path.read_bytes()
    assert exp().load_experience_snapshot(path).state == "store_error"
    assert exp().append_experience(path, card(), None) == "store_error"
    assert path.read_bytes() == before


@pytest.mark.parametrize("changes", [
    {"category": "custom"}, {"tags": ("a",) * 6}, {"summary": ""},
    {"detail": ""}, {"tags": ("",)}, {"tags": ("a" * 65,)},
    {"evidence_refs": ()}, {"evidence_refs": (EvidenceRef("../outside", "a" * 64),)},
])
def test_invalid_card_is_not_written(tmp_path, changes):
    path = tmp_path / "experiences.json"
    assert exp().append_experience(path, replace(card(), **changes), None) == "invalid"
    assert not path.exists()


def test_card_utf8_limit_includes_metadata(tmp_path):
    path = tmp_path / "experiences.json"
    assert exp().append_experience(path, card(detail=chr(0x4e2d) * 2100), None) == "invalid"
    assert exp().append_experience(path, card(), None) == "written"
    assert len(canonical_bytes(asdict(exp().load_experience_snapshot(path).cards[0]))) <= 2048


def test_duplicate_preserves_old_detail(tmp_path):
    path = tmp_path / "experiences.json"
    assert exp().append_experience(path, card(), None) == "written"
    before = path.read_bytes()
    duplicate = card(summary="  ADVICE" + chr(10) + " ", detail="A newer explanation must not replace the old one.")
    assert exp().append_experience(path, duplicate, None) == "duplicate"
    assert path.read_bytes() == before


def test_atomic_replace_failure_preserves_library(tmp_path, monkeypatch):
    path = tmp_path / "experiences.json"
    library(path, card())
    before = path.read_bytes()
    def fail(*args):
        raise OSError("replace refused")
    monkeypatch.setattr(exp(), "atomic_write", fail)
    assert exp().append_experience(path, card("another"), None) == "store_error"
    assert path.read_bytes() == before


def test_library_limit_checked_after_append(tmp_path):
    path = tmp_path / "experiences.json"
    # Whitespace is legal JSON, but the bounded reader must reject a physically huge file.
    path.write_bytes(b'{"schema_version":1,"items":[]}' + b" " * (1024 * 1024))
    before = path.read_bytes()
    assert exp().load_experience_snapshot(path).state == "store_error"
    assert exp().append_experience(path, card(), None) == "store_error"
    assert path.read_bytes() == before


def test_busy_writer_preserves_library(tmp_path):
    path = tmp_path / "experiences.json"
    library(path, card())
    before = path.read_bytes()
    script = """import os,sys,time
f=open(sys.argv[1], 'a+b'); f.seek(0); f.write(b'x'); f.flush(); f.seek(0)
if os.name=='nt':
 import msvcrt
 msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
else:
 import fcntl
 fcntl.flock(f, fcntl.LOCK_EX|fcntl.LOCK_NB)
print('locked',flush=True)
sys.stdin.readline()
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(path) + ".lock"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        assert exp().append_experience(path, card("another"), None) == "busy"
        assert path.read_bytes() == before
    finally:
        child.communicate(chr(10), timeout=10)
    assert exp().append_experience(path, card("another"), None) == "written"


@pytest.mark.parametrize("inside", ["repo", "out", "fixed"])
def test_library_cannot_live_in_repo_output_or_fixed(tmp_path, inside):
    from reproagent.core.models import FixValidationRequest
    for name in ("repo", "out", "fixed"):
        (tmp_path / name).mkdir()
    request = TaskRequest(tmp_path / "repo", tmp_path / "out", tmp_path / "issue")
    with pytest.raises(ValueError):
        exp().validate_experience_path(tmp_path / inside / "exp.json", request,
            FixValidationRequest(tmp_path / "fixed"))
    assert not (tmp_path / inside / "exp.json").exists()

def test_library_limit_checked_after_real_append(tmp_path):
    path = tmp_path / "experiences.json"
    items = []
    size = len(canonical_bytes({"schema_version": 1, "items": []}))
    index = 0
    while True:
        next_card = card("entry " + str(index), detail="x" * 1400)
        increment = len(canonical_bytes(asdict(next_card))) + (1 if items else 0)
        if size + increment > 1024 * 1024:
            break
        items.append(asdict(next_card))
        size += increment
        index += 1
    content = canonical_bytes({"schema_version": 1, "items": items})
    path.write_bytes(content)
    assert exp().load_experience_snapshot(path).state == "ready"
    assert exp().append_experience(path, next_card, None) == "store_error"
    assert path.read_bytes() == content


def test_exact_card_byte_boundary(tmp_path):
    path = tmp_path / "experiences.json"
    overhead = len(canonical_bytes(asdict(card(detail=""))))
    accepted = card(detail="x" * (2048 - overhead))
    assert len(canonical_bytes(asdict(accepted))) == 2048
    assert exp().append_experience(path, accepted, None) == "written"
    rejected = card(summary="another", detail=accepted.detail + chr(0x4e2d))
    assert exp().append_experience(tmp_path / "oversize.json", rejected, None) == "invalid"


def test_library_symlink_cannot_escape_into_fixed_repo(tmp_path):
    import os
    from reproagent.core.models import FixValidationRequest
    target = tmp_path / "fixed"
    target.mkdir()
    link = tmp_path / "linked"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("link creation unavailable")
    request = TaskRequest(tmp_path / "repo", tmp_path / "out", tmp_path / "issue")
    with pytest.raises(ValueError):
        exp().validate_experience_path(link / "exp.json", request, FixValidationRequest(target))
