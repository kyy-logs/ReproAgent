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
from dataclasses import asdict

from agentscope.message import SystemMsg, ToolCallBlock, ToolResultBlock, TextBlock
from agentscope.middleware import MiddlewareBase
from agentscope.model import ChatResponse
from agentscope.tool import ToolResponse
from agentscope.permission import PermissionBehavior, PermissionDecision

from .dependency import require_agentscope
from .tools import TOOL_NAMES
from ...core.budget import BudgetStopped
from ...core.exploration_progress import step_budget_view, execution_budget_policy
from ...core.models import AgentContext
from ...core.protocol import ModelProtocolError
from ...core.serialization import canonical_hash, parse_json
from ...observability import mark, tool_call_key
from ...experience_triggers import ERROR_METADATA, PROTOCOL_CODES, trigger_from_fact
from .progress import render_progress
from .protocol_diagnostics import ToolInputDiagnostic, tool_input_diagnostic

#: The phase these events belong to; one runtime runs the exploration phases of one task.
PHASE = "EXPLORATION"

#: The controlled codes a rejected response can carry.  A code names the shape of the
#: refusal, never the text that caused it.
PHASE_PROTOCOL_CODES = PROTOCOL_CODES

#: How many responses one phase may spend on protocol correction, budget permitting.
PROTOCOL_ATTEMPTS = 3

#: The tools that only read.  They are the ones a phase can spend its whole budget on: the
#: SDK's ReAct loop has no reason to stop reading a large repository, and the fixed-point
#: run spent 20 of 20 decisions on Read/Grep/Glob without ever publishing a candidate.
READER_TOOLS = ("Read", "Grep", "Glob", "list_experiences", "read_experience")



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

    def __init__(self, code: str, *, diagnostic: ToolInputDiagnostic | None = None) -> None:
        if code not in PHASE_PROTOCOL_CODES:
            raise ValueError(f"unknown phase protocol code: {code}")
        self.code = code
        self.diagnostic = diagnostic or ToolInputDiagnostic(code)
        self.failures: tuple[str, ...] = ()
        super().__init__(f'{code}: exploration response rejected ({self.diagnostic.error_category})')

    def set_failures(self, failures: tuple[str, ...]) -> None:
        if not 1 <= len(failures) <= PROTOCOL_ATTEMPTS or any(code not in PHASE_PROTOCOL_CODES for code in failures):
            raise ValueError('invalid phase protocol refusal history')
        self.failures = tuple(failures)
        location = (f' at line {self.diagnostic.line} column {self.diagnostic.column}'
                    if self.diagnostic.line is not None else '')
        self.args = (f'{self.code}: phase protocol refusals {len(failures)}/{PROTOCOL_ATTEMPTS}: '
                     f'{" -> ".join(failures)}; {self.diagnostic.error_category}{location}',)

    def details(self) -> dict:
        return {**self.diagnostic.fields(), 'protocol_failures_used': len(self.failures),
                'protocol_failures_limit': PROTOCOL_ATTEMPTS, 'protocol_failure_codes': list(self.failures)}


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

    def __init__(self, context, gate, store=None, *, allowed_tools=TOOL_NAMES, experience_event=None, experience_input=None, browse_gate=None) -> None:
        require_agentscope()
        self.context, self.gate, self.store = context, gate, store
        self.allowed_tools = tuple(allowed_tools)
        self.components = component_versions()
        #: The phase's contract, handed over by the runtime when the phase starts.  Until
        #: then there is nothing to reserve the last steps for.
        self.contract = None
        self.phase_context: AgentContext | None = None
        self.experience_event, self.experience_input = experience_event, experience_input
        self.browse_gate = browse_gate
        self._browse_delivery_info = {}
        self._convergence_recorded = False

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
        view = step_budget_view(self.context.budget.limits.agent_steps, self.context.budget.steps_used)
        self.context.budget.take_step()
        self._record_budget_progress(view)

    def _record_budget_progress(self, view) -> None:
        """Observe admission, not HTTP delivery or adoption; never spend another step."""
        converted = view.mode == 'converging' and not self._convergence_recorded
        if converted:
            self._convergence_recorded = True
        operations = [
            lambda: self.record('exploration.step', result_code='STEP', budget_mode=view.mode,
                soft_threshold=view.soft_threshold, steps_used_before_call=view.used_steps,
                remaining_steps_before_call=view.remaining_steps, next_call_index=view.next_call_index),
            lambda: mark('budget.progress', attributes={'budget': asdict(view)}),
        ]
        if converted:
            operations.extend([
                lambda: self.record('budget.convergence', result_code='CONVERGING', budget=asdict(view)),
                lambda: mark('budget.convergence', attributes={'budget': asdict(view)}),
            ])
        for operation in operations:
            try:
                operation()
            except BudgetStopped:
                raise
            except Exception:
                pass  # Each optional sink is independent; a failed one cannot block reproduction.

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
        if self.experience_input is not None:
            try:
                input_kwargs = await self.experience_input(agent, input_kwargs)
            except BudgetStopped:
                raise
            except Exception:
                pass  # Optional advice cannot replace the original model input.
        results = []
        if self.browse_gate is not None and 'list_experiences' in self.allowed_tools:
            for message in input_kwargs['messages']:
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        text = block.output if isinstance(block.output, str) else ''.join(
                            item.text for item in block.output if isinstance(item, TextBlock))
                        results.append((block.id, block.name, str(block.state), text))
            state = 'satisfied' if self.browse_gate.has_delivery(results) else self.browse_gate.state
            hint = (' First call list_experiences(category) with an explicit category and no cursor.'
                    if state == 'pending' else ' Initial browsing is no longer required; details and further pages are optional.')
            # This is a fresh per-call program view, not another persistent history message.
            input_kwargs = {**input_kwargs, 'messages': [*input_kwargs['messages'],
                SystemMsg(name='experience-browse', content=f'Current experience browse state: {state}.' + hint)]}
        # Rebuild from task counters before the guarded model takes this call's step.
        # This message is local to this input, never another persistent history entry.
        if not self.gate.finished and not self.context.cancel_event.is_set() and self.steps_remaining > 0:
            self.context.budget.check()
            view = step_budget_view(self.context.budget.limits.agent_steps, self.context.budget.steps_used)
            try:
                progress = render_progress(view, self.phase_context)
            except ValueError as exc:
                raise BudgetStopped('NEEDS_INFORMATION', 'exploration progress exceeds its bounded message') from exc
            input_kwargs = {**input_kwargs, 'messages': [*input_kwargs['messages'],
                SystemMsg(name='exploration-progress', content=progress)]}
            await self._check_progress_capacity(agent, input_kwargs['messages'], input_kwargs['tools'])
        model = input_kwargs["current_model"]
        response = await model(
            messages=input_kwargs["messages"], tools=input_kwargs["tools"],
            tool_choice=input_kwargs["tool_choice"], **self.output_limit(model))
        if self.browse_gate is not None:
            if self.browse_gate.confirm_delivery(results):
                self.record_browse(**self._browse_delivery_info)
        self.check_response(response, allowed_tools=self.allowed_tools)
        return response

    async def _check_progress_capacity(self, agent, messages, tools) -> None:
        """Count the actual input after ephemeral notices, without spending a step."""
        if self.gate.finished or self.context.cancel_event.is_set() or self.steps_remaining <= 0:
            return  # The model guard owns these stops, with their established precedence.
        self.context.budget.check()
        estimated = await agent.model.count_tokens(messages=messages, tools=tools)
        if self.gate.finished or self.context.cancel_event.is_set() or self.steps_remaining <= 0:
            return
        self.context.budget.check()
        if estimated >= agent.context_config.trigger_ratio * agent.model.context_size:
            raise BudgetStopped('NEEDS_INFORMATION',
                                'the final exploration input including budget progress reached the model context threshold')

    async def on_check_permission(self, agent, input_kwargs, next_handler):
        """Refuse every tool outside the phase surface, and never ask a user who is absent.

        The six phase tools are still decided by the SDK's own engine, so a configured
        deny rule keeps winning over them; what cannot happen is an answer that parks the
        phase on a confirmation nobody can give, or a tool the phase never registered.
        Once only the reserved steps are left, the readers are refused too, whatever the
        contract holds, so a large repository cannot consume the budget the ending needs.
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
                    behavior=PermissionBehavior.DENY, decision_reason="the last steps have to end the phase",
                    message=self._reserved_message(name))
            else:
                code = "ALLOWED"
        else:
            decision = PermissionDecision(
                behavior=PermissionBehavior.DENY, decision_reason="outside the phase tool set",
                message=f"{name or 'this tool'} is not part of the exploration phase")
        browse = self.browse_gate
        if browse is not None and browse.state == 'pending':
            if code != 'ALLOWED' or decision.behavior is not PermissionBehavior.ALLOW:
                if name == 'list_experiences':
                    browse.bypass(code)
                    self.record_browse()
            else:
                params = input_kwargs.get('tool_input') or {}
                valid_first = (name == 'list_experiences'
                    and params.get('category') in ('framework', 'model', 'workflow')
                    and params.get('cursor') is None)
                if not valid_first:
                    previous = browse.state
                    browse.reject()
                    code = 'EXPERIENCE_BROWSE_REQUIRED'
                    decision = PermissionDecision(behavior=PermissionBehavior.DENY,
                        decision_reason=code,
                        message='EXPERIENCE_BROWSE_REQUIRED: first call list_experiences with an explicit '
                            'framework, model or workflow category and no cursor. This call was not executed.')
                    if browse.state != previous:
                        self.record_browse()
        # The decision is projected, never asked again: a second permission check would
        # be a second decision.  The arguments are captured once, here, whichever way
        # the decision went -- a refused call is exactly the one worth looking at.
        call_key = tool_call_key(sdk_call_id=getattr(input_kwargs.get("tool_call"), "id", ""),
                                 step_index=self.context.budget.steps_used)
        permission_code = 'DENIED' if code == 'EXPERIENCE_BROWSE_REQUIRED' else code
        attributes = {"tool": name if name in self.allowed_tools else "", "result_code": permission_code}
        if code == 'EXPERIENCE_BROWSE_REQUIRED':
            attributes['error_category'] = code
        if call_key is not None:
            attributes["tool_call_key"] = call_key
        mark("permission", attributes=attributes).capture(
            "sdk_tool_input", input_kwargs.get("tool_input"), source="sdk_tool_input")
        self.record("exploration.action", action=name if name in self.allowed_tools else "", result_code=code,
                    arguments_hash=self._arguments_hash(input_kwargs.get("tool_input")))
        if code != "ALLOWED":
            self._experience_fact({"kind":"permission", "tool":name, "code":code})
        return decision

    def _experience_fact(self, fact):
        if self.experience_event is None or trigger_from_fact(fact) is None:
            return
        try:
            self.experience_event(fact)
        except BudgetStopped:
            raise
        except Exception:
            pass

    async def on_acting(self, agent, input_kwargs, next_handler):
        # Terminal metadata comes from the tool itself; no error-prose matching.
        call = input_kwargs.get("tool_call")
        name = getattr(call, "name", "")
        observed = False
        gen = next_handler()
        try:
            async for item in gen:
                if isinstance(item, ToolResponse) and not observed:
                    observed = True
                    if name == 'list_experiences' and self.browse_gate is not None and self.browse_gate.state == 'pending':
                        text = ''.join(block.text for block in item.content if isinstance(block, TextBlock))
                        try:
                            data = parse_json(text)
                        except (ValueError, TypeError):
                            data = {}
                        code = data.get('code') if isinstance(data, dict) else None
                        if str(item.state) == 'success' and code in ('ready', 'empty'):
                            self.browse_gate.stage_delivery(call.id, text)
                            self._browse_delivery_info = {
                                'category': data.get('category', ''),
                                'ids': [entry['id'] for entry in data.get('items', [])],
                                'bytes': len(text.encode('utf-8')),
                                'tool_call_key': tool_call_key(sdk_call_id=call.id, step_index=self.context.budget.steps_used),
                            }
                        elif code in ('response_capacity', 'catalog_budget', 'catalog_limit', 'catalog_unavailable'):
                            self.browse_gate.bypass(code)
                            self.record_browse()
                    if str(item.state) == "error":
                        fact = {"kind":"tool_error", "tool":name, "code":item.metadata.get(ERROR_METADATA)}
                        if trigger_from_fact(fact) is not None:
                            try:
                                self.record("exploration.tool_error", action=name, result_code=fact["code"])
                            except Exception:
                                pass  # Optional diagnostic I/O cannot discard this response.
                            self._experience_fact(fact)
                yield item
        finally:
            await gen.aclose()

    def _reserved_for_publishing(self, name) -> bool:
        """Use the same policy as the next-call notice, after the call charged a step."""
        count = self.phase_context.original_execution_count if self.phase_context is not None else 0
        policy = execution_budget_policy(self.context.budget.limits.agent_steps,
                                        self.context.budget.steps_used, count)
        return (name in READER_TOOLS and self.steps_remaining <= policy.reader_reserve
                and self.contract is not None)

    def _reserved_message(self, name) -> str:
        """What the reserved steps are for, which depends on what the phase can still do.

        A grounded contract holds everything a candidate needs, so those steps are for
        publishing it.  A contract that still misses facts cannot be published honestly --
        and the exploration phase is where those facts are found, which is why it keeps its
        readers until the reserve -- so its reserved steps are for the two endings that
        remain: the revision its reading justifies, or the report of what is missing.
        """
        count = self.phase_context.original_execution_count if self.phase_context is not None else 0
        purpose = ('Keep room to republish a repaired candidate.' if count else
                   'Publish a justified first candidate soon enough to execute and repair it.')
        contract = self.contract
        grounded = contract is not None and bool(contract.expected) and not contract.missing_information
        if grounded:
            return (f"{name} is refused: {self.steps_remaining} step(s) remain and publishing needs them. {purpose}"
                    " Write the candidate from what you already read, or use revise_contract or"
                    " request_information.")
        return (f"{name} is refused: {self.steps_remaining} step(s) remain and this phase has to end."
                " Read no further: use revise_contract to cite the evidence you already read, or"
                " request_information to report what is still missing.")

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

    def record_browse(self, **fields) -> None:
        if self.browse_gate is None:
            return
        try:
            self.record('experience.browse_gate', state=self.browse_gate.state,
                        reason=self.browse_gate.reason, **fields)
        except (OSError, ValueError, RuntimeError):
            pass  # Optional observation must not block reproduction.
        try:
            mark('experience.browse_gate', attributes={
                'result_code': self.browse_gate.state,
                'stop_reason': self.browse_gate.reason,
                'experience_ids': fields.get('ids', []),
                'tool_call_key': fields.get('tool_call_key', ''),
                'budget': {'catalog_bytes': fields.get('bytes', 0)},
            })
        except (OSError, ValueError, RuntimeError):
            pass

    def record_protocol_failure(self, failure: PhaseProtocolError) -> None:
        details = failure.details()
        operations = (
            lambda: self.record('exploration.protocol_error', result_code=failure.code, **details),
            lambda: mark('exploration.protocol_error', attributes={
                'result_code': failure.code, 'error_category': failure.diagnostic.error_category,
                'budget': details}),
        )
        for operation in operations:
            try:
                operation()
            except BudgetStopped:
                raise
            except Exception:
                pass  # Diagnostic sinks cannot replace the actual protocol decision.

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
            PhaseProtocolError: no actual tool call, more than one tool call (so no call
                may run before the others are judged), a name outside :data:`TOOL_NAMES`,
                or arguments that are not one complete JSON object.
        """
        if not isinstance(response, ChatResponse):   # pragma: no cover - the phase model never streams
            return
        calls = [block for block in response.content if isinstance(block, ToolCallBlock)]
        if len(calls) > 1:
            raise PhaseProtocolError("MULTIPLE_TOOL_CALLS")
        if not calls:
            raise PhaseProtocolError("MISSING_TOOL_CALL")
        if calls[0].name not in allowed_tools:
            raise PhaseProtocolError("UNKNOWN_TOOL")
        diagnostic = tool_input_diagnostic(calls[0].input)
        if diagnostic is not None:
            raise PhaseProtocolError('INVALID_TOOL_INPUT', diagnostic=diagnostic)

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
