"""One exploration phase, run by one SDK agent under the task's step budget.

A task gets one runtime, and a runtime keeps one SDK ``Agent`` with its own ``AgentState``,
so every phase of that task reasons over the tool calls and results the phases before it
produced -- that history is the memory the migration is meant to keep, and a fresh context
per phase would silently throw it away.

The run ends when the phase's gate has a result, and it ends *there*: the finished tool
result has already been written into the conversation, the guard refuses the next model
call before it reaches the wire, and the SDK reply task is always awaited or cancelled and
awaited, so nothing this runtime started survives the run.  A phase that has no domain
result at all is ``no_candidate`` -- the controller decides what that means -- and a task
that ran out of budget keeps raising ``EXHAUSTED`` rather than being reported as finished.
"""
from __future__ import annotations

import asyncio

from agentscope.agent import Agent, ContextConfig, InjectionConfig, ReActConfig
from agentscope.agent import ModelConfig as SDKModelConfig
from agentscope.message import Msg, UserMsg
from agentscope.permission import PermissionContext, PermissionMode
from agentscope.state import AgentState

from .dependency import require_agentscope
from .middleware import PROTOCOL_ATTEMPTS, ExplorationMiddleware, PhaseEnded, PhaseProtocolError
from .tools import TOOL_NAMES
from ...core.budget import BudgetStopped
from ...core.models import AgentContext
from ...core.phase import PhaseGate, PhaseResult
from ...core.serialization import canonical_bytes

#: The SDK agent's name; it is the name of every message the SDK writes for this task.
AGENT_NAME = "reproagent-exploration"

#: How the phase reports a run that produced no domain result.
NO_CANDIDATE_REASON = ("the exploration phase ended without publishing a candidate, requesting a contract "
                       "revision or reporting missing information")

#: Recorded as the SDK end reason when the reply produced no final message of its own,
#: because the phase's result already existed and the next model call was refused.
REPLY_STOPPED_BY_PHASE = "phase_ended"
#: Recorded as the SDK end reason when this runtime had to stop the reply itself.
REPLY_STOPPED = "stopped"


def sdk_end_reason(reply) -> str:
    """How one SDK reply ended, in the SDK's own vocabulary, next to the business kind.

    The SDK's own reasons (``completed``, ``interrupted``, ``exceed_max_iters``, ``error``)
    are recorded as they are; a reply that never produced a final message -- because this
    runtime stopped it -- is reported with the closed token :data:`REPLY_STOPPED`.  The
    reason is an SDK enum value, never provider text, so it is safe to record.
    """
    if reply is None:
        return REPLY_STOPPED
    return str(getattr(reply, "finished_reason", "") or "")


class AgentScopeRuntime:
    """Runs the exploration phases of one task as bounded SDK replies.

    Args:
        model: the exploration model of this task, created by
            :class:`~reproagent.adapters.agentscope.model_factory.AgentScopeModelFactory`.  It
            must be guardable (``bind``), non-streaming and carry the configured output
            limit, because the phase's budget and its request ceiling are installed here.
        toolkit: the phase toolkit, built by :func:`...tools.build_toolkit`.
        gate: the phase's result gate; one domain tool ends a phase through it.
        context: budget and cancellation for every call the phase makes.
        system_prompt: the product's exploration instructions.
        store: the task store events are appended to, or None to record nothing.
    """

    def __init__(self, model, toolkit, gate: PhaseGate, context, *, system_prompt: str, store=None) -> None:
        require_agentscope()
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError("an exploration phase needs the product's system prompt")
        self._require_guardable(model)
        self.model, self.toolkit, self.gate, self.context = model, toolkit, gate, context
        self.middleware = ExplorationMiddleware(context, gate, store)
        self._task: asyncio.Task | None = None
        self._agent = Agent(
            name=AGENT_NAME, system_prompt=system_prompt, model=model, toolkit=toolkit,
            middlewares=[self.middleware],
            # One agent, one state, one task: the history of this task lives here.
            state=AgentState(permission_context=PermissionContext(mode=PermissionMode.DONT_ASK)),
            # A phase retries nothing on its own; the factory's bounded HTTP loop and the
            # step budget are the only retry and the only ceiling.
            model_config=SDKModelConfig(max_retries=0),
            # The phase never asks for structured output, and its history is never rewritten:
            # see ExplorationMiddleware.on_compress_context for why the threshold itself is
            # refused, and remember that compression_tool_enabled only removes the tool.
            context_config=ContextConfig(compression_tool_enabled=False, compression_fallback_to_truncation=False),
            react_config=ReActConfig(max_iters=max(1, self._steps_remaining()), structured_output_grace_iters=1),
            # The runtime state injection would rewrite the prompt with the wall clock on
            # every step; the phase's input is the contract, the history and the feedback.
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        self._bind_guard()

    # =======================================================================
    # The exploration port
    # =======================================================================

    async def explore(self, context: AgentContext) -> PhaseResult:
        """Run one phase of this task to its result.

        The round is opened here: the gate holds this phase's result and nothing of the
        phase before it.

        A phase that produced its result answers with it, whatever stopped the agent
        afterwards -- that is the one precedence in this runtime: the guard, the compression
        hook and :meth:`_result` all ask the gate before they ask anything else, and a task
        stop that arrives once the result exists is a stop of the *agent*, not of the phase.
        The cancel event is left set either way, so the controller still sees the task was
        cancelled and runs its own cancellation path.

        Args:
            context: the contract, project view, history and feedback this phase starts
                from.  The history is the SDK's own, so only what is new is delivered.

        Returns:
            `PhaseResult`: what the phase's gate holds.

        Raises:
            BudgetStopped: the task is cancelled (``CANCELLED``), or out of time, cost or
                steps while this phase has no result (``EXHAUSTED``, and
                ``NEEDS_INFORMATION`` when the contract cannot fit or the context is at the
                compression threshold).
            PhaseProtocolError: no usable response within the bounded correction attempts.
        """
        if self._task is not None:
            raise RuntimeError("this runtime runs one phase at a time")
        self.gate.begin()
        self._bind_guard()
        self._agent.react_config.max_iters = max(1, self._steps_remaining())
        self.middleware.record("exploration.phase", action="", result_code="BEGIN",
                               contract_id=context.contract.contract_id, contract_version=context.contract.version)
        message = self._message(context)
        end_reason = ""
        for attempt in range(PROTOCOL_ATTEMPTS):
            try:
                reply = await self._reply(message)
            except PhaseEnded:
                end_reason = REPLY_STOPPED_BY_PHASE     # the phase's result ended the reply
                break
            except BudgetStopped:
                if not self.gate.finished:
                    raise
                end_reason = REPLY_STOPPED_BY_PHASE     # ... and so does a stop that follows it
                break
            except PhaseProtocolError as failure:
                self.middleware.record("exploration.protocol_error", action="", result_code=failure.code)
                if attempt + 1 == PROTOCOL_ATTEMPTS:
                    raise
                message = self._correction(failure)
                continue
            end_reason = sdk_end_reason(reply)          # the SDK's own reason, when it has one
            break
        result = self._result()
        self.middleware.record("exploration.finished", action=result.kind, result_code=result.kind,
                               sdk_end_reason=end_reason)
        return result

    async def aclose(self) -> None:
        """Retire this task's SDK session; nothing this runtime started outlives it."""
        task, self._task = self._task, None
        if task is None:
            return
        await self._retire(task)

    @property
    def messages(self) -> tuple:
        """The task's SDK conversation, oldest first, as the model saw it."""
        return tuple(self._agent.state.context)

    # =======================================================================
    # Internals
    # =======================================================================

    def _result(self) -> PhaseResult:
        """The phase's outcome once the agent has come to rest.

        Raises:
            BudgetStopped: the user cancelled the task while no domain result existed.  A
                phase that did produce one answers with that result, whatever stopped the
                agent afterwards -- the cancel event stays set for the controller either way.
        """
        if self.gate.finished:
            return self.gate.result
        if self.context.cancel_event.is_set():
            raise BudgetStopped("CANCELLED")
        return PhaseResult("no_candidate", reason=NO_CANDIDATE_REASON)

    async def _reply(self, message: Msg) -> Msg | None:
        """One SDK reply, raced against the phase's own end and awaited either way.

        Returns:
            `Msg | None`: the SDK's own final message, or None when this runtime stopped the
                reply.  Whatever ended the reply is re-raised.
        """
        task = asyncio.ensure_future(self._agent.reply(message))
        stopper = asyncio.ensure_future(self._stop_signal())
        self._task = task
        try:
            await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if not task.done():
                # The phase already has its result (or the task was cancelled): the finished
                # tool result still gets its bounded chance to be written into the history
                # before the reply is stopped.
                try:
                    await asyncio.wait_for(asyncio.shield(task), self.context.budget.limits.finalize_timeout_seconds)
                except asyncio.TimeoutError:
                    task.cancel()
        finally:
            await self._retire(stopper, task)
            if self._task is task:
                self._task = None
        if task.cancelled():
            return None                                 # stopped by this runtime, not by the model
        return task.result()                            # the final message, or what ended the reply

    async def _stop_signal(self) -> None:
        """Return once this phase must stop waiting: its result exists, or it was cancelled."""
        gate, cancelled = (asyncio.ensure_future(self.gate.event.wait()),
                           asyncio.ensure_future(self.context.cancel_event.wait()))
        try:
            await asyncio.wait({gate, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            await self._retire(gate, cancelled)

    @staticmethod
    async def _retire(*tasks) -> None:
        """Cancel whatever is unfinished and await it, so no task outlives its run."""
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _message(self, context: AgentContext) -> Msg:
        """The round's own facts as one message; the rest of the history is the SDK's.

        Raises:
            BudgetStopped: the contract, its sources and the feedback cannot fit the
                configured context budget.  History entries are dropped oldest first, but
                the contract is never trimmed: a phase that cannot see what it must
                reproduce reports the missing information instead.
        """
        cap = self.context.budget.limits.tool_response_bytes
        data = {"contract": context.contract, "feedback": context.feedback, "history": list(context.history),
                "budget": {"steps_remaining_including_this_attempt": self._steps_remaining(),
                           "seconds_remaining": max(0, self.context.budget.deadline - self.context.budget.clock())}}
        text = canonical_bytes(data).decode("utf-8")
        while len(text.encode("utf-8")) > cap and data["history"]:
            data["history"].pop(0)
            text = canonical_bytes(data).decode("utf-8")
        if len(text.encode("utf-8")) > cap:
            raise BudgetStopped("NEEDS_INFORMATION",
                                "the phase contract does not fit the configured context budget; "
                                "no source reference is dropped to make it fit")
        return UserMsg(name="user", content=text)

    @staticmethod
    def _correction(failure: PhaseProtocolError) -> Msg:
        """The one hint a rejected response is answered with; the response itself is gone."""
        return UserMsg(name="user", content=(
            "<system-reminder>Your previous response was rejected before any of it ran: "
            f"{failure.code}. Answer again with at most one tool call, its arguments a single complete "
            f"JSON object, and its name one of: {', '.join(TOOL_NAMES)}.</system-reminder>"))

    def _steps_remaining(self) -> int:
        return self.context.budget.limits.agent_steps - self.context.budget.steps_used

    def _bind_guard(self) -> None:
        """Install the phase's guard on the model this runtime runs."""
        self.model.bind(self.middleware.before_model_call)

    @staticmethod
    def _require_guardable(model) -> None:
        """Refuse a model the phase could not bound.

        Raises:
            TypeError: the model cannot take the guard (so no call would be charged to the
                step budget), streams (so a response could not be checked whole before the
                SDK acted on it), or does not carry the configured output limit (so the
                request would leave without a ceiling while the attempt record claimed one).
        """
        if not callable(getattr(model, "bind", None)):
            raise TypeError("the exploration model must accept the phase guard: create it with "
                            "AgentScopeModelFactory.create, or no model call is charged to the step budget")
        if getattr(model, "stream", None) is not False:
            raise TypeError("the exploration model must not stream: one whole response is checked "
                            "before the SDK acts on any part of it")
        try:
            ExplorationMiddleware.output_limit(model)
        except ValueError:
            raise TypeError("the exploration model must carry the configured output limit: the phase "
                            "resolves it, because the SDK sends no ceiling of its own") from None


__all__ = ["AGENT_NAME", "NO_CANDIDATE_REASON", "REPLY_STOPPED", "REPLY_STOPPED_BY_PHASE",
           "AgentScopeRuntime", "sdk_end_reason"]
