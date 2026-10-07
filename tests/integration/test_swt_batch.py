import asyncio
from dataclasses import asdict

from tests.unit.test_swt_prepare import binding_fixture
from tests.unit.test_swt_provenance import tool_root


def inputs(tmp_path,projects,count=2):
    from evals.swt_bench.io import seal
    row,binding=binding_fixture(tmp_path,projects)
    entries=[{**row,'instance_id':f'fixture__repo-{index}'} for index in range(1,count+1)]
    catalog=seal({'source':{'harness_commit':'a'*40},'entries':entries},'catalog_hash')
    manifest=seal({'catalog_hash':catalog['catalog_hash'],'source':catalog['source'],
        'cases':[{key:entry[key] for key in ('instance_id','repo','base_commit','description_hash')} for entry in entries]},'manifest_hash')
    return catalog,manifest,{entries[0]['instance_id']:binding}


def test_batch_preserves_missing_binding_and_failed_agent_as_predictions(tmp_path,projects):
    from evals.swt_bench.run import run_batch
    from evals.schema import EvalResult
    from evals.swt_bench.io import read_json
    from reproagent.core.models import BudgetLimits,ModelConfig
    catalog,manifest,bindings=inputs(tmp_path,projects); calls=[]
    async def run_one(case,model,output,**kwargs):
        calls.append(kwargs)
        return EvalResult(case.case_id,status='EXHAUSTED',http_attempts=3,usage={'total_tokens':41})
    result=asyncio.run(run_batch(catalog,manifest,bindings,ModelConfig(),tmp_path/'batch',limits=BudgetLimits(agent_steps=7),
        model_backend='native',agent_backend='agentscope',run_one=run_one))
    assert len(calls)==1 and calls[0]['limits'].agent_steps==7 and calls[0]['agent_backend']=='agentscope'
    assert len(result['outcomes'])==2 and result['outcomes']['fixture__repo-2']['status']=='NOT_PREPARED'
    assert read_json(tmp_path/'batch'/'summary.json')['all_tasks']==2
    assert len((tmp_path/'batch'/'predictions.jsonl').read_text().splitlines())==2


def test_cancelled_batch_never_starts_another_case(tmp_path,projects):
    from evals.swt_bench.run import run_batch
    from evals.schema import EvalResult
    from reproagent.core.models import ModelConfig
    catalog,manifest,bindings=inputs(tmp_path,projects)
    bindings['fixture__repo-2']={**bindings['fixture__repo-1'],'instance_id':'fixture__repo-2'}
    calls=[]
    async def run_one(case,model,output,**kwargs):
        calls.append(case.case_id); kwargs['cancel_event'].set()
        return EvalResult(case.case_id,status='CANCELLED')
    result=asyncio.run(run_batch(catalog,manifest,bindings,ModelConfig(),tmp_path/'batch',run_one=run_one))
    assert calls==['fixture__repo-1'] and all(outcome['status']=='CANCELLED' for outcome in result['outcomes'].values())


def test_cli_help_is_offline_and_product_has_no_dataset_dependency():
    import subprocess,sys
    result=subprocess.run([sys.executable,'-m','evals.swt_bench','--help'],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert 'fetch' in result.stdout and 'run' in result.stdout and 'import-reports' in result.stdout


def test_round_output_inside_target_is_rejected_before_writing(tmp_path,projects):
    import pytest
    from evals.swt_bench.run import run_batch
    from reproagent.core.models import ModelConfig
    catalog,manifest,bindings=inputs(tmp_path,projects)
    destination=tmp_path/'buggy'/'evaluation-output'
    with pytest.raises(ValueError): asyncio.run(run_batch(catalog,manifest,bindings,ModelConfig(),destination))
    assert not destination.exists()


def test_unchanged_tool_source_records_a_comparable_round(tmp_path,projects):
    from evals.schema import EvalResult
    from evals.swt_bench.io import read_json
    from evals.swt_bench.run import run_batch
    from reproagent.core.models import ModelConfig
    catalog,manifest,bindings=inputs(tmp_path,projects)
    async def run_one(case,model,output,**kwargs): return EvalResult(case.case_id,status='EXHAUSTED')
    result=asyncio.run(run_batch(catalog,manifest,bindings,ModelConfig(),tmp_path/'batch',run_one=run_one,
        tool_root=tool_root(tmp_path)))
    assert result['source_comparison_status']=='verified'
    assert result['outcomes']['fixture__repo-1']['status']=='EXHAUSTED'
    assert read_json(tmp_path/'batch'/'round.json')['source_comparison_status']=='verified'
    assert 'src/reproagent/prompts/explore.md' in result['tool_source']['files']


def test_source_change_invalidates_round_and_stops_later_model_calls(tmp_path,projects):
    from evals.schema import EvalResult
    from evals.swt_bench.io import read_json
    from evals.swt_bench.run import run_batch
    from reproagent.core.models import ModelConfig
    root=tool_root(tmp_path)
    catalog,manifest,bindings=inputs(tmp_path,projects,count=20)
    identities=[case['instance_id'] for case in manifest['cases']]
    first_binding=bindings[identities[0]]
    bindings={identity:{**first_binding,'instance_id':identity} for identity in identities}
    model_calls=[]
    async def run_one(case,model,output,**kwargs):
        model_calls.append(case.case_id)
        if len(model_calls)==1:
            (root/'src/reproagent/prompts/explore.md').write_text('parallel edit during the round\n',encoding='utf-8')
        return EvalResult(case.case_id,status='EXHAUSTED')
    result=asyncio.run(run_batch(catalog,manifest,bindings,ModelConfig(),tmp_path/'batch',run_one=run_one,tool_root=root))
    assert model_calls==[identities[0]]
    assert len(result['outcomes'])==20 and result['source_comparison_status']=='changed'
    assert all(outcome['status'] in ('EXHAUSTED','SOURCE_CHANGED') for outcome in result['outcomes'].values())
    assert result['outcomes'][identities[1]]['status']=='SOURCE_CHANGED'
    assert result['outcomes'][identities[1]]['stop_reason']=='TOOL_SOURCE_CHANGED'
    assert read_json(tmp_path/'batch'/'round.json')['source_comparison_status']=='changed'
    assert read_json(tmp_path/'batch'/'summary.json')['all_tasks']==20
    assert len((tmp_path/'batch'/'predictions.jsonl').read_text().splitlines())==20
