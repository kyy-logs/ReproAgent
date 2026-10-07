"""What one exploration phase may hand back, and the gate that keeps the first answer.

A phase is run by an SDK agent that can call tools in any order, so the end of a phase has
to be a fact rather than a reading of the agent's last message.  A domain tool publishes a
frozen :class:`PhaseResult` — one of four closed kinds with the fields that kind actually
means — and the :class:`PhaseGate` keeps the first one and sets an event the runtime waits
on.  A later tool cannot replace that answer with a different one, and nothing here is
SDK-specific: the controller decides what to do with the result.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass

from .models import EvidenceRef

#: The closed vocabulary of phase outcomes.  A candidate was published, a contract revision
#: is requested, information is missing, or the phase ended with nothing to act on.
PHASE_RESULT_KINDS = ("candidate", "revise_contract", "request_information", "no_candidate")

#: Per kind, the fields that must be present, and the fields that must stay empty so a
#: result never claims two things at once.
_BRANCH_FIELDS = {
    "candidate": (("candidate_id",), ("source_refs", "reason", "question")),
    "revise_contract": (("source_refs", "reason"), ("candidate_id", "question")),
    "request_information": (("question",), ("candidate_id", "source_refs", "reason")),
    "no_candidate": (("reason",), ("candidate_id", "source_refs", "question")),
}
_TEXT_FIELDS = ("candidate_id", "reason", "question")


@dataclass(frozen=True, slots=True)
class PhaseResult:
    """The controller-facing outcome of one exploration phase.

    ``kind`` is one of :data:`PHASE_RESULT_KINDS`; the fields a kind does not use must stay
    empty, so ``candidate`` always carries a real id and ``revise_contract`` always carries
    the references and the reason the controller needs to ask for a revision.
    """

    kind: str
    candidate_id: str = ""
    source_refs: tuple[EvidenceRef, ...] = ()
    reason: str = ""
    question: str = ""

    def __post_init__(self):
        if self.kind not in PHASE_RESULT_KINDS:
            raise ValueError(f"unknown phase result kind: {self.kind!r}; expected one of {PHASE_RESULT_KINDS}")
        for name in _TEXT_FIELDS:
            if type(getattr(self, name)) is not str:
                raise ValueError(f"phase result {name} must be a string")
        refs = self.source_refs
        if not isinstance(refs, (tuple, list)):
            raise ValueError("phase result source_refs must be a sequence of references")
        refs = tuple(refs)
        if any(not isinstance(ref, EvidenceRef) for ref in refs):
            raise ValueError("phase result source_refs must be evidence references, not paths or text")
        object.__setattr__(self, "source_refs", refs)
        required, forbidden = _BRANCH_FIELDS[self.kind]
        for name in required:
            if not getattr(self, name):
                raise ValueError(f"phase result of kind {self.kind!r} requires {name}")
        for name in forbidden:
            if getattr(self, name):
                raise ValueError(f"phase result of kind {self.kind!r} must leave {name} empty")

    def as_dict(self) -> dict:
        """The result as plain data, for events and reports."""
        return asdict(self)


class PhaseGate:
    """The one result of the current phase, and the event that says it arrived.

    ``begin`` opens a round, ``finish`` accepts the first result of that round and ignores
    later ones, and ``event`` lets the runtime stop waiting for the agent without polling.
    """

    def __init__(self) -> None:
        self._result: PhaseResult | None = None
        self.event = asyncio.Event()

    @property
    def result(self) -> PhaseResult | None:
        """The first result of the current round, or None while the phase is open."""
        return self._result

    @property
    def finished(self) -> bool:
        """Whether this round already has its result."""
        return self._result is not None

    def begin(self) -> None:
        """Open a new round: no result yet, and nothing to wake up for."""
        self._result = None
        self.event.clear()

    def finish(self, result: PhaseResult) -> None:
        """Accept *result* if this round has none yet; a later result is ignored.

        Raises:
            TypeError: *result* is not a :class:`PhaseResult`; only a validated domain
                result may end a phase.
        """
        if not isinstance(result, PhaseResult):
            raise TypeError(f"a phase ends on a PhaseResult, not {type(result).__name__}")
        if self._result is not None:
            return
        self._result = result
        self.event.set()


__all__ = ["PHASE_RESULT_KINDS", "PhaseGate", "PhaseResult"]
