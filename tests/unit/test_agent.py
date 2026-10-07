import asyncio
import importlib
import json

import pytest
from reproagent.core.models import AgentContext, EvidenceContext, IssueContract, IssueDescription, ModelResponse, ProjectView, SourceRef
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


def phase_context(facts, tmp_path, projects, **contract):
    _, _, _, snapshot, _, _ = setup_runner(tmp_path, projects, facts)
    values = dict(contract_id='contract-1', version=1, expected='parse([]) returns []',
                  sources=(SourceRef('input/issue.md', 'hash'),), missing_information=())
    return AgentContext(IssueContract(**{**values, **contract}), ProjectView(snapshot))


def exploration_prompt(context, **kwargs):
    return importlib.import_module('reproagent.core.agent').exploration_prompt(context, **kwargs)


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


def test_contract_analysis_never_carries_the_fixed_version(tmp_path, projects, facts):
    # Contract analysis sees the issue and its sources; the target environment of the
    # original version, and every hidden repair material, stay out of the request.
    a, model, _ = agent({'expected':'return []','trigger':'empty','reported_actual':'IndexError','source_indices':[0],'missing_information':[]}, facts)
    asyncio.run(a.analyze(IssueDescription('bug','hash'), EvidenceContext((SourceRef('issue.md','hash'),), ('bug',))))
    assert 'fixed_repo' not in json.dumps(model.messages) and 'fixed-python' not in json.dumps(model.messages)
    assert set(json.loads(model.messages[-1]['content'])) == {'description', 'sources', 'texts', 'response_schema'}


def test_exploration_prompt_states_the_phase_rules(tmp_path, projects, facts):
    """The prompt carries the product's rules, not a JSON action protocol.

    The phase acts through tools, so the prompt states what the tools are for, which
    directory a candidate may be installed in, which lines may be cited, and that the
    Controller -- never the phase's own prose -- decides whether the bug reproduced.
    """
    text = exploration_prompt(phase_context(facts, tmp_path, projects), candidate_parent='checks')
    for tool in ('Read', 'Grep', 'Glob', 'write_candidate', 'revise_contract', 'request_information'):
        assert tool in text
    for absent in ('Bash', 'PowerShell', 'run_candidate', 'submit_candidate', 'shell'):
        assert absent not in text
    assert 'checks/test_repro.py' in text and 'tests/test_repro.py' not in text
    # The search is a regular expression, which is the opposite of the JSON action
    # protocol's literal substring search; the phase is told which one it has.
    assert 'regular expression' in text and 'not a literal string' in text
    assert 'evidence' in text and 'displayed whole' in text
    assert 'not a result' in text and 'never evidence' in text
    assert 'correctness standard' in text and 'proposal' in text
    assert 'return-versus-raise' in text


def test_exploration_prompt_is_built_from_a_phase_context_only(tmp_path, projects, facts):
    context = phase_context(facts, tmp_path, projects)
    assert exploration_prompt(context)
    for invalid in (None, object(), 'contract'):
        with pytest.raises(ValueError):
            exploration_prompt(invalid)
    unbound = AgentContext(IssueContract('', version=0), context.project)
    with pytest.raises(ValueError):
        exploration_prompt(unbound)


def test_exploration_prompt_uses_the_configured_candidate_parent(tmp_path, projects, facts):
    context = phase_context(facts, tmp_path, projects)
    for parent in ('tests', 'tests/unit', 'repro_tests'):
        text = exploration_prompt(context, candidate_parent=parent)
        assert f'{parent}/test_repro.py' in text
    with pytest.raises(ValueError):
        exploration_prompt(context, candidate_parent='  ')
