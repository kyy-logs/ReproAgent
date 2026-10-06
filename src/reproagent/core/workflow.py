"""The deterministic half of the candidate lifecycle.

The Explorer owns test content and the source evidence it needs. Whether a
published candidate gets executed, and whether a reproduced one gets submitted,
are facts about the task state rather than choices, so they are decided here and
the Controller acts on them without spending a model decision. Everything that
remains a choice stays inside the same action budget.
"""

from dataclasses import dataclass

from .models import AgentAction
from .tools import SCHEMAS

# Fixed vocabulary: a code says which rule fired, never anything a model wrote.
FORCE_RUN = 'FORCE_RUN_CANDIDATE'
FORCE_SUBMIT = 'FORCE_SUBMIT_CANDIDATE'
REPEATED = 'REPEATED_ACTION'
UNGROUNDED = 'CONTRACT_MISSING_INFORMATION'
RESERVE = 'RESERVE_WRITE_RUN_SUBMIT'
SELECT = 'MODEL_SELECTS'

# Reading, revising and asking stay possible when the contract is not grounded;
# none of them turns into a candidate on its own.
INFORMATION_ACTIONS = ('search_code', 'read_file', 'revise_contract', 'request_information')
# write, run, submit and one correction attempt.
RESERVE_STEPS = 4


@dataclass(frozen=True, slots=True)
class WorkflowState:
    steps_remaining: int
    grounded: bool
    candidate_id: str = ''
    candidate_contract_version: int = 0
    current_contract_version: int = 0
    has_execution: bool = False
    reproduced: bool = False
    repeated_action: AgentAction | None = None


@dataclass(frozen=True, slots=True)
class WorkflowDecision:
    forced_action: AgentAction | None = None
    allowed_actions: tuple[str, ...] = ()
    blocked_actions: tuple[AgentAction, ...] = ()
    reason_code: str = ''


def choose_workflow(state):
    """Decide the next lifecycle step from the state alone, without a model."""
    blocked = (state.repeated_action,) if state.repeated_action is not None else ()
    if not state.grounded:
        # An unsourced contract cannot be forced into a candidate: read, revise or ask.
        return WorkflowDecision(None, INFORMATION_ACTIONS, blocked, UNGROUNDED)
    usable = bool(state.candidate_id) and state.candidate_contract_version == state.current_contract_version
    forced = None
    if usable and state.reproduced:
        forced = AgentAction('submit_candidate', {'candidate_id':state.candidate_id})
    elif usable and not state.has_execution:
        forced = AgentAction('run_candidate', {'candidate_id':state.candidate_id})
    if forced is not None and forced not in blocked:
        return WorkflowDecision(forced, (forced.name,), blocked, FORCE_SUBMIT if forced.name == 'submit_candidate' else FORCE_RUN)
    if forced is not None:
        # The same action already returned the same result twice in this state: hand
        # the choice back instead of spending the budget on a third identical attempt.
        return WorkflowDecision(None, _allowed(state), blocked, REPEATED)
    return WorkflowDecision(None, _allowed(state), blocked, RESERVE if state.steps_remaining <= RESERVE_STEPS else SELECT)


def _allowed(state):
    """Every action an Explorer may still choose in this state."""
    allowed = list(SCHEMAS)
    if not state.candidate_id:
        allowed = [name for name in allowed if name not in ('run_candidate', 'submit_candidate')]
    if state.steps_remaining <= RESERVE_STEPS:
        # Keep write/run/submit and one correction attempt; a read no longer fits.
        allowed = [name for name in allowed if name not in ('read_file', 'search_code')]
    return tuple(allowed)
