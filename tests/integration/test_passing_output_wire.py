import asyncio
import json
from dataclasses import replace
from tests.unit.test_controller import setup,publish,ask
from tests.integration.test_agentscope_runtime import environment,agent_context,tool_reply


def test_runner_passing_observation_reaches_real_sdk_http_body(tmp_path,projects,facts):
    text="from example.parser import parse\ndef test_one():\n    print('REAL_PASS_STDOUT_FACT')\n    assert parse([1]) == [1]\n"
    request,model,controller,ctx=setup(tmp_path/'product',projects,facts,plan=[publish(content=text),ask()])
    asyncio.run(controller.run(request,ctx))
    feedback=controller.explorer.contexts[1].feedback
    row=json.loads(json.loads(feedback)['verdict']['issues'][0]['measurements']['passing_outputs'][0])
    seen=[]
    def handler(request):
        data=json.loads(request.content)
        body=json.dumps(data['messages'],ensure_ascii=False)
        assert 'REAL_PASS_STDOUT_FACT' in body
        assert row['evidence_ref']['content_hash'] in body
        assert row['run_id'] in body
        seen.append(data)
        return tool_reply('request_information',{'question':'Need a supported narrower trigger'})
    env=environment(tmp_path/'sdk',projects,facts,handler=handler)
    context=replace(agent_context(env),feedback=feedback)
    async def go():
        try:return await env.runtime.explore(context)
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    assert asyncio.run(go()).kind=='request_information'
    assert len(seen)==1
    body=json.dumps(seen[0]['messages'],ensure_ascii=False)
    assert '正常对照' in body and '失败分支' in body
    assert '原版证据' in body and 'revise_contract' in body
