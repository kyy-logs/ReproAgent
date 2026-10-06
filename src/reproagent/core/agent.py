import uuid
import re
from dataclasses import asdict
from importlib.resources import files

from .models import IssueContract, ModelRequest
from .serialization import canonical_bytes, parse_json
from .tools import SCHEMAS, tool_schemas, validate_action
from .protocol import ModelOutputError, ModelProtocolError, contract_schema, object_schema, validate_contract
from .budget import BudgetStopped


PROTOCOL_ERROR_CODES = frozenset({'INVALID_ACTION_RESPONSE'})


class ActionProtocolError(ValueError):
    """No usable action within the bounded correction attempts; only a fixed code is recorded."""
    def __init__(self, code, message):
        if code not in PROTOCOL_ERROR_CODES:
            raise ValueError(f'unknown protocol error code: {code}')
        super().__init__(message)
        self.code = code


def prompt(name):
    return files('reproagent').joinpath('prompts/' + name + '.md').read_text(encoding='utf-8')


class ReproAgent:
    def __init__(self, gateway, context, candidate_parent='tests'):
        self.gateway, self.context, self.candidate_parent = gateway, context, candidate_parent
        self.expectation_evidence = None

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
        # Keep original facts visible during generation; a model's paraphrase
        # of an error object as a raised exception must not replace its source.
        self.expectation_evidence = {'description':description.text, 'sources':[asdict(s) for s in evidence.sources],
                                    'source_texts':list(evidence.texts)}
        return IssueContract(previous.contract_id if previous else 'contract-' + uuid.uuid4().hex, previous.version + 1 if previous else 1,
            description.content_hash, result.get('trigger', ''), expected, result.get('reported_actual', ''), tuple(result.get('observable_checks', [])),
            sources, tuple(result.get('assumptions', [])), missing, 'new evidence' if previous else '')

    async def next_action(self, context):
        schemas = tool_schemas()
        parent = self.candidate_parent.rstrip('/')
        file_properties = schemas['write_candidate']['properties']['files']['items']['properties']
        file_properties['path'].update({'pattern':'^' + re.escape(parent) + '/',
            'description':f'Install path under {parent}/; example {parent}/test_repro.py. Never substitute another test directory.'})
        data = {'contract':asdict(context.contract), 'files':[f.path for f in context.project.snapshot.files],
            'authoritative_expectations':self.expectation_evidence,
            'candidate_ids':[c.candidate_id for c in context.project.candidates], 'candidate_parent':self.candidate_parent,
            'history':list(context.history), 'feedback':context.feedback, 'tools':schemas,
            'candidate_file_schema':{'path':f'{parent}/test_repro.py','content':'UTF-8 test/data text','role':'test or data'}}
        for attempt in range(3):
            self.context.budget.check()
            if self.context.cancel_event.is_set():
                raise BudgetStopped('CANCELLED')
            remaining = self.context.budget.limits.agent_steps - self.context.budget.steps_used
            allowed = [name for name in SCHEMAS if context.project.candidates or name not in ('run_candidate', 'submit_candidate')]
            # Reserve first write/run/submit plus one possible correction. This
            # does not invent an expectation when authoritative facts are missing.
            if remaining <= 4 and context.contract.expected and not context.contract.missing_information:
                allowed = [name for name in allowed if name not in ('read_file', 'search_code')]
            data['allowed_actions'] = allowed
            data['budget'] = {'steps_remaining_including_this_attempt':remaining,
                              'seconds_remaining':max(0, self.context.budget.deadline - self.context.budget.clock())}
            data['response_schema'] = {'oneOf':[object_schema({'name':{'type':'string','const':name},
                                                           'parameters':schemas[name]}) for name in allowed]}
            self.context.budget.take_step()
            try:
                response = await self.gateway.complete(ModelRequest(({'role':'system','content':prompt('explore')},
                    {'role':'user','content':self._action_payload(data)}), 'action'), self.context)
            except ModelOutputError as exc:
                error = str(exc)
            else:
                try:
                    action = validate_action(parse_json(response.text))
                    if action.name not in allowed:
                        raise ValueError('choose an allowed action; reserve the remaining budget for writing, executing and submitting a candidate, or explain missing facts')
                    if action.name == 'write_candidate' and any(not f['path'].startswith(parent + '/') for f in action.parameters['files']):
                        raise ValueError(f'all candidate paths must start with {parent}/; for example {parent}/test_repro.py')
                except ValueError as exc:
                    error = str(exc)
                else:
                    return action
            data['protocol_error'] = error[:512] + '; return exactly ONE complete JSON object, no DSML, XML, prose or additional actions'
        raise ActionProtocolError('INVALID_ACTION_RESPONSE', 'action response invalid after 3 attempts: ' + error[:512])

    def _action_payload(self, data):
        cap = self.context.budget.limits.tool_response_bytes
        data.setdefault('file_count', len(data['files']))
        text = canonical_bytes(data).decode()
        while len(text.encode()) > cap and data['history']:
            data['history'].pop(0)
            text = canonical_bytes(data).decode()
        while len(text.encode()) > cap and data['files']:
            data['files'] = data['files'][:len(data['files']) // 2]
            data['file_index_truncated'] = True
            text = canonical_bytes(data).decode()
        if len(text.encode()) > cap:
            data['feedback'] = data['feedback'].encode()[:1024].decode(errors='ignore')
            text = canonical_bytes(data).decode()
        if len(text.encode()) > cap:
            raise BudgetStopped('NEEDS_INFORMATION', 'contract/context cannot fit configured context budget')
        return text
