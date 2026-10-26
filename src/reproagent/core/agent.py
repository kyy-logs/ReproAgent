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

#: Where it states what history this round has.  The sentence is instruction, not data: the
#: count and the categories travel, and the cards' names and bodies stay behind the tools.
EXPERIENCE_CATALOG_TOKEN = '{experience_catalog}'


def experience_catalog_sentence(notice):
    """How much history this round has, and of what kind, in one instruction-zone line."""
    if notice is None:
        return 'This round has no historical cards.'
    categories = list(notice.categories)
    if not categories:
        return f'This round has {notice.count} historical cards.'
    if notice.count == 1:
        return f'This round has 1 historical card ({categories[0]}).'
    listed = categories[0] if len(categories) == 1 else ', '.join(categories[:-1]) + ' and ' + categories[-1]
    return f'This round has {notice.count} historical cards across {listed}.'


def prompt(name):
    return files('reproagent').joinpath('prompts/' + name + '.md').read_text(encoding='utf-8')


def require_correctable_output(error):
    """A truncation, filter or oversized body cannot be corrected by asking again.

    Repeating it would only spend another billed attempt on the same request, so the
    logical call ends here and only the fixed code is reported.
    """
    if not error.retryable:
        raise ModelProtocolError(f'{error.code}: repeating the same request cannot correct it') from None


def exploration_prompt(context, candidate_parent='tests', *, experience_enabled=False, experience_mode='legacy'):
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
    sentence = experience_catalog_sentence(context.experience_catalog)
    if context.experience_catalog is not None and context.experience_catalog.category_counts:
        sentence += ' Category counts: ' + ', '.join(
            f'{category}={count}' for category, count in context.experience_catalog.category_counts) + '.'
    text = prompt('explore').replace(CANDIDATE_PARENT_TOKEN, parent).replace(
        EXPERIENCE_CATALOG_TOKEN, sentence)
    if not experience_enabled:
        text = text.partition('Optional historical experience tools')[0].rstrip() + '\n'
    if experience_enabled and experience_mode == 'draft_review':
        text += (
            '\nWhen you submit write_candidate, you may include one optional lesson_draft '
            'with exactly situation, observation, lesson and source_ids. situation must say '
            'when a future task should load it, including the observed operation/error and '
            'supported version or input conditions; observation states what was tried and '
            'what happened; lesson separates known blockers, checks and untested mitigation; '
            'source_ids must copy 1-3 learning_source_id values from <learning_source> notices '
            'or this phase\'s learning_sources list. File paths, content hashes and SDK tool-call IDs '
            'are not learning source IDs. Summarize an actually observed pitfall, not the issue '
            'itself or a guessed root cause. Tool/protocol refusals, path or permission errors, '
            'citation failures, exploration order and budget waste can support a workflow lesson; '
            'source-code knowledge is not required. Describe the observed condition, attempted '
            'operation, actual result and a concrete next check, citing a displayed learning source. '
            'For example, a Read refusal requiring an absolute path supports checking the frozen '
            'source path before reading, not a claim that any absolute path is permitted. Copy the '
            'source ID from that actual refusal. A known root cause or successful workaround is '
            'not required; mark untested advice as unverified. Use lesson_status="none" when '
            'support is missing, the lesson is only speculation, it merely repeats an existing '
            'rule without useful observed conditions, or no concrete next check follows. '
            'An error alone does not require a draft; lack of source-code facts alone does not '
            'justify none. A new '
            'candidate has not run yet, so do not claim its recovery is verified. The draft '
            'is optional and never blocks candidate publication. '
            'Keep single REPRODUCED observations separate from confirmed replay. '
            'A null classification is unresolved, not NOT_REPRODUCED; MODEL_PROTOCOL_ERROR '
            'alone does not establish that the candidate test is wrong. Call edits equivalent only after '
            'comparing the code, not merely identical failed test names; otherwise mark '
            'equivalence as unverified.\n')
    return text


class ReproAgent:
    """The contract-analysis strategy, over a model gateway and one call context."""

    def __init__(self, gateway, context, candidate_parent='tests'):
        self.gateway, self.context, self.candidate_parent = gateway, context, candidate_parent

    async def analyze(self, description, evidence):
        previous = evidence.previous_contract
        data = {'description':description.text, 'sources':[asdict(s) for s in evidence.sources], 'texts':list(evidence.texts),
                'response_schema':contract_schema(len(evidence.sources))}
        if previous is not None:
            # A revision must see the constraints it is revising. Keep this
            # derived interpretation separate from the indexed original sources:
            # it neither adds a citable source nor changes source_indices.
            data['previous_contract'] = {
                'contract_id': previous.contract_id, 'version': previous.version,
                'trigger': previous.trigger, 'expected': previous.expected,
                'reported_actual': previous.reported_actual,
                'observable_checks': list(previous.observable_checks),
                'assumptions': list(previous.assumptions),
                'missing_information': list(previous.missing_information),
            }
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
        material_change = previous and (expected != previous.expected or result.get('trigger', '') != previous.trigger or result.get('reported_actual', '') != previous.reported_actual or tuple(result.get('observable_checks', [])) != previous.observable_checks)
        if material_change and not any(s not in previous.sources for s in sources):
            raise ValueError('contract revision requires new evidence')
        return IssueContract(previous.contract_id if previous else 'contract-' + uuid.uuid4().hex, previous.version + 1 if previous else 1,
            description.content_hash, result.get('trigger', ''), expected, result.get('reported_actual', ''), tuple(result.get('observable_checks', [])),
            sources, tuple(result.get('assumptions', [])), missing, 'new evidence' if previous else '')
