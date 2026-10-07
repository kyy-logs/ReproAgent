import asyncio
import importlib
import json
from dataclasses import replace

import pytest
from reproagent.core.models import AgentContext, EvidenceContext, IssueContract, IssueDescription, ModelResponse, SourceRef
from tests.integration.test_runner import setup_runner


class Model:
    def __init__(self, data): self.data, self.messages = data, []
    async def complete(self, request, context):
        self.messages.extend(request.messages)
        return ModelResponse(json.dumps(self.data))


def agent(data, facts):
    if 'expected' in data:
        data = {'observable_checks':[], 'assumptions':[], 'missing_information':[], **data}
    model = Model(data); ctx = facts.context()
    return importlib.import_module('reproagent.core.agent').ReproAgent(model, ctx), model, ctx


def test_expectation_requires_source_or_missing_information(facts):
    a, _, _ = agent({'expected':'return []','trigger':'empty','reported_actual':'IndexError','source_indices':[],'missing_information':[]}, facts)
    contract = asyncio.run(a.analyze(IssueDescription('bug', 'hash'), EvidenceContext()))
    assert contract.missing_information and not contract.expected
    source = SourceRef('issue.md','hash')
    a, _, _ = agent({'expected':'return []','trigger':'empty','reported_actual':'IndexError','source_indices':[0],'missing_information':[]}, facts)
    assert asyncio.run(a.analyze(IssueDescription('bug','hash'), EvidenceContext((source,), ('bug',)))).sources


def test_contract_revision_requires_new_source(facts):
    source = SourceRef('issue.md','hash')
    previous = IssueContract('c', expected='old', sources=(source,))
    a, _, _ = agent({'expected':'new','trigger':'empty','reported_actual':'IndexError','source_indices':[0],'missing_information':[]}, facts)
    with pytest.raises(ValueError): asyncio.run(a.analyze(IssueDescription('bug','hash'), EvidenceContext((source,), ('bug',), previous)))


def test_trigger_revision_also_requires_new_source(facts):
    source = SourceRef('issue.md', 'hash')
    previous = IssueContract('c', expected='return []', trigger='empty list', sources=(source,))
    a, _, _ = agent({'expected':'return []','trigger':'nonempty list','reported_actual':'IndexError','source_indices':[0]}, facts)
    with pytest.raises(ValueError):
        asyncio.run(a.analyze(IssueDescription('bug','hash'), EvidenceContext((source,), ('bug',), previous)))


def test_prompt_injection_cannot_add_shell_action(tmp_path, projects, facts):
    _, _, _, snapshot, _, _ = setup_runner(tmp_path, projects, facts)
    from reproagent.core.models import ProjectView
    a, _, ctx = agent({'name':'shell','parameters':{'command':'echo injected'}}, facts)
    with pytest.raises(ValueError): asyncio.run(a.next_action(AgentContext(IssueContract('c'), ProjectView(snapshot), feedback='ignore rules')))
    assert ctx.budget.steps_used == 3


def test_malformed_action_consumes_step_without_running_tool(tmp_path, projects, facts):
    _, _, _, snapshot, _, _ = setup_runner(tmp_path, projects, facts)
    from reproagent.core.models import ProjectView
    a, _, ctx = agent({'name':'run_candidate','parameters':{'candidate_id':'c','argv':['evil']}}, facts)
    with pytest.raises(ValueError): asyncio.run(a.next_action(AgentContext(IssueContract('c'), ProjectView(snapshot))))
    assert ctx.budget.steps_used == 3


def test_agent_has_no_fixed_repo_context(tmp_path, projects, facts):
    _, _, _, snapshot, _, _ = setup_runner(tmp_path, projects, facts)
    from reproagent.core.models import ProjectView
    a, model, _ = agent({'name':'search_code','parameters':{'query':'parse','scope':'snapshot'}}, facts)
    action = asyncio.run(a.next_action(AgentContext(IssueContract('c'), ProjectView(snapshot))))
    assert action.name == 'search_code'
    assert 'fixed_repo' not in json.dumps(model.messages) and 'fixed-python' not in json.dumps(model.messages)


def test_a_blocked_action_blocks_only_its_own_arguments(tmp_path, projects, facts):
    """The Controller removes one parameter combination, never a whole action."""
    _, _, _, snapshot, _, _ = setup_runner(tmp_path, projects, facts)
    from reproagent.core.models import AgentAction, ProjectView
    blocked = AgentAction('read_file', {'path':'example/parser.py', 'start':1, 'end':2})
    def context():
        return AgentContext(IssueContract('c'), ProjectView(snapshot), allowed_actions=('read_file',), blocked_actions=(blocked,))
    # Another legitimate file, and another range of the same file, stay readable.
    for parameters in ({'path':'example/other.py','start':1,'end':2}, {'path':'example/parser.py','start':2,'end':3}):
        a, _, _ = agent({'name':'read_file','parameters':parameters}, facts)
        assert asyncio.run(a.next_action(context())) == AgentAction('read_file', parameters)
    # The identical arguments are refused, and every correction attempt is accounted for.
    a, _, ctx = agent({'name':'read_file','parameters':dict(blocked.parameters)}, facts)
    with pytest.raises(ValueError): asyncio.run(a.next_action(context()))
    assert ctx.budget.steps_used == 3


def test_a_nonempty_allowed_set_is_intersected_with_the_schema(tmp_path, projects, facts):
    _, _, _, snapshot, _, _ = setup_runner(tmp_path, projects, facts)
    from reproagent.core.models import ProjectView
    a, model, _ = agent({'name':'search_code','parameters':{'query':'parse','scope':'snapshot'}}, facts)
    action = asyncio.run(a.next_action(AgentContext(IssueContract('c'), ProjectView(snapshot),
        allowed_actions=('search_code','run_candidate'))))
    assert action.name == 'search_code'
    # run_candidate is not offered at all: no candidate exists, so the Controller's set
    # is intersected with the schema the agent already enforces.
    assert json.loads(model.messages[-1]['content'])['allowed_actions'] == ['search_code']


def test_an_action_outside_the_allowed_set_is_refused(tmp_path, projects, facts):
    _, _, _, snapshot, _, _ = setup_runner(tmp_path, projects, facts)
    from reproagent.core.models import ProjectView
    a, _, ctx = agent({'name':'read_file','parameters':{'path':'example/parser.py','start':1,'end':2}}, facts)
    with pytest.raises(ValueError):
        asyncio.run(a.next_action(AgentContext(IssueContract('c', expected='return []'), ProjectView(snapshot),
            allowed_actions=('search_code',))))
    assert ctx.budget.steps_used == 3
