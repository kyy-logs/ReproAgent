"""The phase-result contract, the gate that keeps one result, and the candidate service."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from reproagent.core.budget import BudgetStopped
from reproagent.core.candidate_service import CandidateService
from reproagent.core.models import (
    CandidateDraft,
    DraftFile,
    EvidenceRef,
    IssueContract,
    ProjectView,
    PythonPytestConfig,
    SourceRef,
    TaskRequest,
)
from reproagent.core.phase import PHASE_RESULT_KINDS, PhaseGate, PhaseResult
from reproagent.store import TaskStore
from reproagent.workspace import Workspace

REF = EvidenceRef("snapshots/snapshot-1/code/example/parser.py", "a" * 64, 1, 1)


def contract(**overrides):
    values = dict(contract_id="contract-1", version=1, description_hash="description-hash",
                  trigger="parse([]) raises IndexError", expected="parse([]) returns []", reported_actual="IndexError",
                  observable_checks=("parse([]) == []",), sources=(SourceRef("input/issue.md", "0" * 64, 1, 1, "issue"),),
                  assumptions=(), missing_information=())
    return IssueContract(**{**values, **overrides})


def draft(snapshot, bound, path="tests/test_repro.py", content=b"def test_repro():\n    assert False\n", candidate_id=""):
    return CandidateDraft((DraftFile(path, content),), snapshot.snapshot_id, bound.contract_id, bound.version,
                          "empty input", candidate_id=candidate_id, expectation_sources=bound.sources)


def test_phase_result_kinds_and_branch_fields_are_closed():
    assert PHASE_RESULT_KINDS == ("candidate", "revise_contract", "request_information", "no_candidate")
    assert PhaseResult("candidate", candidate_id="candidate-1").candidate_id == "candidate-1"
    assert PhaseResult("revise_contract", source_refs=(REF,), reason="narrow").source_refs == (REF,)
    assert PhaseResult("revise_contract", source_refs=[REF], reason="narrow").source_refs == (REF,)
    assert PhaseResult("request_information", question="which version?").question == "which version?"
    assert PhaseResult("no_candidate", reason="nothing cited yet").reason == "nothing cited yet"
    # One closed vocabulary and one field set per branch: a result that says two things at
    # once is not a result the controller could act on.
    for fields in ({"kind": "reproduced"},                                   # unknown kind
                   {"kind": "candidate"},                                    # no candidate id
                   {"kind": "candidate", "candidate_id": ""},
                   {"kind": "candidate", "candidate_id": "c", "source_refs": (REF,)},
                   {"kind": "candidate", "candidate_id": "c", "reason": "done"},
                   {"kind": "candidate", "candidate_id": "c", "question": "?"},
                   {"kind": "revise_contract", "source_refs": (REF,)},       # no reason
                   {"kind": "revise_contract", "reason": "x"},               # no references
                   {"kind": "revise_contract", "source_refs": (), "reason": "x"},
                   {"kind": "revise_contract", "source_refs": (REF,), "reason": "x", "candidate_id": "c"},
                   {"kind": "revise_contract", "source_refs": (REF,), "reason": "x", "question": "?"},
                   {"kind": "request_information"},                          # no question
                   {"kind": "request_information", "question": "q", "reason": "x"},
                   {"kind": "request_information", "question": "q", "source_refs": (REF,)},
                   {"kind": "no_candidate"},                                 # no reason
                   {"kind": "no_candidate", "reason": "x", "question": "q"},
                   {"kind": "no_candidate", "reason": "x", "candidate_id": "c"}):
        with pytest.raises(ValueError):
            PhaseResult(**fields)
    # A reference is a real reference, not whatever the caller passed through.
    with pytest.raises(ValueError):
        PhaseResult("revise_contract", source_refs=("example/parser.py:1",), reason="x")
    # A result is plain data for events and reports, without the caller reaching for the
    # dataclass module.
    assert PhaseResult("no_candidate", reason="nothing cited yet").as_dict() == {
        "kind": "no_candidate", "candidate_id": "", "source_refs": (), "reason": "nothing cited yet", "question": ""}


def test_gate_keeps_only_the_first_result_and_reopens_on_begin():
    gate = PhaseGate()
    assert gate.result is None and not gate.event.is_set()
    first = PhaseResult("no_candidate", reason="nothing cited yet")
    gate.begin()
    gate.finish(first)
    assert gate.result is first and gate.event.is_set()
    # The first result of a round is the round's result: a later tool cannot replace it.
    gate.finish(PhaseResult("request_information", question="later"))
    assert gate.result is first
    with pytest.raises(TypeError):
        gate.finish("not a result")
    assert asyncio.run(_wait(gate)) is first                # the event is what the runtime awaits
    gate.begin()
    assert gate.result is None and not gate.event.is_set()
    gate.finish(PhaseResult("request_information", question="which version?"))
    assert gate.result.question == "which version?"


async def _wait(gate):
    await asyncio.wait_for(gate.event.wait(), timeout=1)
    return gate.result


@pytest.fixture
def frozen(tmp_path, projects, facts):
    repo = projects.plain(tmp_path / "中文 repo")
    task = tmp_path / "task"
    store = TaskStore(task)
    workspace = Workspace(task, store)
    snapshot = workspace.freeze(TaskRequest(repo, task, repo / "issue.md", language=PythonPytestConfig()), facts.context())
    project = ProjectView(snapshot)
    return SimpleNamespace(repo=repo, task=task, store=store, workspace=workspace, snapshot=snapshot,
                           project=project, context=facts.context())


def test_publish_requires_a_bound_grounded_contract(frozen):
    bound = contract()
    service = CandidateService(frozen.project, frozen.workspace, frozen.context)
    # Nothing is published before a contract is bound to the phase.
    with pytest.raises(ValueError, match="contract"):
        service.publish(draft(frozen.snapshot, bound))
    assert not (frozen.task / "candidates").exists()
    service.bind_contract(bound)
    assert service.contract is bound
    # A contract the controller cannot act on is not a contract a candidate may cite.
    for weak in (contract(expected=""), contract(sources=()), contract(missing_information=("which parser?",))):
        service.bind_contract(weak)
        with pytest.raises(ValueError, match="contract"):
            service.publish(draft(frozen.snapshot, bound))
    assert not (frozen.task / "candidates").exists()


def test_publish_refuses_a_draft_built_against_another_contract_or_snapshot(frozen):
    bound = contract()
    service = CandidateService(frozen.project, frozen.workspace, frozen.context)
    service.bind_contract(bound)
    # The draft carries the contract identity it was built against; a phase that has moved
    # on to another contract version cannot publish the older draft.
    service.bind_contract(contract(version=2))
    with pytest.raises(ValueError, match="contract"):
        service.publish(draft(frozen.snapshot, bound))
    service.bind_contract(contract(contract_id="contract-2", version=1))
    with pytest.raises(ValueError, match="contract"):
        service.publish(draft(frozen.snapshot, bound))
    # The bound snapshot is the phase's frozen one, never a guess made at publish time.
    service.bind_contract(bound)
    assert service.snapshot is frozen.snapshot
    with pytest.raises(ValueError, match="snapshot"):
        service.publish(replace(draft(frozen.snapshot, bound), snapshot_id="snapshot-other"))
    assert not (frozen.task / "candidates").exists()


def test_publish_stores_the_immutable_candidate_and_refreshes_the_view(frozen):
    bound = contract()
    service = CandidateService(frozen.project, frozen.workspace, frozen.context)
    service.bind_contract(bound)
    candidate = service.publish(draft(frozen.snapshot, bound))
    assert candidate.candidate_id.startswith("candidate-")
    assert (candidate.contract_id, candidate.contract_version) == (bound.contract_id, bound.version)
    assert candidate.snapshot_id == frozen.snapshot.snapshot_id
    assert candidate.expectation_sources == bound.sources
    # The id the phase will report is the stored record, re-validated against its own hashes.
    stored = frozen.store.load_record("candidates", candidate.candidate_id)
    assert stored == candidate
    frozen.workspace.validate_candidate(stored)
    assert (stored.storage_root / "tests" / "test_repro.py").read_bytes() == b"def test_repro():\n    assert False\n"
    assert service.project.candidates == (candidate,)
    # The identity is generated here, never supplied by the caller of the service.
    with pytest.raises(ValueError, match="immutable candidate ID"):
        service.publish(draft(frozen.snapshot, bound, candidate_id=candidate.candidate_id))
    # A cancelled or expired phase publishes nothing.
    service.context.cancel_event.set()
    with pytest.raises(BudgetStopped):
        service.publish(draft(frozen.snapshot, bound, path="tests/test_second.py"))
    assert service.project.candidates == (candidate,)
