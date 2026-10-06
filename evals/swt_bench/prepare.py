"""Read-only prepared environment validation, isolated from generation."""
import json
import os
import re
import subprocess
import time
from pathlib import Path

from reproagent.adapters.languages.python_pytest.adapter import ALLOWED_ARGS,absolute_python
from reproagent.core.serialization import bytes_hash,canonical_hash
from reproagent.store import safe_child
from reproagent.workspace import inventory,EXCLUDED_NAMES
from ..schema import EvalCase


def source_hash(repo):
    return canonical_hash({path:bytes_hash(data) for path,data in inventory(Path(repo)).items()})


def git_output(repo,*args):
    process=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,encoding='utf-8',timeout=30)
    if process.returncode: raise ValueError('repository version check failed')
    return process.stdout.strip()


def overlap(first,second):
    first,second=Path(first).resolve(),Path(second).resolve()
    return first.is_relative_to(second) or second.is_relative_to(first)


def validate_binding(row,binding,protected_roots=()):
    if binding.get('instance_id')!=row['instance_id']: raise ValueError('binding instance mismatch')
    buggy=Path(binding['buggy_repo']).resolve(); fixed=Path(binding['fixed_repo']).resolve()
    if not buggy.is_dir() or not fixed.is_dir() or overlap(buggy,fixed): raise ValueError('missing or overlapping repositories')
    if any(overlap(repo,root) for repo in (buggy,fixed) for root in protected_roots):
        raise ValueError('repository overlaps hidden materials or output')
    if git_output(buggy,'rev-parse','HEAD')!=row['base_commit']: raise ValueError('buggy HEAD mismatch')
    if git_output(buggy,'diff','HEAD','--'): raise ValueError('buggy tracked source changed')
    tracked=set(git_output(buggy,'ls-files','-z').split('\0'))-{''}
    if set(inventory(buggy))-tracked: raise ValueError('untracked or ignored input in buggy repository')
    if binding.get('fix_patch_hash')!=row['fix_patch_hash']: raise ValueError('fixed patch hash mismatch')
    actual_fixed=source_hash(fixed)
    if binding.get('fixed_source_hash')!=actual_fixed: raise ValueError('fixed source hash mismatch')
    modules=binding.get('target_modules',[]); roots=binding.get('source_roots',['.'])
    parent=binding.get('candidate_parent','tests'); args=binding.get('pytest_args',['-q'])
    baseline=binding.get('baseline_tests',[])
    if not isinstance(modules,list) or not modules or any(type(module) is not str or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',module) for module in modules):
        raise ValueError('explicit target modules required')
    if not isinstance(roots,list) or not roots or not isinstance(args,list) or any(arg not in ALLOWED_ARGS for arg in args):
        raise ValueError('invalid roots or pytest arguments')
    for repo in (buggy,fixed):
        if not safe_child(repo,parent).is_dir(): raise ValueError('candidate directory missing')
        for root in roots:
            if root!='.' and not safe_child(repo,root).is_dir(): raise ValueError('source root missing')
        for selector in baseline:
            if not safe_child(repo,selector.split('::')[0]).is_file(): raise ValueError('baseline test missing')
    if binding.get('review_status','pending')=='approved' and not binding.get('review_actor'):
        raise ValueError('approved binding requires technical review actor')
    return EvalCase(row['instance_id'],'https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite',row['description_hash'],
        buggy,row['base_commit'],fixed,'patch:'+row['fix_patch_hash'],absolute_python(binding['buggy_python']),
        absolute_python(binding['fixed_python']),row['problem_statement'],review_status=binding.get('review_status','pending'),
        target_modules=tuple(modules),source_roots=tuple(roots),candidate_parent=parent,pytest_args=tuple(args),baseline_tests=tuple(baseline),
        buggy_source_hash=source_hash(buggy),fixed_source_hash=actual_fixed)


def preflight(case):
    started=time.monotonic(); versions={}
    receipt={'status':'pending','reason':'','python_versions':versions,'buggy_source_hash':case.buggy_source_hash,
        'fixed_source_hash':case.fixed_source_hash}
    try:
        for role,repo,python,expected in [('buggy',case.buggy_repo,case.buggy_python,case.buggy_source_hash),
                                        ('fixed',case.fixed_repo,case.fixed_python,case.fixed_source_hash)]:
            if source_hash(repo)!=expected: raise ValueError('source changed before preflight')
            environment={key:value for key,value in os.environ.items() if key.upper() in ('SYSTEMROOT','WINDIR','PATH','PATHEXT','TEMP','TMP','HOME','USERPROFILE','LANG')}
            environment.update(PYTHONIOENCODING='utf-8',PYTHONPATH=os.pathsep.join(str(repo if root=='.' else safe_child(repo,root)) for root in case.source_roots),
                PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTEST_ADDOPTS='')
            code=('import importlib,json,sys,pytest; from pathlib import Path; '
                'origins={name:str(Path(importlib.import_module(name).__file__).resolve()) for name in json.loads(sys.argv[1])}; '
                'assert all(Path(path).is_relative_to(Path.cwd()) for path in origins.values()); '
                'print(json.dumps(dict(python_version=sys.version.split()[0],pytest_version=pytest.__version__,target_origins=origins)))')
            process=subprocess.run([python,'-c',code,json.dumps(case.target_modules)],cwd=repo,capture_output=True,timeout=20,env=environment)
            if process.returncode: raise OSError('target unavailable')
            versions[role]=json.loads(process.stdout.decode('utf-8'))
            if source_hash(repo)!=expected: raise ValueError('source changed during preflight')
        receipt['status']='ready'
    except (OSError,subprocess.TimeoutExpired,json.JSONDecodeError):
        receipt.update(status='blocked',reason='target Python/pytest unavailable')
    except ValueError:
        receipt.update(status='preparation_error',reason='source changed during preflight')
    receipt['duration']=time.monotonic()-started
    return receipt


def inspect_bindings(catalog,manifest,bindings,protected_roots=()):
    from .io import verify_seal
    verify_seal(catalog,'catalog_hash'); verify_seal(manifest,'manifest_hash')
    if manifest['catalog_hash']!=catalog['catalog_hash'] or manifest['source']!=catalog['source']:
        raise ValueError('manifest/catalog mismatch')
    rows={row['instance_id']:row for row in catalog['entries']}
    identities=[item['instance_id'] for item in manifest['cases']]
    if len(set(identities))!=len(identities) or set(bindings)-set(identities): raise ValueError('invalid binding IDs')
    receipts={}
    for item in manifest['cases']:
        identity=item['instance_id']
        if identity not in rows or any(rows[identity][key]!=item[key] for key in ('repo','base_commit','description_hash')):
            raise ValueError('selected source identity mismatch')
        if identity not in bindings:
            receipts[identity]={'status':'pending','reason':'environment binding missing'}; continue
        try:
            case=validate_binding(rows[identity],bindings[identity],protected_roots)
            receipts[identity]=preflight(case)
            if receipts[identity]['status']=='ready' and case.review_status!='approved':
                receipts[identity].update(status='pending',reason='technical review pending')
        except (ValueError,OSError,KeyError,TypeError,subprocess.SubprocessError):
            receipts[identity]={'status':'preparation_error','reason':'invalid or changed environment binding'}
    return receipts
