import asyncio
import json
import sys
from dataclasses import replace

import pytest

from reproagent.core.models import ModelConfig, TaskState
from tests.unit.test_controller import setup, publish, ask
from tests.integration.test_agentscope_runtime import environment, agent_context, tool_reply


def test_target_requirement_is_saved_and_reaches_next_real_sdk_request(tmp_path, projects, facts):
    from reproagent.core.models import DeliveryRequirements
    seen=[]
    def handler(request):
        data=json.loads(request.content)
        seen.append(data)
        envelope=next(json.loads(m['content']) for m in data['messages']
            if isinstance(m.get('content'),str) and m['content'].startswith('{')
            and 'pinned-harness:offline' in m['content'])
        assert envelope['delivery'] == {'platform':'linux','source':'pinned-harness:offline'}
        assert envelope['execution_platform'] == 'win32'
        return tool_reply('request_information', {'question':'Need a target-compatible trigger'})
    env=environment(tmp_path, projects, facts, handler=handler)
    context=replace(agent_context(env), delivery=DeliveryRequirements('linux','pinned-harness:offline'),
                    execution_platform='win32')
    async def go():
        try: return await env.runtime.explore(context)
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    assert asyncio.run(go()).kind=='request_information'
    assert len(seen)==1


def test_unknown_target_collection_is_advisory_and_does_not_block_local_execution(tmp_path, projects, facts):
    from reproagent.core.models import DeliveryRequirements
    text=('import sys\nimport pytest\nfrom example.parser import parse\n'
          '@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows")\n'
          'def test_target(): assert parse([]) == []\n')
    request, model, controller, ctx=setup(tmp_path, projects, facts, plan=[publish(content=text),ask()])
    request=replace(request,delivery=DeliveryRequirements('linux','pinned-harness:offline'))
    result=asyncio.run(controller.run(request,ctx))
    assert list((request.output_dir/'runs').glob('*/execution.json'))
    assert controller.delivery_assessment['status']=='target_unverified'
    assert controller.delivery_assessment['target_platform']=='linux'
    assert controller.explorer.contexts[0].delivery.source=='pinned-harness:offline'
    saved=controller.store.load_record('request','request')
    assert saved.delivery==request.delivery
    assert controller.explorer.contexts[0].execution_platform==sys.platform


@pytest.mark.parametrize('platform,source',[('unknown','x'),('linux',''),('linux','x'*513)])
def test_delivery_requirements_refuse_unknown_or_unsourced_values(platform,source):
    from reproagent.core.models import DeliveryRequirements
    with pytest.raises(ValueError): DeliveryRequirements(platform,source)


def test_swt_batch_supplies_linux_from_pinned_harness(tmp_path,projects):
    from tests.integration.test_swt_batch import inputs
    from evals.swt_bench.run import run_batch
    from evals.schema import EvalResult
    catalog,manifest,bindings=inputs(tmp_path,projects)
    seen=[]
    async def run_one(case,model,output,**kwargs):
        seen.append(kwargs)
        return EvalResult(case.case_id,status='EXHAUSTED')
    result=asyncio.run(run_batch(catalog,manifest,bindings,ModelConfig(),tmp_path/'batch',run_one=run_one))
    assert seen[0]['delivery'].platform=='linux'
    assert seen[0]['delivery'].source=='swt-bench:'+('a'*40)+':unit_test'
    assert result['configuration']['delivery']['platform']=='linux'


def test_eval_delivery_reaches_product_request_and_wire(tmp_path,projects,monkeypatch):
    from reproagent.core.models import DeliveryRequirements
    from evals.run import run_case
    from tests.integration.test_eval_observability import eval_environment
    case,model,requests,controllers=eval_environment(tmp_path,projects,monkeypatch)
    requirement=DeliveryRequirements('linux','pinned-harness:offline')
    result=asyncio.run(run_case(case,model,tmp_path/'out',delivery=requirement))
    stored=controllers[0].store.load_record('request','request')
    assert stored.delivery==requirement
    exploration=[r for r in requests if r.get('tools')]
    assert exploration
    for request in exploration:
        # SDK history retains the original product task message.
        assert any('pinned-harness:offline' in m.get('content','') for m in request['messages']
                   if isinstance(m.get('content'),str))
    assert result.delivery_status=='target_unverified'


def test_product_report_distinguishes_local_reproduction_from_target_delivery(tmp_path, projects, facts):
    from reproagent.core.models import DeliveryRequirements
    request, model, controller, ctx = setup(tmp_path, projects, facts)
    target = 'linux' if sys.platform != 'linux' else 'win32'
    request = replace(request, delivery=DeliveryRequirements(target, 'pinned-harness:offline'))
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.DONE
    report_root = request.output_dir / 'artifacts/reproduction'
    report = json.loads((report_root/'report.json').read_text(encoding='utf-8'))
    assert report['delivery'] == {'platform': target, 'source': 'pinned-harness:offline'}
    assert report['delivery_status'] == 'target_unverified'
    assert report['execution_platforms'] == [sys.platform]
    text = (report_root/'report.md').read_text(encoding='utf-8')
    assert target in text and sys.platform in text
    assert '目标平台尚未验证' in text
