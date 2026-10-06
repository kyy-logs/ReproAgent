import asyncio
import json

import pytest

from reproagent.core.agent import ReproAgent
from reproagent.core.budget import BudgetedGateway, BudgetStopped
from reproagent.core.models import AgentContext, BudgetLimits, EvidenceContext, IssueContract, IssueDescription, ModelResponse, ProjectView, SourceRef
from tests.integration.test_runner import setup_runner


class Responses:
    def __init__(self, *values):
        self.values, self.requests = values, []
    async def complete(self, request, context):
        self.requests.append(request)
        value = self.values[min(len(self.requests)-1, len(self.values)-1)]
        return ModelResponse(value if isinstance(value, str) else json.dumps(value))


VALID_READ = {'name': 'read_file', 'parameters': {'path': 'example/parser.py', 'start': 1, 'end': 2}}


@pytest.mark.parametrize('invalid', ['{}\n{}', '{"name":"read_file"}<DSML>invoke</DSML>', '[]',
                                   {'name':[], 'parameters':{}}, {'name':{}, 'parameters':{}}])
def test_action_format_is_corrected_with_each_attempt_counted(tmp_path, projects, facts, invalid):
    _, _, _, snapshot, _, _ = setup_runner(tmp_path, projects, facts)
    model = Responses(invalid, VALID_READ)
    context = facts.context()
    agent = ReproAgent(BudgetedGateway(model), context)
    result = asyncio.run(agent.next_action(AgentContext(IssueContract('c'), ProjectView(snapshot))))
    assert result.name == 'read_file' and result.parameters['path'] == 'example/parser.py'
    assert context.budget.steps_used == context.budget.unknown_cost_calls == 2
    repair = json.loads(model.requests[1].messages[-1]['content'])
    assert repair['protocol_error'] and repair['contract']['contract_id'] == 'c'


def test_action_correction_is_finite_and_does_not_extract_an_ambiguous_first_action(tmp_path, projects, facts):
    _, _, _, snapshot, _, _ = setup_runner(tmp_path, projects, facts)
    model = Responses(json.dumps(VALID_READ) + '\n' + json.dumps(VALID_READ))
    context = facts.context()
    agent = ReproAgent(BudgetedGateway(model), context)
    with pytest.raises(ValueError):
        asyncio.run(agent.next_action(AgentContext(IssueContract('c'), ProjectView(snapshot))))
    assert context.budget.steps_used == context.budget.unknown_cost_calls == len(model.requests) == 3


def test_action_repair_cannot_exceed_remaining_budget(tmp_path, projects, facts):
    _, _, _, snapshot, _, _ = setup_runner(tmp_path, projects, facts)
    model = Responses('{}', VALID_READ)
    context = facts.context(limits=BudgetLimits(agent_steps=1))
    agent = ReproAgent(BudgetedGateway(model), context)
    with pytest.raises(BudgetStopped):
        asyncio.run(agent.next_action(AgentContext(IssueContract('c'), ProjectView(snapshot))))
    assert context.budget.steps_used == len(model.requests) == 1


def test_candidate_path_correction_preserves_configured_parent(tmp_path, projects, facts):
    _, _, _, snapshot, _, _ = setup_runner(tmp_path, projects, facts, parent='repro_tests')
    wrong = {'name': 'write_candidate', 'parameters': {'files': [{'path': 'tests/test_repro.py', 'content': 'def test_a(): assert False', 'role': 'test'}], 'hypothesis': 'empty'}}
    right = json.loads(json.dumps(wrong)); right['parameters']['files'][0]['path'] = 'repro_tests/test_repro.py'
    model = Responses(wrong, right)
    context = facts.context()
    agent = ReproAgent(BudgetedGateway(model), context, candidate_parent='repro_tests')
    result = asyncio.run(agent.next_action(AgentContext(IssueContract('c', expected='return []'), ProjectView(snapshot))))
    assert result.parameters['files'][0]['path'] == 'repro_tests/test_repro.py'
    assert context.budget.steps_used == 2


def test_cancel_between_action_corrections_sends_no_further_request(tmp_path, projects, facts):
    _, _, _, snapshot, _, _ = setup_runner(tmp_path, projects, facts)
    class CancellingModel(Responses):
        async def complete(self, request, context):
            response = await super().complete(request, context)
            context.cancel_event.set()
            return response
    model = CancellingModel('{}', VALID_READ)
    context = facts.context()
    with pytest.raises(BudgetStopped):
        asyncio.run(ReproAgent(BudgetedGateway(model), context).next_action(AgentContext(IssueContract('c'), ProjectView(snapshot))))
    assert len(model.requests) == 1


def test_invalid_gateway_configuration_is_not_retried_as_action_output(tmp_path, projects, facts):
    _, _, _, snapshot, _, _ = setup_runner(tmp_path, projects, facts)
    class InvalidConfiguration(Responses):
        async def complete(self, request, context):
            self.requests.append(request)
            raise ValueError('API key environment variable is missing')
    model = InvalidConfiguration()
    context = facts.context()
    with pytest.raises(ValueError, match='API key environment variable is missing'):
        asyncio.run(ReproAgent(model, context).next_action(AgentContext(IssueContract('c'), ProjectView(snapshot))))
    assert len(model.requests) == context.budget.steps_used == 1


def test_action_keeps_raw_expectation_evidence_when_contract_rephrases_it(tmp_path, projects, facts):
    _, _, _, snapshot, _, _ = setup_runner(tmp_path, projects, facts)
    description = 'The validator returns a ValueError object; it does not raise that object.'
    contract_response = {'trigger':'invalid value', 'expected':'raises ValueError', 'reported_actual':'returns True',
                         'source_indices':[0], 'observable_checks':[], 'assumptions':[], 'missing_information':[]}
    model = Responses(contract_response, VALID_READ)
    agent = ReproAgent(model, facts.context())
    source = SourceRef('input/issue.md', 'source-hash')
    contract = asyncio.run(agent.analyze(IssueDescription(description, 'source-hash'), EvidenceContext((source,), (description,))))
    asyncio.run(agent.next_action(AgentContext(contract, ProjectView(snapshot))))
    payload = json.loads(model.requests[-1].messages[-1]['content'])
    assert payload['authoritative_expectations']['description'] == description
    assert payload['authoritative_expectations']['source_texts'] == [description]
    assert payload['authoritative_expectations']['sources'][0]['content_hash'] == 'source-hash'
