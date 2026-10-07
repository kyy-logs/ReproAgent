import asyncio
import subprocess
import sys
from dataclasses import replace

import pytest

from tests.unit.test_controller import PhasePlan, ScriptedModel, publish
from tests.unit.test_swt_prepare import binding_fixture,git


def completed(tmp_path,projects,monkeypatch,plan=None):
    import evals.run as runner
    from reproagent.app import create_controller
    from reproagent.core.models import ModelConfig
    from evals.swt_bench.prepare import validate_binding
    row,binding=binding_fixture(tmp_path,projects)
    case=validate_binding(row,binding)
    script=ScriptedModel()
    explorer=PhasePlan(plan or [publish()])
    monkeypatch.setattr(runner,'create_controller',
        lambda request,model,**kwargs:create_controller(request,model,gateway=script,explorer_factory=explorer,**kwargs))
    result=asyncio.run(runner.run_case(case,ModelConfig(),tmp_path/'out'))
    assert result.status=='DONE'
    return case,tmp_path/'out'/'task'


def test_frozen_candidate_exports_patch_that_fails_before_fix_and_passes_after(tmp_path,projects,monkeypatch):
    from evals.swt_bench.predictions import candidate_patch
    case,task=completed(tmp_path,projects,monkeypatch)
    patch=candidate_patch(task,case)
    assert 'diff --git a/tests/test_repro.py b/tests/test_repro.py' in patch
    for label,factory,expected in [('buggy',projects.plain,1),('fixed',projects.fixed,0)]:
        fresh=factory(tmp_path/('fresh-'+label)); git(fresh,'init')
        apply=subprocess.run(['git','-C',str(fresh),'apply','--whitespace=nowarn','-'],input=patch.encode(),capture_output=True)
        assert apply.returncode==0,apply.stderr
        result=subprocess.run([sys.executable,'-m','pytest','-q','tests/test_repro.py'],cwd=fresh,capture_output=True,
            env={**__import__('os').environ,'PYTHONPATH':str(fresh)},timeout=20)
        assert result.returncode==expected,result.stderr


def test_changed_candidate_or_base_cannot_be_exported(tmp_path,projects,monkeypatch):
    from evals.swt_bench.predictions import candidate_patch
    case,task=completed(tmp_path,projects,monkeypatch)
    with pytest.raises(ValueError): candidate_patch(task,replace(case,case_id='wrong'))
    file=task/'artifacts/reproduction/candidate/tests/test_repro.py'
    file.write_text('changed')
    with pytest.raises(ValueError,match='changed|hash'): candidate_patch(task,case)


def test_unified_patch_preserves_no_final_newline_and_empty_files(tmp_path):
    from evals.swt_bench.predictions import new_file_patch
    repo=tmp_path/'repo'; repo.mkdir(); git(repo,'init')
    patch=new_file_patch('tests/test_unicode.py','中文👋'.encode())+new_file_patch('tests/__init__.py',b'')
    result=subprocess.run(['git','-C',str(repo),'apply','-'],input=patch.encode(),capture_output=True)
    assert result.returncode==0,result.stderr
    assert (repo/'tests/test_unicode.py').read_bytes()=='中文👋'.encode()
    assert (repo/'tests/__init__.py').read_bytes()==b''
    with pytest.raises(ValueError): new_file_patch('../source.py',b'changed')


def test_failed_tasks_stay_in_prediction_file(tmp_path):
    from evals.swt_bench.predictions import write_predictions
    from evals.swt_bench.io import seal,loads
    manifest=seal({'cases':[{'instance_id':'one'},{'instance_id':'two'}]},'manifest_hash')
    receipt=write_predictions(manifest,{'one':{'status':'DONE','prediction_patch':'test patch'},'two':{'status':'BLOCKED'}},'reproagent',tmp_path/'predictions.jsonl')
    predictions=[loads(line) for line in (tmp_path/'predictions.jsonl').read_text().splitlines()]
    assert len(predictions)==2 and predictions[1]['model_patch']==''
    assert receipt['count']==2 and receipt['predictions_hash']


def test_frozen_candidate_data_file_is_included(tmp_path,projects,monkeypatch):
    from evals.swt_bench.predictions import candidate_patch
    case,task=completed(tmp_path,projects,monkeypatch,
        plan=[publish(extra=(('tests/input.txt','fixture data','data'),))])
    assert 'b/tests/input.txt' in candidate_patch(task,case)
