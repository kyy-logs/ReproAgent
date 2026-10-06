import json
import subprocess
import sys
from dataclasses import asdict

import pytest


def git(repo,*args):
    result=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert result.returncode==0,result.stderr
    return result.stdout.strip()


def binding_fixture(tmp_path,projects):
    from reproagent.workspace import inventory
    from reproagent.core.serialization import bytes_hash,canonical_hash
    repo=projects.plain(tmp_path/'buggy'); fixed=projects.fixed(tmp_path/'fixed')
    git(repo,'init'); git(repo,'add','.'); git(repo,'-c','user.name=Fixture','-c','user.email=fixture@example.org','commit','-m','base')
    commit=git(repo,'rev-parse','HEAD')
    row={'instance_id':'fixture__repo-1','repo':'fixture/repo','base_commit':commit,'description_hash':bytes_hash(b'Original issue'),
        'problem_statement':'Original issue','fix_patch_hash':'c'*64}
    binding={'instance_id':row['instance_id'],'buggy_repo':str(repo),'fixed_repo':str(fixed),'buggy_python':sys.executable,
        'fixed_python':sys.executable,'target_modules':['example.parser'],'source_roots':['.'],'candidate_parent':'tests',
        'pytest_args':['-q'],'fix_patch_hash':row['fix_patch_hash'],
        'fixed_source_hash':canonical_hash({path:bytes_hash(data) for path,data in inventory(fixed).items()}),
        'review_status':'approved','review_actor':'fixture reviewer'}
    return row,binding


def test_binding_isolated_and_preflight_receipt_records_actual_environment(tmp_path,projects):
    from evals.swt_bench.prepare import validate_binding,preflight
    row,binding=binding_fixture(tmp_path,projects)
    case=validate_binding(row,binding,protected_roots=(tmp_path/'gold-cache',tmp_path/'output'))
    receipt=preflight(case)
    assert receipt['status']=='ready' and receipt['python_versions']['buggy']['pytest_version']
    assert receipt['buggy_source_hash'] and receipt['fixed_source_hash']
    assert case.allowed_description=='Original issue' and case.candidate_parent=='tests'
    assert 'fixed_repo' not in __import__('evals.run',fromlist=['generation_input']).generation_input(case)


@pytest.mark.parametrize('failure',['overlap','cache_inside','head','dirty','fix_hash','source_hash','traversal'])
def test_invalid_prepared_binding_cannot_enter_generation(tmp_path,projects,failure):
    from evals.swt_bench.prepare import validate_binding
    row,binding=binding_fixture(tmp_path,projects)
    protected=(tmp_path/'gold-cache',)
    if failure=='overlap': binding['fixed_repo']=binding['buggy_repo']
    if failure=='cache_inside': protected=(tmp_path/'buggy'/'gold-cache',)
    if failure=='head': row['base_commit']='f'*40
    if failure=='dirty': (tmp_path/'buggy'/'example'/'parser.py').write_text('changed')
    if failure=='fix_hash': binding['fix_patch_hash']='wrong'
    if failure=='source_hash': binding['fixed_source_hash']='wrong'
    if failure=='traversal': binding['candidate_parent']='../outside'
    with pytest.raises(ValueError): validate_binding(row,binding,protected_roots=protected)


def test_unavailable_interpreter_is_blocked_not_ready(tmp_path,projects):
    from evals.swt_bench.prepare import validate_binding,preflight
    row,binding=binding_fixture(tmp_path,projects)
    binding['buggy_python']=str(tmp_path/'missing-python.exe')
    receipt=preflight(validate_binding(row,binding))
    assert receipt['status']=='blocked' and receipt['reason']=='target Python/pytest unavailable'


def test_missing_target_module_is_not_treated_as_ready(tmp_path,projects):
    from evals.swt_bench.prepare import validate_binding,preflight
    row,binding=binding_fixture(tmp_path,projects)
    binding['target_modules']=['example.missing']
    assert preflight(validate_binding(row,binding))['status']=='blocked'


def test_ignored_oracle_file_cannot_enter_buggy_snapshot(tmp_path,projects):
    from evals.swt_bench.prepare import validate_binding
    row,binding=binding_fixture(tmp_path,projects)
    repo=tmp_path/'buggy'
    (repo/'.git/info/exclude').write_text('gold.txt\n',encoding='utf-8')
    (repo/'gold.txt').write_text('hidden regression oracle',encoding='utf-8')
    with pytest.raises(ValueError,match='untracked'): validate_binding(row,binding)


def test_tracked_unicode_paths_are_not_mistaken_for_ignored_files(tmp_path,projects):
    from evals.swt_bench.prepare import validate_binding
    row,binding=binding_fixture(tmp_path,projects)
    repo=tmp_path/'buggy'; (repo/'tracked_ä_中文.txt').write_text('public tracked data',encoding='utf-8')
    git(repo,'add','.'); git(repo,'-c','user.name=Fixture','-c','user.email=fixture@example.org','commit','-m','unicode')
    row['base_commit']=git(repo,'rev-parse','HEAD')
    assert validate_binding(row,binding).buggy_source_hash


def test_run_case_passes_backend_budget_and_candidate_location(monkeypatch,tmp_path,projects):
    import asyncio
    import evals.run as module
    from evals.schema import EvalCase
    from reproagent.core.models import BudgetLimits,ModelConfig,TaskResult,TaskState
    captured={}
    class Store:
        def read_events(self): return ([],[])
    class Controller:
        store=Store()
        async def run(self,request,context,fixed):
            captured['request']=request
            return TaskResult('id',TaskState.BLOCKED)
    def factory(request,model,**kwargs): captured.update(kwargs); return Controller()
    monkeypatch.setattr(module,'create_controller',factory)
    repo=projects.plain(tmp_path/'buggy'); fixed=projects.fixed(tmp_path/'fixed')
    case=EvalCase('id','https://example.org','hash',repo,'old',fixed,'new',sys.executable,sys.executable,'issue',
        review_status='approved',target_modules=('example.parser',),candidate_parent='tests/unit',pytest_args=('-vv',))
    limits=BudgetLimits(agent_steps=7,task_timeout_seconds=37)
    result=asyncio.run(module.run_case(case,ModelConfig(),tmp_path/'output',limits=limits,model_backend='agentscope',agent_backend='agentscope'))
    assert captured['model_backend']==captured['agent_backend']=='agentscope'
    assert captured['request'].limits==limits and captured['request'].language.candidate_parent=='tests/unit'
    assert captured['request'].language.pytest_args==('-vv',) and result.environment_status=='blocked'


def test_source_changes_at_controller_creation_never_reach_model(monkeypatch,tmp_path,projects):
    import asyncio
    import evals.run as module
    from reproagent.app import create_controller
    from reproagent.core.models import ModelConfig
    from evals.swt_bench.prepare import validate_binding
    from tests.unit.test_controller import ScriptedModel
    row,binding=binding_fixture(tmp_path,projects); case=validate_binding(row,binding); model=ScriptedModel()
    def factory(request,configuration,**kwargs):
        controller=create_controller(request,configuration,gateway=model,**kwargs)
        (case.buggy_repo/'gold.txt').write_text('hidden material added after preflight')
        return controller
    monkeypatch.setattr(module,'create_controller',factory)
    result=asyncio.run(module.run_case(case,ModelConfig(),tmp_path/'output'))
    assert result.status=='BLOCKED' and not model.messages


def test_http_attempt_usage_is_not_double_counted_or_unknown_cost_zeroed(monkeypatch,tmp_path,projects):
    import asyncio
    import evals.run as module
    from types import SimpleNamespace
    from evals.schema import EvalCase
    from reproagent.core.models import ModelConfig,TaskResult,TaskState
    class Store:
        def read_events(self): return ([
            SimpleNamespace(kind='model.attempt',payload={'usage':{'total_tokens':10},'cost_value':None}),
            SimpleNamespace(kind='model.attempt',payload={'usage':{'total_tokens':20},'cost_value':.1}),
            SimpleNamespace(kind='model.completed',payload={'usage':{'total_tokens':20},'cost_value':None})],[])
    class Controller:
        store=Store()
        async def run(self,*args): return TaskResult('id',TaskState.BLOCKED)
    monkeypatch.setattr(module,'create_controller',lambda *args,**kwargs:Controller())
    case=EvalCase('id','url','hash',tmp_path/'buggy','old',tmp_path/'fixed','new','python','python','issue',review_status='approved')
    result=asyncio.run(module.run_case(case,ModelConfig(),tmp_path/'output'))
    assert result.http_attempts==2 and result.usage['total_tokens']==30
    assert result.cost_kind=='unknown' and result.cost_value is None and result.unknown_cost_attempts==1
    assert result.known_cost_subtotal==.1
