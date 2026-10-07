"""A reference control must prove the target tests ran, not merely that pytest exited.

The recorded round called five cases discriminating from exit codes alone.  A
collection error, a fixture that raises during setup, or a skipped node all give
the same "original failed, fixed passed" shape without the target test ever
reaching its call phase.  These tests pin the node-level rule that replaces it,
using synthetic projects whose original and fixed sources are stated explicitly.
"""
import json
import subprocess
import sys

import pytest

from reproagent.core.serialization import bytes_hash, canonical_hash
from reproagent.workspace import inventory

TARGET='tests/test_reference.py::test_empty_input_is_empty'
SECOND_TARGET='tests/test_reference.py::test_empty_input_is_the_first_item'
BASE_FILES={'example/__init__.py':'',
    'example/parser.py':'def parse(values):\n    return [values[0]]\n',
    'tests/test_existing.py':'def test_existing():\n    assert True\n'}
FIXED_PARSER='def parse(values):\n    return [values[0]] if values else []\n'
#: Replaces the second line of example/parser.py, optionally appending one marker.
FIX_PATCH='''diff --git a/example/parser.py b/example/parser.py
--- a/example/parser.py
+++ b/example/parser.py
@@ -1,2 +1,2 @@
 def parse(values):
-    return [values[0]]
+    return [values[0]] if values else []
'''
MARKED_FIX='''diff --git a/example/parser.py b/example/parser.py
--- a/example/parser.py
+++ b/example/parser.py
@@ -1,2 +1,3 @@
 def parse(values):
-    return [values[0]]
+    return [values[0]] if values else []
+%s
'''
SKIP_FIX=MARKED_FIX % 'SKIP_ON_FIXED = True'
MARK_FIX=MARKED_FIX % 'FIXED = True'
NEW_REFERENCE_TESTS='''diff --git a/tests/test_reference.py b/tests/test_reference.py
new file mode 100644
--- /dev/null
+++ b/tests/test_reference.py
@@ -0,0 +1,5 @@
+from example.parser import parse
+
+
+def test_empty_input_is_empty():
+    assert parse([]) == []
'''


def git(repo,*args):
    result=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stderr
    return result.stdout.strip()


def make_repo(root,files):
    for name,content in files.items():
        path=root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(content,encoding='utf-8')
    git(root,'init')
    git(root,'add','.')
    git(root,'-c','user.name=Fixture','-c','user.email=fixture@example.org','commit','-m','base')
    return root


def new_test_file(body):
    """A reference test patch that adds ``body`` as tests/test_reference.py."""
    lines=body.rstrip('\n').split('\n')
    return ('diff --git a/tests/test_reference.py b/tests/test_reference.py\nnew file mode 100644\n'
        '--- /dev/null\n+++ b/tests/test_reference.py\n'
        f'@@ -0,0 +1,{len(lines)} @@\n'
        +''.join('+'+line+'\n' for line in lines))


def control_inputs(tmp_path,*,fix_patch,test_patch,targets,fixed_files):
    """A row (gold material) and a binding (reviewed repositories) for one control."""
    buggy=make_repo(tmp_path/'buggy',BASE_FILES)
    fixed=make_repo(tmp_path/'fixed',fixed_files)
    row={'instance_id':'fixture__repo-1','repo':'fixture/repo','base_commit':git(buggy,'rev-parse','HEAD'),
        'description_hash':bytes_hash(b'Original issue'),'problem_statement':'Original issue',
        'patch':fix_patch,'test_patch':test_patch,'FAIL_TO_PASS':json.dumps(list(targets)),
        'fix_patch_hash':bytes_hash(fix_patch.encode('utf-8'))}
    binding={'instance_id':row['instance_id'],'buggy_repo':str(buggy),'fixed_repo':str(fixed),
        'buggy_python':sys.executable,'fixed_python':sys.executable,'target_modules':['example.parser'],
        'source_roots':['.'],'candidate_parent':'tests','pytest_args':['-q'],'fix_patch_hash':row['fix_patch_hash'],
        'fixed_source_hash':canonical_hash({path:bytes_hash(data) for path,data in inventory(fixed).items()}),
        'review_status':'approved','review_actor':'fixture reviewer'}
    return row,binding


def run_control(root,row,binding):
    from evals.swt_bench.control import run_reference_control
    return run_reference_control(row,binding,root/'controls')


def test_success_requires_buggy_call_failure_and_all_fixed_calls_pass(tmp_path):
    fixed_files={**BASE_FILES,'example/parser.py':FIXED_PARSER}
    # A failing call on the original copy and a passing call on the fixed copy.
    row,binding=control_inputs(tmp_path,fix_patch=FIX_PATCH,test_patch=NEW_REFERENCE_TESTS,
        targets=[TARGET],fixed_files=fixed_files)
    receipt=run_control(tmp_path,row,binding)
    assert receipt['status']=='discriminating' and receipt['discriminating'] is True,receipt['reason']
    assert receipt['target_nodes']==[TARGET]
    assert receipt['roles']['buggy']['targets'][TARGET]['call']=='failed'
    assert receipt['roles']['fixed']['targets'][TARGET]['call']=='passed'
    assert receipt['roles']['buggy']['exit_code']!=0 and receipt['roles']['fixed']['exit_code']==0

    # An original copy that passes the target test is not a control, whatever the
    # fixed copy does: nothing distinguishes the two versions.
    passing=tmp_path/'passing'
    row,binding=control_inputs(passing,fix_patch=FIX_PATCH,
        test_patch=new_test_file('def test_unrelated_assertion():\n    assert True'),
        targets=['tests/test_reference.py::test_unrelated_assertion'],fixed_files=fixed_files)
    receipt=run_control(passing,row,binding)
    assert receipt['status']=='not_discriminating' and 'no target test call failed' in receipt['reason'],receipt
    assert receipt['roles']['buggy']['exit_code']==0 and receipt['roles']['fixed']['exit_code']==0

    # One failing target call on the fixed copy is enough to disqualify the control.
    both=tmp_path/'both'
    row,binding=control_inputs(both,fix_patch=FIX_PATCH,
        test_patch=new_test_file('from example.parser import parse\n\n\ndef test_empty_input_is_empty():\n'
            '    assert parse([]) == []\n\n\ndef test_empty_input_is_the_first_item():\n'
            '    assert parse([]) == [1]'),
        targets=[TARGET,SECOND_TARGET],fixed_files=fixed_files)
    receipt=run_control(both,row,binding)
    assert receipt['status']=='not_discriminating',receipt
    assert receipt['reason']=='the fixed version did not pass every target test call'
    assert receipt['roles']['fixed']['targets'][TARGET]['call']=='passed'
    assert receipt['roles']['fixed']['targets'][SECOND_TARGET]['call']=='failed'


def test_collection_error_is_not_a_discriminating_control(tmp_path):
    fix_patch='''diff --git a/example/feature.py b/example/feature.py
new file mode 100644
--- /dev/null
+++ b/example/feature.py
@@ -0,0 +1 @@
+VALUE = 1
diff --git a/example/parser.py b/example/parser.py
--- a/example/parser.py
+++ b/example/parser.py
@@ -1,2 +1,2 @@
 def parse(values):
-    return [values[0]]
+    return [values[0]] if values else []
'''
    test_patch=new_test_file('from example.feature import VALUE\n\n\ndef test_empty_input_is_empty():\n'
        '    assert VALUE == 1')
    fixed_files={**BASE_FILES,'example/parser.py':FIXED_PARSER,'example/feature.py':'VALUE = 1\n'}
    row,binding=control_inputs(tmp_path,fix_patch=fix_patch,test_patch=test_patch,targets=[TARGET],fixed_files=fixed_files)
    receipt=run_control(tmp_path,row,binding)
    # Exit codes alone look like a control: the original copy exits non-zero because it
    # cannot import the module at all, and the fixed copy exits zero.
    assert receipt['roles']['buggy']['exit_code']!=0 and receipt['roles']['fixed']['exit_code']==0
    assert receipt['status']=='not_discriminating' and receipt['discriminating'] is False,receipt
    assert receipt['roles']['buggy']['collection_errors'] and receipt['roles']['buggy']['uncollected']==[TARGET]
    assert receipt['roles']['buggy']['targets'][TARGET].get('call') is None
    assert receipt['roles']['fixed']['targets'][TARGET]['call']=='passed'
    assert 'collection error' in receipt['reason']
    for role in ('buggy','fixed'):
        assert (tmp_path/'controls'/f'{role}.stdout.log').is_file()
        assert (tmp_path/'controls'/f'{role}.stderr.log').is_file()


@pytest.mark.parametrize('shape',['setup_failure','skipped'])
def test_skipped_or_setup_failed_nodes_do_not_qualify(tmp_path,shape):
    if shape=='setup_failure':
        fix_patch=MARK_FIX; fixed_parser=FIXED_PARSER+'FIXED = True\n'
        test_patch=new_test_file('import pytest\n\nfrom example import parser\n\n\n@pytest.fixture\n'
            'def value():\n    if not getattr(parser, \'FIXED\', False):\n'
            '        raise RuntimeError(\'the fix is not present\')\n    return 1\n\n\n'
            'def test_empty_input_is_empty(value):\n    assert value == 1')
    else:
        fix_patch=SKIP_FIX; fixed_parser=FIXED_PARSER+'SKIP_ON_FIXED = True\n'
        test_patch=new_test_file('import pytest\n\nfrom example import parser\n\n\n'
            'def test_empty_input_is_empty():\n    if getattr(parser, \'SKIP_ON_FIXED\', False):\n'
            '        pytest.skip(\'skipped on the fixed version\')\n    assert parser.parse([]) == []')
    fixed_files={**BASE_FILES,'example/parser.py':fixed_parser}
    row,binding=control_inputs(tmp_path,fix_patch=fix_patch,test_patch=test_patch,targets=[TARGET],fixed_files=fixed_files)
    receipt=run_control(tmp_path,row,binding)
    # The target node is collected and reported, so only its phases show that the
    # original copy never reached a failing call.
    assert receipt['roles']['buggy']['exit_code']!=0 and receipt['roles']['fixed']['exit_code']==0,receipt.get('reason')
    assert receipt['status']=='not_discriminating' and receipt['discriminating'] is False,receipt
    if shape=='setup_failure':
        assert receipt['roles']['buggy']['targets'][TARGET]['setup']=='failed'
        assert receipt['roles']['buggy']['targets'][TARGET].get('call') is None
        assert 'setup/teardown error' in receipt['reason']
    else:
        assert receipt['roles']['fixed']['targets'][TARGET]['call']=='skipped'
        assert 'skipped test' in receipt['reason']


def test_patch_that_changes_no_target_file_is_a_patch_error(tmp_path):
    # git apply exits 0 for a hunk that rewrites a line with itself, and the first
    # recorded control script was fooled by an application that changed nothing.
    no_op_reference_tests='''diff --git a/tests/test_existing.py b/tests/test_existing.py
--- a/tests/test_existing.py
+++ b/tests/test_existing.py
@@ -1,2 +1,2 @@
 def test_existing():
-    assert True
+    assert True
'''
    no_op_fix='''diff --git a/example/parser.py b/example/parser.py
--- a/example/parser.py
+++ b/example/parser.py
@@ -1,2 +1,2 @@
 def parse(values):
-    return [values[0]]
+    return [values[0]]
'''
    fixed_files={**BASE_FILES,'example/parser.py':FIXED_PARSER}
    for label,fix_patch,test_patch in [('reference-tests',FIX_PATCH,no_op_reference_tests),('fix',no_op_fix,FIX_PATCH)]:
        root=tmp_path/label
        row,binding=control_inputs(root,fix_patch=fix_patch,test_patch=test_patch,targets=[TARGET],fixed_files=fixed_files)
        receipt=run_control(root,row,binding)
        assert receipt['status']=='patch_error' and receipt['discriminating'] is False,receipt
        assert 'without changing' in receipt['reason'],receipt['reason']
        assert 'roles' not in receipt


def test_control_never_mutates_generation_repositories(tmp_path):
    from evals.run import generation_input
    from evals.swt_bench.prepare import validate_binding
    fixed_files={**BASE_FILES,'example/parser.py':FIXED_PARSER}
    row,binding=control_inputs(tmp_path,fix_patch=FIX_PATCH,test_patch=NEW_REFERENCE_TESTS,
        targets=[TARGET],fixed_files=fixed_files)
    before={name:{path:bytes_hash(data) for path,data in inventory(tmp_path/name).items()} for name in ('buggy','fixed')}
    receipt=run_control(tmp_path,row,binding)
    assert receipt['status']=='discriminating'
    for name in ('buggy','fixed'):
        assert {path:bytes_hash(data) for path,data in inventory(tmp_path/name).items()}==before[name]
    assert not (tmp_path/'buggy'/'tests'/'test_reference.py').exists()
    assert (tmp_path/'controls'/'buggy'/'tests'/'test_reference.py').is_file()
    # The gold material is read on the evaluation side only: the generation input built
    # from the same reviewed binding carries none of it, and never names the fixed copy.
    case=validate_binding(row,binding)
    published=json.dumps(generation_input(case))
    assert 'test_reference' not in published and 'FAIL_TO_PASS' not in published
    assert row['test_patch'] not in published and row['patch'] not in published
    assert str(case.fixed_repo) not in published


@pytest.mark.parametrize('failure',['timeout','unreadable-receipt'])
def test_runner_failure_is_not_reported_as_an_uncollected_node(tmp_path,monkeypatch,failure):
    """A run that reported nothing measured nothing, and the receipt must say so."""
    from evals.swt_bench import control
    fixed_files={**BASE_FILES,'example/parser.py':FIXED_PARSER}
    row,binding=control_inputs(tmp_path,fix_patch=FIX_PATCH,test_patch=NEW_REFERENCE_TESTS,
        targets=[TARGET],fixed_files=fixed_files)
    if failure=='timeout':
        monkeypatch.setattr(control,'RUN_TIMEOUT',0.001)
    else:
        # A real subprocess that exits without writing the run receipt.
        monkeypatch.setattr(control,'RUNNER','import sys\nsys.exit(0)\n')
    receipt=run_control(tmp_path,row,binding)
    assert receipt['status']=='not_discriminating' and receipt['discriminating'] is False
    assert 'control run reported nothing' in receipt['reason'],receipt['reason']
    assert 'never collected' not in receipt['reason'],receipt['reason']
    for role in ('buggy','fixed'):
        assert receipt['roles'][role]['runner_failed']
        assert receipt['roles'][role]['uncollected']==[]
        assert receipt['roles'][role]['exit_code']==(None if failure=='timeout' else 0)


def test_control_cli_records_every_selected_case(tmp_path):
    from evals.swt_bench.io import read_json,seal,write_json
    fixed_files={**BASE_FILES,'example/parser.py':FIXED_PARSER}
    row,binding=control_inputs(tmp_path,fix_patch=FIX_PATCH,test_patch=NEW_REFERENCE_TESTS,
        targets=[TARGET],fixed_files=fixed_files)
    second={**row,'instance_id':'fixture__repo-2'}
    entries=[row,second]
    manifest=seal({'source':{'harness_commit':'a'*40},'cases':[{key:entry[key] for key in
        ('instance_id','repo','base_commit','description_hash')} for entry in entries]},'manifest_hash')
    data=tmp_path/'data'; data.mkdir()
    write_json(data/'snapshot.json',entries); write_json(data/'manifest.json',manifest)
    write_json(data/'bindings.json',{row['instance_id']:binding})
    result=subprocess.run([sys.executable,'-m','evals.swt_bench','control','--snapshot',str(data/'snapshot.json'),
        '--manifest',str(data/'manifest.json'),'--bindings',str(data/'bindings.json'),
        '--output',str(tmp_path/'controls')],capture_output=True,text=True,timeout=300)
    assert result.returncode==0,result.stderr
    # The bound case is controlled; the unbound one stays in the denominator.
    assert json.loads(result.stdout)=={'all_cases':2,'controlled':1,'counts':{'discriminating':1,'not_run':1}}
    verification=read_json(tmp_path/'controls'/'verification.json')
    assert verification['all_cases']==2 and verification['cases']['fixture__repo-2']['status']=='not_run'


def test_control_subprocess_environment_excludes_secrets(tmp_path,monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','sk-do-not-leak')
    monkeypatch.setenv('REPROAGENT_SWT_SNAPSHOT','gold-cache')
    fixed_files={**BASE_FILES,'example/parser.py':FIXED_PARSER}
    row,binding=control_inputs(tmp_path,fix_patch=FIX_PATCH,
        test_patch=new_test_file('import os\n\nfrom example.parser import parse\n\n\n'
            'def test_empty_input_is_empty():\n'
            '    assert \'DEEPSEEK_API_KEY\' not in os.environ and \'REPROAGENT_SWT_SNAPSHOT\' not in os.environ\n'
            '    assert parse([]) == []'),targets=[TARGET],fixed_files=fixed_files)
    receipt=run_control(tmp_path,row,binding)
    assert receipt['status']=='discriminating',receipt
    assert receipt['roles']['fixed']['targets'][TARGET]['call']=='passed'
