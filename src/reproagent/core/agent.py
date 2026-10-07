"""The product's reproduction strategy: how an issue becomes a sourced contract.

The strategy owns two things and nothing else.  ``analyze`` turns an untrusted issue and
its source excerpts into an :class:`IssueContract`, with the bounded correction loop the
protocol requires; ``exploration_prompt`` states, in product terms, what the exploration
phase is for and which rules its answers are held to.  Neither of them decides whether a
candidate reproduces a bug: that is execution evidence, and it belongs to the Controller.

The phase's own mechanics -- one SDK agent, its toolkit, its message history and the
result that ends the phase -- are the runtime's, not this strategy's.  This class is what
the runtime is combined with, which is why it no longer speaks a JSON action protocol:
the phase acts through tools, and the Controller acts on a :class:`PhaseResult`.
"""
import uuid
from dataclasses import asdict
from importlib.resources import files

from .models import AgentContext, IssueContract, ModelRequest
from .serialization import canonical_bytes, parse_json
from .protocol import ModelOutputError, ModelProtocolError, contract_schema, validate_contract
from .budget import BudgetStopped

#: Where a controller-built exploration prompt puts the test area it was configured with.
CANDIDATE_PARENT_TOKEN = '{candidate_parent}'


def prompt(name):
    return files('reproagent').joinpath('prompts/' + name + '.md').read_text(encoding='utf-8')


def require_correctable_output(error):
    """A truncation, filter or oversized body cannot be corrected by asking again.

    Repeating it would only spend another billed attempt on the same request, so the
    logical call ends here and only the fixed code is reported.
    """
    if not error.retryable:
        raise ModelProtocolError(f'{error.code}: repeating the same request cannot correct it') from None


def exploration_prompt(context, candidate_parent='tests'):
    """The strategy the SDK exploration phase of *context* runs under.

    The prompt is product policy, not a description of the phase's current state: the
    contract, the feedback and the phase history reach the phase as its own message, and
    the runtime keeps them current across phases.  Only the test area is substituted here,
    because it is configuration the phase cannot read from its context and must not guess.

    Args:
        context: the phase this prompt is built for.  A prompt is only defined for a phase
            that runs under a bound contract version, so a caller cannot build one for a
            bare model request.
        candidate_parent: the configured test area; candidate files are installed only here.

    Returns:
        `str`: the system prompt of the phase's SDK agent.

    Raises:
        ValueError: *context* is not the phase context this strategy explores for.
    """
    if not isinstance(context, AgentContext):
        raise ValueError('the exploration prompt is built from the phase context, not from a bare request')
    if not context.contract.contract_id or context.contract.version < 1:
        raise ValueError('an exploration phase runs under a bound contract version')
    parent = str(candidate_parent).strip().rstrip('/')
    if not parent or parent == '.':
        raise ValueError('the exploration phase needs a configured test area')
    return prompt('explore').replace(CANDIDATE_PARENT_TOKEN, parent)


class ReproAgent:
    """The contract-analysis strategy, over a model gateway and one call context."""

    def __init__(self, gateway, context, candidate_parent='tests'):
        self.gateway, self.context, self.candidate_parent = gateway, context, candidate_parent

    async def analyze(self, description, evidence):
        data = {'description':description.text, 'sources':[asdict(s) for s in evidence.sources], 'texts':list(evidence.texts),
                'response_schema':contract_schema(len(evidence.sources))}
        for attempt in range(3):
            self.context.budget.check()
            if self.context.cancel_event.is_set():
                raise BudgetStopped('CANCELLED')
            try:
                response = await self.gateway.complete(ModelRequest(({'role':'system','content':prompt('analyze_issue')},
                    {'role':'user','content':canonical_bytes(data).decode()}), 'contract'), self.context)
            except ModelOutputError as exc:
                require_correctable_output(exc)
                error = str(exc)
            else:
                try:
                    result = validate_contract(parse_json(response.text), len(evidence.sources))
                except ValueError as exc:
                    error = str(exc)
                else:
                    break
            if attempt == 2:
                raise ModelProtocolError(f'contract response invalid after 3 attempts: {error}')
            # Keep authoritative input; do not promote rejected model text to evidence.
            data['protocol_error'] = error[:512]
        indices = result.get('source_indices', [])
        sources = tuple(evidence.sources[i] for i in indices)
        expected = result.get('expected', '')
        missing = tuple(result.get('missing_information', []))
        if not sources or not expected:
            expected = ''
            missing = (*missing, 'Provide a source defining expected correct behavior.')
        previous = evidence.previous_contract
        material_change = previous and (expected != previous.expected or result.get('trigger', '') != previous.trigger or result.get('reported_actual', '') != previous.reported_actual or tuple(result.get('observable_checks', [])) != previous.observable_checks)
        if material_change and not any(s not in previous.sources for s in sources):
            raise ValueError('contract revision requires new evidence')
        return IssueContract(previous.contract_id if previous else 'contract-' + uuid.uuid4().hex, previous.version + 1 if previous else 1,
            description.content_hash, result.get('trigger', ''), expected, result.get('reported_actual', ''), tuple(result.get('observable_checks', [])),
            sources, tuple(result.get('assumptions', [])), missing, 'new evidence' if previous else '')
