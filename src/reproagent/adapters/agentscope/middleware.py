"""The budget, permission and context boundary of one exploration phase.

Everything the SDK would otherwise decide for itself is decided here, on the SDK's own
published hooks:

* a model call is a step.  ``before_model_call`` is the guard Task 1's factory runs once,
  before its bounded HTTP attempt loop, so a network retry never costs a second step and a
  phase that has already produced its result never spends one at all;
* a response is checked whole before the SDK acts on any of it, so a two-call answer runs
  neither call and an unknown name has no execution path to take;
* a tool outside the phase surface is refused, and no answer is ever a question: the phase
  has no user to ask;
* context compression is refused outright.  ``ContextConfig.compression_tool_enabled``
  only removes the model-invocable compression *tool*, and the threshold compression the
  agent runs before every reasoning step still fires on ``trigger_ratio * context_size``
  with ``trigger_ratio`` capped at 0.9, so ``on_compress_context`` is the only point where
  it can be stopped for good -- and, when the context really has reached the threshold,
  the phase stops with a diagnostic instead of dropping the references it was given.

The events this middleware records carry the phase, a controlled action and result code, a
canonical hash of the admitted arguments, the steps left and the versions actually running.
No provider response, reasoning text or tool argument ever enters them.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from agentscope.message import SystemMsg, ToolCallBlock
from agentscope.middleware import MiddlewareBase
from agentscope.model import ChatResponse
from agentscope.permission import PermissionBehavior, PermissionDecision

from .dependency import require_agentscope
from .tools import TOOL_NAMES
from ...core.budget import BudgetStopped
from ...core.protocol import ModelProtocolError
from ...core.serialization import canonical_hash, parse_json

#: The phase these events belong to; one runtime runs the exploration phases of one task.
PHASE = "EXPLORATION"

#: The controlled codes a rejected response can carry.  A code names the shape of the
#: refusal, never the text that caused it.
PHASE_PROTOCOL_CODES = ("MULTIPLE_TOOL_CALLS", "UNKNOWN_TOOL", "INVALID_TOOL_INPUT")

#: How many responses one phase may spend on protocol correction, budget permitting.
PROTOCOL_ATTEMPTS = 3

#: The tools that only read.  They are the ones a phase can spend its whole budget on: the
#: SDK's ReAct loop has no reason to stop reading a large repository, and the fixed-point
#: run spent 20 of 20 decisions on Read/Grep/Glob without ever publishing a candidate.
READER_TOOLS = ("Read", "Grep", "Glob", "read_experience")

#: Steps kept for publishing and its bounded protocol corrections.  The controller runs,
#: repeats and submits the candidate itself, so those steps cost no budget of their own.
#: A contract that still misses facts keeps its readers: reading is how facts are found.
RESERVED_FOR_PUBLISHING = 3


class PhaseEnded(Exception):
    """The phase already has its result, so no further model call may be made.

    Raised by :meth:`ExplorationMiddleware.before_model_call` before the step is counted
    and before any request leaves, and caught by the runtime: a finished phase is a normal
    end, not a cancellation.
    """


class PhaseProtocolError(ModelProtocolError):
    """No usable exploration response within the bounded protocol attempts.

    Args:
        code: one of :data:`PHASE_PROTOCOL_CODES`.
    """

    def __init__(self, code: str) -> None:
        if code not in PHASE_PROTOCOL_CODES:
            raise ValueError(f"unknown phase protocol code: {code}")
        super().__init__(f"{code}: the exploration response was rejected in all {PROTOCOL_ATTEMPTS} attempts")
        self.code = code


def component_versions() -> dict:
    """The components actually running, for the audit trail."""
    def installed(name: str) -> str:
        try:
            return version(name)
        except PackageNotFoundError:      # pragma: no cover - installed in every supported run
            return ""

    from reproagent import __version__ as product_version

    return {"reproagent": product_version, "agentscope": installed("agentscope"), "runtime": "agentscope-react"}


class ExplorationMiddleware(MiddlewareBase):
    """The SDK middleware that makes one exploration phase bounded and terminable.

    Args:
        context: budget and cancellation for every call the phase makes.
        gate: the phase's result gate; a finished gate stops the next model call.
        store: the task store events are appended to, or None to record nothing.
    """

    def __init__(self, context, gate, store=None, *, allowed_tools=TOOL_NAMES) -> None:
        require_agentscope()
        self.context, self.gate, self.store = context, gate, store
        self.allowed_tools = tuple(allowed_tools)
        self.components = component_versions()
        #: The phase's contract, handed over by the runtime when the phase starts.  Until
        #: then there is nothing to reserve the last steps for.
        self.contract = None

    @property
    def steps_remaining(self) -> int:
        """The steps this task may still spend, including one in flight."""
        return self.context.budget.limits.agent_steps - self.context.budget.steps_used

    # =======================================================================
    # The product-side guard, run by the model factory before its HTTP loop
    # =======================================================================

    def before_model_call(self) -> None:
        """One exploration step, or the reason there may not be one.

        This is the function handed to ``AgentScopeModelFactory.create`` as the guard.  It
        runs once per logical model call, before the bounded attempt loop, which is what
        makes a step a logical request rather than an HTTP attempt, and it runs *inside*
        the model call rather than in a hook, so no path to the provider can skip it.

        Raises:
            PhaseEnded: this phase already produced its result.
            BudgetStopped: the task is cancelled (``CANCELLED``) or over its deadline, its
                cost limit or its step budget (``EXHAUSTED``).
        """
        if self.gate.finished:
            raise PhaseEnded("the phase already produced its result")
        if self.context.cancel_event.is_set():
            raise BudgetStopped("CANCELLED")
        self.context.budget.check()
        self.context.budget.take_step()
        self.record("exploration.step", action="", result_code="STEP")

    # =======================================================================
    # SDK hooks
    # =======================================================================

    async def on_model_call(self, agent, input_kwargs, next_handler):
        """Call the model with the configured output ceiling, then check the whole reply.

        The SDK never puts the output limit on the wire itself -- only the product gateway
        does -- so the ceiling is resolved from the model's own configuration here; a
        request without it would carry no limit while its attempt record claimed one.  The
        model is called directly because the SDK's model-call chain forwards no extra
        keyword arguments to it, and this phase registers exactly this one middleware.

        Raises:
            PhaseProtocolError: the reply was refused whole.  Nothing of it has run, and
                nothing of it enters the conversation.
        """
        model = input_kwargs["current_model"]
        response = await model(
            messages=input_kwargs["messages"], tools=input_kwargs["tools"],
            tool_choice=input_kwargs["tool_choice"], **self.output_limit(model))
        self.check_response(response, allowed_tools=self.allowed_tools)
        return response

    async def on_check_permission(self, agent, input_kwargs, next_handler):
        """Refuse every tool outside the phase surface, and never ask a user who is absent.

        The six phase tools are still decided by the SDK's own engine, so a configured
        deny rule keeps winning over them; what cannot happen is an answer that parks the
        phase on a confirmation nobody can give, or a tool the phase never registered.
        Once a grounded contract has only the reserved steps left, the readers are refused
        too, so a large repository cannot consume the budget the publish needs.
        """
        tool = input_kwargs.get("tool")
        name = getattr(tool, "name", "")
        code = "DENIED"
        if name in self.allowed_tools:
            decision = await next_handler(**input_kwargs)
            if decision.behavior is not PermissionBehavior.ALLOW:
                decision = PermissionDecision(
                    behavior=PermissionBehavior.DENY, decision_reason="not allowed in this phase",
                    message=f"{name} was not allowed in this phase; the phase's own checks were not reached")
            elif self._reserved_for_publishing(name):
                code = "RESERVED_FOR_PUBLISHING"
                decision = PermissionDecision(
                    behavior=PermissionBehavior.DENY, decision_reason="the last steps are reserved for publishing",
                    message=(f"{name} is refused: {self.steps_remaining} step(s) remain and publishing needs them."
                             " Write the candidate from what you already read, or use revise_contract or"
                             " request_information."))
            else:
                code = "ALLOWED"
        else:
            decision = PermissionDecision(
                behavior=PermissionBehavior.DENY, decision_reason="outside the phase tool set",
                message=f"{name or 'this tool'} is not part of the exploration phase")
        self.record("exploration.action", action=name if name in self.allowed_tools else "", result_code=code,
                    arguments_hash=self._arguments_hash(input_kwargs.get("tool_input")))
        return decision

    def _reserved_for_publishing(self, name) -> bool:
        """Whether this reader must wait: a grounded contract and only the reserved steps."""
        contract = self.contract
        return (name in READER_TOOLS and self.steps_remaining <= RESERVED_FOR_PUBLISHING
                and contract is not None and bool(contract.expected) and not contract.missing_information)

    async def on_compress_context(self, agent, input_kwargs, next_handler):
        """Stop compression at the only point that can: the compression call itself.

        The SDK runs this hook before every reasoning step, whether or not the context is
        near the threshold, and its own implementation returns early only when the input is
        below ``trigger_ratio * context_size``.  This one never compresses -- a summary
        replaces the messages that carry the source references -- so it answers the same
        question instead: below the threshold there is nothing to do, and at it the phase
        stops with a diagnostic rather than continuing on a rewritten history.

        Raises:
            BudgetStopped: the task was cancelled, or the context reached the model's
                compression threshold (``NEEDS_INFORMATION``).
        """
        if self.gate.finished:
            # The phase already has its result, and the next model call ends it, so nothing
            # here needs a shorter context.  A threshold stop must never overtake a result
            # that exists: this hook runs before every reasoning step, and the step right
            # after a domain tool is exactly the one whose arguments pushed the context up.
            return
        if self.context.cancel_event.is_set():
            raise BudgetStopped("CANCELLED")
        config = input_kwargs.get("context_config") or agent.context_config
        threshold = config.trigger_ratio * agent.model.context_size
        if await self._estimated_tokens(agent) < threshold:
            return
        raise BudgetStopped(
            "NEEDS_INFORMATION",
            "the exploration context reached the model's compression threshold and this phase does not "
            "compress: a summary would not keep the source references. Report the missing information "
            "instead of continuing on a rewritten history.")

    # =======================================================================
    # Internals
    # =======================================================================

    def record(self, kind: str, **fields) -> None:
        """Append one exploration event, with the phase's own vocabulary.

        Every event carries ``phase``, a controlled ``action``/``result_code`` pair, the
        canonical ``arguments_hash`` of the admitted arguments, ``steps_remaining`` and the
        components actually running.  A refused response admits no arguments, so its hash
        is empty; no event carries provider text.
        """
        if self.store is None:
            return
        self.store.append_event(kind, (), {
            "phase": PHASE, "action": "", "result_code": "", "arguments_hash": "",
            "steps_remaining": self.steps_remaining, "components": self.components, **fields})

    @staticmethod
    def output_limit(model) -> dict:
        """The configured output ceiling, as the provider request must carry it.

        Raises:
            ValueError: the model does not carry a resolved limit, so the request would go
                out without a ceiling while its accounting claimed one.
        """
        config = getattr(model, "config", None)
        field, limit = getattr(config, "output_limit_field", None), getattr(config, "max_output_tokens", None)
        if not isinstance(field, str) or isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("the exploration model must carry a configured output limit")
        return {field: limit}

    @staticmethod
    def check_response(response, *, allowed_tools=TOOL_NAMES) -> None:
        """Refuse a response whole, before the SDK acts on any part of it.

        Raises:
            PhaseProtocolError: more than one tool call (so no call may run before the
                others are judged), a name outside :data:`TOOL_NAMES`, or arguments that
                are not one complete JSON object.
        """
        if not isinstance(response, ChatResponse):   # pragma: no cover - the phase model never streams
            return
        calls = [block for block in response.content if isinstance(block, ToolCallBlock)]
        if len(calls) > 1:
            raise PhaseProtocolError("MULTIPLE_TOOL_CALLS")
        if not calls:
            return
        if calls[0].name not in allowed_tools:
            raise PhaseProtocolError("UNKNOWN_TOOL")
        try:
            parse_json(calls[0].input)
        except (TypeError, ValueError):
            raise PhaseProtocolError("INVALID_TOOL_INPUT") from None

    async def _estimated_tokens(self, agent) -> int:
        """What the SDK is about to send, counted the way its own compression gate counts it."""
        prepare = getattr(agent, "_prepare_model_input", None)
        if prepare is not None:
            kwargs = await prepare()
        else:                                        # pragma: no cover - 2.0.9 always provides it
            kwargs = {"messages": [SystemMsg(name="system", content=getattr(agent, "_system_prompt", ""))]
                                  + list(agent.state.context),
                      "tools": await agent.toolkit.get_tool_schemas(agent.state.tool_context.activated_groups)}
        return await agent.model.count_tokens(**kwargs)

    @staticmethod
    def _arguments_hash(tool_input) -> str:
        """The canonical hash of one tool call's admitted arguments, or empty."""
        if not isinstance(tool_input, dict):
            return ""
        try:
            return canonical_hash(tool_input)
        except (TypeError, ValueError):              # pragma: no cover - the SDK parses JSON before this
            return ""


__all__ = ["PHASE", "PHASE_PROTOCOL_CODES", "PROTOCOL_ATTEMPTS", "ExplorationMiddleware",
           "PhaseEnded", "PhaseProtocolError", "component_versions"]
