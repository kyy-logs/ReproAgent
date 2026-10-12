import json
from dataclasses import asdict

import pytest

from reproagent import experience as exp
from reproagent.core.models import EvidenceRef
from reproagent.core.serialization import canonical_bytes, bytes_hash
from reproagent.store import TaskStore
from tests.unit.test_experience_store import card


def learning_fixture(tmp_path, *, code="SOURCE_RANGE_NOT_DISPLAYED", cited=True):
    store = TaskStore(tmp_path / "task")
    entry = {"id": "E1", "kind": "tool_error", "text": json.dumps({
        "tool": "revise_contract", "state": "error", "link_state": "matched",
        "arguments": {"source_refs": []}, "error_code": code,
        "result": "untrusted error prose"})}
    issue = {"id": "E2", "kind": "issue", "text": "logging format"}
    raw = b"\n".join(canonical_bytes(e) for e in (entry, issue)) + b"\n"
    path = store.root / "learning/evidence.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    refs = {e["id"]: EvidenceRef("learning/evidence.jsonl", bytes_hash(raw), i+1, i+1)
            for i, e in enumerate((entry, issue))}
    material = exp.LearningInput({"evidence": [entry, issue]}, refs, "task@hash", 0)
    proposal = {"experience": {"category": "workflow", "tags": ["revise_contract", "logging"],
        "summary": "When a source range is rejected, obtain the displayed range.",
        "detail": "Observed rejection; recovery remains unverified.",
        "evidence_ids": ["E1" if cited else "E2"]}}
    return store, material, proposal


def test_cited_error_generates_trigger_and_normalized_keys(tmp_path):
    store, material, proposal = learning_fixture(tmp_path)
    learned = exp.validate_experience(proposal, material, store)
    assert [asdict(t) for t in learned.triggers] == [
        {"kind": "tool_error", "tool": "revise_contract", "code": "SOURCE_RANGE_NOT_DISPLAYED"}]
    assert "tool:revise_contract" in learned.tags
    assert "error:source_range_not_displayed" in learned.tags
    assert len(learned.tags) <= 5
    assert len(canonical_bytes(asdict(learned))) <= 2048


def test_uncited_error_is_not_used_as_applicability(tmp_path):
    store, material, proposal = learning_fixture(tmp_path, cited=False)
    learned = exp.validate_experience(proposal, material, store)
    assert learned.triggers == ()


def test_unknown_error_and_forged_tag_do_not_create_trigger(tmp_path):
    store, material, proposal = learning_fixture(tmp_path, code="UNCONTROLLED_PROVIDER_TEXT")
    proposal["experience"]["tags"] = ["error:source_range_not_displayed"]
    learned = exp.validate_experience(proposal, material, store)
    assert learned.triggers == ()
    assert not any(tag.startswith("error:") for tag in learned.tags)


def test_legacy_card_missing_trigger_metadata_keeps_identity(tmp_path):
    old = asdict(card())
    old.pop("triggers", None)
    path = tmp_path / "library.json"
    path.write_bytes(canonical_bytes({"schema_version": 1, "items": [old]}))
    loaded = exp.load_experience_snapshot(path)
    assert loaded.state == "ready"
    assert loaded.cards[0].id == old["id"]
    assert loaded.cards[0].triggers == ()


def test_trigger_conditions_participate_in_identity(tmp_path):
    store, material, proposal = learning_fixture(tmp_path)
    learned = exp.validate_experience(proposal, material, store)
    other = dict(proposal["experience"])
    other["evidence_ids"] = ["E2"]
    plain = exp.validate_experience({"experience": other}, material, store)
    assert learned.id != plain.id


def test_trigger_derivation_keeps_original_validation(tmp_path):
    store, material, proposal = learning_fixture(tmp_path)
    proposal["experience"]["tags"] = ["tag"] * 6
    with pytest.raises(exp.LearningValidationError) as error:
        exp.validate_experience(proposal, material, store)
    assert error.value.code == "invalid_fields"


def test_reserved_tag_namespace_cannot_be_forged_with_whitespace(tmp_path):
    store, material, proposal = learning_fixture(tmp_path, cited=False)
    proposal["experience"]["tags"] = [" Tool:revise_contract ", " ERROR:source_range_not_displayed "]
    learned = exp.validate_experience(proposal, material, store)
    assert learned.tags == ()


@pytest.mark.parametrize("fact", [
    {"kind":"tool_error", "tool":"Read", "code":"SOURCE_RANGE_NOT_DISPLAYED"},
    {"kind":"permission", "tool":"write_candidate", "code":"RESERVED_FOR_PUBLISHING"},
    {"kind":"permission", "tool":"Read", "code":"ALLOWED"},
    {"kind":"protocol_error", "tool":"Read", "code":"MULTIPLE_TOOL_CALLS"},
    {"kind":"tool_error", "tool":"revise_contract", "code":False},
])
def test_unknown_or_impossible_conditions_cannot_be_stored(fact):
    from reproagent.experience_triggers import trigger_from_fact
    assert trigger_from_fact(fact) is None


def test_small_controlled_error_fact_keeps_trigger_without_large_arguments():
    from reproagent.experience_triggers import evidence_triggers
    entry = {"kind":"tool_error","text":json.dumps({"seq":5,"kind":"exploration.tool_error",
        "tool":"revise_contract","code":"SOURCE_RANGE_NOT_DISPLAYED"})}
    assert [asdict(t) for t in evidence_triggers(entry)] == [
        {"kind":"tool_error","tool":"revise_contract","code":"SOURCE_RANGE_NOT_DISPLAYED"}]


def test_exact_legacy_card_boundary_survives_read_and_append(tmp_path):
    legacy = asdict(card(detail=""))
    legacy.pop("triggers", None)
    overhead = len(canonical_bytes(legacy))
    legacy['detail'] = 'x'*(2048-overhead)
    assert len(canonical_bytes(legacy)) == 2048
    path = tmp_path/'library.json'
    path.write_bytes(canonical_bytes({'schema_version':1,'items':[legacy]}))
    original = path.read_bytes()
    loaded = exp.load_experience_snapshot(path)
    assert loaded.state == 'ready'
    assert path.read_bytes() == original
    assert exp.append_experience(path,card('other'),None) == 'written'
    stored = json.loads(path.read_text(encoding='utf-8'))['items']
    assert canonical_bytes(stored[0]) == canonical_bytes(legacy)
    assert all(len(canonical_bytes(item)) <= 2048 for item in stored)


@pytest.mark.parametrize("fact", [
    {"kind":"exploration.tool_error","tool":"write_candidate","code":"GROUNDED_CONTRACT_REQUIRED"},
    {"tool":"write_candidate","state":"error","link_state":"matched","arguments":{"files":[]},"error_code":"GROUNDED_CONTRACT_REQUIRED"},
])
def test_grounded_frozen_fact_derives_exact_condition(fact):
    from reproagent.experience_triggers import evidence_triggers
    assert [asdict(t) for t in evidence_triggers({"kind":"tool_error","text":json.dumps(fact)})] == [
        {"kind":"tool_error","tool":"write_candidate","code":"GROUNDED_CONTRACT_REQUIRED"}]


@pytest.mark.parametrize('action,expected',[('run_candidate',True),('submit_candidate',False),('Read',False)])
def test_only_actual_initial_review_overflow_action_is_triggerable(action,expected):
    from reproagent.experience_triggers import evidence_triggers
    entry = {'kind':'tool_error','text':json.dumps({'kind':'action.rejected','action':action,
        'code':'REVIEW_CONTEXT_TOO_LARGE'})}
    result = evidence_triggers(entry)
    assert bool(result) is expected
    if expected:
        assert [asdict(t) for t in result] == [{'kind':'controller_error','tool':'','code':'REVIEW_CONTEXT_TOO_LARGE'}]
