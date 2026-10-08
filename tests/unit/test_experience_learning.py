from dataclasses import replace
import pytest
from reproagent.core.models import TaskState
from reproagent.core.serialization import bytes_hash, canonical_bytes
from reproagent.store import atomic_write
from tests.integration.test_exporter import export_setup
from tests.unit.test_experience_store import exp


def material_setup(tmp_path, projects, facts):
    _, result, store, candidate, execution, snapshot, request = export_setup(tmp_path, projects, facts)
    result = replace(result, status=TaskState.DONE, export_state="published")
    atomic_write(store.root / "input/issue.md", (snapshot.root / "issue.md").read_bytes())
    store.save_record("task", result.task_id, result)
    material = exp().build_learning_input(store, result, facts.context())
    return material, store, result, candidate, execution


def proposed(ids):
    return {"experience": {"category": "model", "tags": ["pytest"],
        "summary": "Confirm expected behaviour before an exception assertion.",
        "detail": "Condition: an exception is observed. Observation: the test failed. Read current evidence before choosing an assertion.",
        "evidence_ids": ids}}


def test_learning_input_excludes_fixed_and_answers(tmp_path, projects, facts):
    _, result, store, candidate, execution, snapshot, request = export_setup(tmp_path, projects, facts)
    atomic_write(store.root / "input/issue.md", (snapshot.root / "issue.md").read_bytes())
    fixed = replace(execution, run_id="run-fixed-test", execution_role="fixed",
        observation=replace(execution.observation, tests=({"longrepr": "HIDDEN_PATCH"},)))
    store.save_record("runs", fixed.run_id, fixed)
    atomic_write(store.root / "report.json", b"HIDDEN_REPORT")
    atomic_write(store.root / "input/experiences.json", b"OLD_EXPERIENCE")
    material = exp().build_learning_input(store, replace(result, status=TaskState.DONE), facts.context())
    assert material is not None
    text = canonical_bytes(material.payload).decode()
    assert all(marker not in text for marker in ("HIDDEN_PATCH", "HIDDEN_REPORT", "OLD_EXPERIENCE"))
    assert len(canonical_bytes(material.payload)) <= 8192


def test_learning_evidence_survives_event_append(tmp_path, projects, facts):
    material, store, *_ = material_setup(tmp_path, projects, facts)
    ref = next(iter(material.evidence_map.values()))
    path = store.root / ref.path
    before = path.read_bytes()
    store.append_event("experience.learning", (), {"code": "empty"})
    assert bytes_hash(path.read_bytes()) == ref.content_hash
    assert path.read_bytes() == before
    assert 1 <= ref.start_line <= ref.end_line <= len(before.splitlines())


def test_learning_ids_are_mapped_by_program(tmp_path, projects, facts):
    material, store, *_ = material_setup(tmp_path, projects, facts)
    identity = next(iter(material.evidence_map))
    card = exp().validate_experience(proposed([identity]), material, store)
    assert card.source_task_id == material.source_task_id
    assert card.evidence_refs == (material.evidence_map[identity],)
    assert card.id.startswith("exp_") and len(card.id) == 68


@pytest.mark.parametrize("mutation", ["unknown_id", "duplicate_id", "category", "extra", "oversize", "changed_source"])
def test_learning_validation_rejects_invalid_proposals(tmp_path, projects, facts, mutation):
    material, store, *_ = material_setup(tmp_path, projects, facts)
    identity = next(iter(material.evidence_map))
    response = proposed([identity])
    if mutation == "unknown_id": response["experience"]["evidence_ids"] = ["E999"]
    if mutation == "duplicate_id": response["experience"]["evidence_ids"] = [identity, identity]
    if mutation == "category": response["experience"]["category"] = "custom"
    if mutation == "extra": response["experience"]["path"] = "/outside"
    if mutation == "oversize": response["experience"]["detail"] = "x" * 4096
    if mutation == "changed_source": atomic_write(store.root / material.evidence_map[identity].path, b"altered")
    with pytest.raises(ValueError):
        exp().validate_experience(response, material, store)


def test_null_experience_is_empty(tmp_path, projects, facts):
    material, store, *_ = material_setup(tmp_path, projects, facts)
    assert exp().validate_experience({"experience": None}, material, store) is None


def test_learning_input_overflow_skips_instead_of_truncating(tmp_path, projects, facts):
    _, result, store, _, _, _, _ = export_setup(tmp_path, projects, facts)
    atomic_write(store.root / "input/issue.md", b"x" * 9000)
    assert exp().build_learning_input(store, replace(result, status=TaskState.DONE), facts.context()) is None
    assert not (store.root / "learning/evidence.jsonl").exists()

def test_learning_expectation_cannot_come_from_candidate_run(tmp_path, projects, facts):
    from reproagent.core.models import SourceRef
    from reproagent.core.serialization import encode_record
    _, result, store, candidate, run = material_setup(tmp_path, projects, facts)
    # Rebuild in a new fixture because the first evidence capsule must not be overwritten.
    _, result, store, candidate, run, _, _ = export_setup(tmp_path / "new", projects, facts)
    contract = store.load_contract(candidate.contract_id, candidate.contract_version)
    path = store.root / "runs" / run.run_id / "code/tests/test_repro.py"
    ref = SourceRef(path.relative_to(store.root).as_posix(), bytes_hash(path.read_bytes()))
    altered = replace(contract, sources=(ref,))
    atomic_write(store.record_path("contracts", contract.contract_id), canonical_bytes(encode_record(altered)))
    assert exp().build_learning_input(store, replace(result, status=TaskState.DONE), facts.context()) is None


def test_quoted_known_credential_is_redacted_inside_json():
    import json
    secret = 'test-key-with-"quote'
    value = exp()._redact(json.dumps({"value": secret}), (secret,))
    assert json.loads(value)["value"] == "[REDACTED]"
