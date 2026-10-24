"""Check the actual SDK request, not only the prompt builder's return value."""
from importlib.resources import files

from tests.integration.test_agentscope_explorer import exploration, run_task
from tests.integration.test_agentscope_runtime import tool_reply
from reproagent.core.agent import reproduction_skill_checklist
from reproagent.core.models import TaskState


SKILL_RESOURCE = 'resources/skills/reproduce-bug/SKILL.md'


def skill_body():
    return files('reproagent').joinpath(SKILL_RESOURCE).read_text(encoding='utf-8').partition('\n---\n')[2].strip()


def test_skill_reaches_only_the_first_request_without_extra_tools_or_calls(tmp_path, projects, facts):
    request, controller, context, requests, answers = exploration(tmp_path, projects, facts, [
        tool_reply('Read', {'file_path': 'example/parser.py'}, call_id='read-source'),
        tool_reply('Read', {'file_path': 'example/parser.py'}, call_id='read-source-2'),
        tool_reply('request_information', {'question': '缺少触发条件'}, call_id='ask')])
    result = run_task(request, controller, context)
    body = skill_body()
    assert body
    assert result.status == TaskState.NEEDS_INFORMATION
    assert len(requests) == 3 and not answers
    assert context.budget.steps_used == 3
    systems = ['\n'.join(m['content'] for m in payload['messages'] if m['role'] == 'system')
               for payload in requests]
    # The full method is a first-request view; the compact card is a view of every request.
    # A third request is what separates "rendered fresh each time" from "persisted once and
    # rendered again": a persisted copy would make the card count at request 3 two, not one.
    assert [system.count(body) for system in systems] == [1, 0, 0]
    card = reproduction_skill_checklist()
    assert card
    assert [system.count(card) for system in systems] == [1, 1, 1]
    for system, payload in zip(systems, requests):
        assert 'name: reproduce-bug' not in system
        tool_names = {tool['function']['name'] for tool in payload['tools']}
        assert tool_names == {'Read', 'Grep', 'Glob', 'write_candidate', 'revise_contract', 'request_information'}
    assert any(m.get('tool_call_id') == 'read-source' for m in requests[1]['messages'])
    assert any(m.get('tool_call_id') == 'read-source-2' for m in requests[2]['messages'])


def test_skill_is_not_sent_to_contract_analysis(facts):
    import asyncio
    from tests.unit.test_agent import agent
    from reproagent.core.models import EvidenceContext, IssueDescription, SourceRef
    strategy, model, context = agent({'expected': 'return []', 'trigger': 'empty input', 'reported_actual': 'IndexError', 'source_indices': [0]}, facts)
    asyncio.run(strategy.analyze(IssueDescription('empty input', 'hash'),
        EvidenceContext((SourceRef('issue.md', 'hash'),), ('empty input returns []',))))
    assert all(skill_body() not in message['content'] for message in model.messages)
