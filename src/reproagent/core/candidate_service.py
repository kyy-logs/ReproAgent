"""The only way a phase publishes a candidate, against the contract and snapshot it was given.

The controller freezes a snapshot, decides which contract version the phase works from, and
hands both to this service.  A phase may then publish a draft, and every product rule that
already guards publication stays where it was: containment, roles, naming, collisions and
hashes are all enforced by :meth:`Workspace.publish`, and the stored record is re-validated
afterwards, so the id a phase reports is the id the task store actually holds.

Neither the snapshot nor the contract is chosen here.  The bound snapshot is the frozen one
the project view carries — this service never freezes, guesses or re-freezes — and every
draft is checked against the bound contract version, so a phase that has moved on to another
contract version cannot publish the older draft.
"""
from __future__ import annotations

from dataclasses import replace

from reproagent.workspace import Workspace

from .budget import BudgetStopped
from .models import CallContext, Candidate, CandidateDraft, CodeSnapshot, IssueContract, ProjectView


class CandidateService:
    """Publishes candidates for one phase, under the phase's contract and snapshot."""

    def __init__(self, project: ProjectView, workspace: Workspace, context: CallContext) -> None:
        """Bind the service to *project*'s frozen snapshot and to *workspace*.

        Args:
            project: the phase's view of the frozen snapshot.  ``project.snapshot`` is the
                snapshot every candidate is published against; taking it from the view is
                the binding, so nothing here has to freeze or look one up later.
            workspace: the product workspace that owns the publication rules.
            context: budget and cancellation for every publication.
        """
        self.project, self.workspace, self.context = project, workspace, context
        self.snapshot: CodeSnapshot = project.snapshot
        self._contract: IssueContract | None = None

    @property
    def contract(self) -> IssueContract | None:
        """The contract the current phase works from, or None before it is bound."""
        return self._contract

    def bind_contract(self, contract: IssueContract) -> None:
        """Bind the contract version the phase builds drafts against.

        Raises:
            ValueError: *contract* is not a contract, or has no identity to bind.
        """
        if not isinstance(contract, IssueContract):
            raise ValueError(f"a phase binds an IssueContract, not {type(contract).__name__}")
        if not contract.contract_id or contract.version < 1:
            raise ValueError("a bound contract needs an id and a version")
        self._contract = contract

    def publish(self, draft: CandidateDraft) -> Candidate:
        """Publish *draft* as an immutable candidate and return the stored record.

        Raises:
            BudgetStopped: the task is cancelled, over its deadline or over budget.
            ValueError: no grounded contract is bound, the draft was built against another
                contract version or snapshot, or the workspace refused the publication.
        """
        self.context.budget.check()
        if self.context.cancel_event.is_set():
            raise BudgetStopped("CANCELLED")
        contract = self._contract
        if contract is None:
            raise ValueError("no contract is bound to this phase, so no candidate can be published")
        if (draft.contract_id, draft.contract_version) != (contract.contract_id, contract.version):
            raise ValueError(f"candidate was built against contract {draft.contract_id} v{draft.contract_version}, "
                             f"not the bound {contract.contract_id} v{contract.version}")
        if draft.snapshot_id != self.snapshot.snapshot_id:
            raise ValueError(f"candidate was built against snapshot {draft.snapshot_id!r}, not the bound {self.snapshot.snapshot_id!r}")
        if not contract.expected or not contract.sources or contract.missing_information:
            raise ValueError("candidate requires a grounded contract: expected behaviour, at least one source, and no missing information")
        candidate = self.workspace.publish(draft, self.snapshot)
        self.workspace.validate_candidate(candidate)
        stored = self.workspace.store.load_record("candidates", candidate.candidate_id)
        if stored != candidate:
            raise ValueError(f"stored candidate {candidate.candidate_id} does not match the published record")
        self.project = replace(self.project, candidates=(*self.project.candidates, candidate))
        return candidate


__all__ = ["CandidateService"]
