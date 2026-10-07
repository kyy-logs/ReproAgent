"""Reference controls that prove the target tests really executed.

A reference control is the dataset's own fix patch plus its own new tests, applied
to isolated copies of the reviewed repositories: the original copy should fail the
target tests and the fixed copy should pass them.  The recorded round called five
cases discriminating from pytest exit codes alone, but a collection error, a
fixture that raises during setup and a skipped node all produce the same
"non-zero then zero" shape without the target test ever reaching its call phase.

This module re-runs each control and decides it per target node: the original copy
must record a *failing call* and the fixed copy a *passing call* for every target
node, with no collection, setup or teardown error and no skip.  Patch application
is checked by content, so a patch that exits 0 while leaving every target file
byte-identical is a patch error rather than a passing control.

The gold material (``row``) is read here and nowhere else: nothing derived from it
reaches ``evals.run.run_case``, which is only ever given the public issue text.
"""
import json
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

from reproagent.adapters.languages.python_pytest.adapter import absolute_python
from reproagent.core.serialization import bytes_hash
from reproagent.store import atomic_write,safe_child
from .io import file_hash,fresh_dir,loads,read_json,verify_seal,write_json
from .prepare import isolated_environment,load_bindings,overlap,source_hash

#: Fixed arguments: a control compares two copies of one project, so an outer
#: addopts or a cache left by a previous run must not change what is collected.
PYTEST_ARGS=('-q','--tb=short','-p','no:cacheprovider')
#: A copy is a workspace, not a checkout: version control and caches are rebuilt.
COPY_IGNORES=('.git','__pycache__','.pytest_cache','.venv','venv')
PATCH_TIMEOUT=60
RUN_TIMEOUT=900

#: Runs inside the target interpreter, records every node phase, and writes the run
#: receipt to a file the caller names (stdout stays a human-readable log).
RUNNER=r'''
import json
import sys

import pytest


def main():
    config = json.loads(sys.argv[1])
    nodes = {}
    collection_errors = []

    class Recorder:
        def pytest_runtest_logreport(self, report):
            nodes.setdefault(report.nodeid, {})[report.when] = report.outcome

        def pytest_collectreport(self, report):
            if report.outcome != 'passed':
                collection_errors.append(report.nodeid)

    code = int(pytest.main(config['selectors'] + config['pytest_args'], plugins=[Recorder()]))
    import hashlib
    import importlib.metadata as metadata

    distributions = sorted({(item.metadata['Name'] or '', item.version or '') for item in metadata.distributions()})
    receipt = {'exit_code': code, 'nodes': nodes, 'collection_errors': collection_errors,
        'python_version': sys.version.split()[0], 'pytest_version': pytest.__version__,
        'dependency_hash': hashlib.sha256(repr(distributions).encode('utf-8')).hexdigest()}
    with open(config['receipt'], 'w', encoding='utf-8') as handle:
        handle.write(json.dumps(receipt))
    return 0


sys.exit(main())
'''


def reference_targets(row):
    """The target test nodes of a control, as the dataset records them."""
    value=row.get('FAIL_TO_PASS')
    if isinstance(value,str):
        try: value=json.loads(value)
        except ValueError: raise ValueError('unreadable target test nodes')
    if not isinstance(value,list) or not value: raise ValueError('reference control requires target test nodes')
    nodes=[]
    for node in value:
        # A node is passed to pytest as one argument, so it only has to be a single
        # identifier-like string; its layout differs per repository.
        if type(node) is not str or node!=node.strip() or not node or set(node)&set('\r\n'):
            raise ValueError('invalid target test node')
        nodes.append(node)
    return nodes


def patch_targets(text):
    """The repository-relative paths a unified diff names, added and deleted alike."""
    found=set()
    for line in text.splitlines():
        for prefix in ('--- a/','+++ b/'):
            if line.startswith(prefix): found.add(line[len(prefix):].strip())
    return sorted(path for path in found if path)


def _file_state(root,paths):
    state={}
    for relative in paths:
        path=safe_child(root,relative)
        state[relative]=bytes_hash(path.read_bytes()) if path.is_file() else None
    return state


def _fresh_copy(source,destination):
    shutil.copytree(source,destination,ignore=shutil.ignore_patterns(*COPY_IGNORES))
    # Each copy is its own repository, so no outer repository's configuration or
    # ignore rules can take part in applying the reference patches.
    _git(destination,'init')
    return destination


def _git(repo,*args):
    process=subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,encoding='utf-8',timeout=PATCH_TIMEOUT)
    if process.returncode: raise ValueError('git failed in the control copy: '+process.stderr.strip()[:200])
    return process.stdout


def apply_patch(copy,text,path,label,expected_hash=None):
    """Apply one patch and prove it changed the files it names.

    An application that exits 0 without changing a named file leaves the control
    comparing two identical trees, so it is reported as a patch error.
    """
    if not isinstance(text,str) or not text.strip(): raise ValueError(f'empty {label} patch')
    encoded=text.encode('utf-8')
    if expected_hash is not None and bytes_hash(encoded)!=expected_hash:
        raise ValueError(f'{label} patch does not match the reviewed hash')
    targets=patch_targets(text)
    if not targets: raise ValueError(f'{label} patch names no target file')
    atomic_write(Path(path),encoded)
    before=_file_state(copy,targets)
    process=subprocess.run(['git','-C',str(copy),'apply','--whitespace=nowarn',str(path)],
        capture_output=True,text=True,encoding='utf-8',timeout=PATCH_TIMEOUT)
    if process.returncode: raise ValueError(f'{label} patch did not apply: '+process.stderr.strip()[:200])
    after=_file_state(copy,targets)
    changed=sorted(name for name in targets if before[name]!=after[name])
    if not changed: raise ValueError(f'{label} patch applied without changing any target file')
    return {'file':Path(path).name,'hash':bytes_hash(encoded),'targets':targets,'changed':changed}


def _role_summary(measured,targets):
    """What one role's run proves, node phase by node phase."""
    nodes=measured['nodes']
    summary={'interpreter':'','exit_code':measured['exit_code'],'python_version':measured.get('python_version',''),
        'pytest_version':measured.get('pytest_version',''),'dependency_hash':measured.get('dependency_hash',''),
        'collection_errors':sorted(measured['collection_errors']),'uncollected':[],'errors':[],'skips':[],
        'targets':{node:dict(nodes.get(node,{})) for node in targets}}
    for node in targets:
        phases=summary['targets'][node]
        if not phases: summary['uncollected'].append(node)
        elif not phases.get('call'): summary['errors'].append(node)
        if any(outcome=='skipped' for outcome in phases.values()): summary['skips'].append(node)
        elif any(outcome=='failed' for phase,outcome in phases.items() if phase!='call'):
            summary['errors'].append(node)
    summary['failed_calls']=sorted(node for node in targets if summary['targets'][node].get('call')=='failed')
    summary['passed_calls']=sorted(node for node in targets if summary['targets'][node].get('call')=='passed')
    return summary


def _unusable(summary):
    """Why this role's run cannot stand as a control, in the order a reader checks."""
    reasons=[]
    # A run that reported nothing measured nothing: no claim about collection, phases
    # or skips can be made from it, so it is named before those.
    if summary.get('runner_failed'): reasons.append('control run reported nothing: '+summary['runner_failed'])
    if summary['collection_errors']: reasons.append('collection error')
    if summary['uncollected']: reasons.append('target test never collected')
    if summary['errors']: reasons.append('setup/teardown error or a target test without a call phase')
    if summary['skips']: reasons.append('skipped test')
    return reasons


def _empty_summary(interpreter,exit_code,targets,message):
    """A role whose run produced no measurement; it must not claim the nodes were seen."""
    return {'interpreter':interpreter,'exit_code':exit_code,'python_version':'','pytest_version':'',
        'dependency_hash':'','collection_errors':[],'uncollected':[],'errors':[],'skips':[],
        'targets':{node:{} for node in targets},'failed_calls':[],'passed_calls':[],'runner_failed':message}


def _run_role(role,copy,binding,interpreter,targets,destination):
    receipt_path=destination/f'{role}.json'
    config={'selectors':list(targets),'pytest_args':list(PYTEST_ARGS),'receipt':str(receipt_path)}
    command=[interpreter,'-c',RUNNER,json.dumps(config)]
    with (destination/f'{role}.stdout.log').open('xb') as stdout,(destination/f'{role}.stderr.log').open('xb') as stderr:
        try:
            process=subprocess.run(command,cwd=copy,env=isolated_environment(copy,binding.get('source_roots') or ('.',)),
                stdout=stdout,stderr=stderr,timeout=RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            return _empty_summary(interpreter,None,targets,'the control run exceeded its time limit')
    try:
        measured=loads(receipt_path.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        return _empty_summary(interpreter,process.returncode,targets,'the target interpreter could not report the run')
    summary=_role_summary(measured,targets); summary['interpreter']=interpreter
    return summary


def run_reference_control(row,binding,output):
    """Run one reference control on fresh copies and return its receipt.

    ``row`` carries the dataset's fix patch, reference tests and target nodes, and is
    read only here.  A measured failure is reported in the receipt; only a call that
    cannot be configured at all raises.
    """
    started=time.monotonic()
    identity=row.get('instance_id')
    if not isinstance(identity,str) or binding.get('instance_id')!=identity:
        raise ValueError('binding instance mismatch')
    targets=reference_targets(row)
    source=Path(binding['buggy_repo']).resolve(); reviewed=Path(binding['fixed_repo']).resolve()
    interpreters={}
    for role in ('buggy','fixed'):
        interpreter=binding.get(f'{role}_python')
        if not isinstance(interpreter,str) or not interpreter: raise ValueError(f'{role} interpreter is not bound')
        interpreters[role]=absolute_python(interpreter)
    destination=fresh_dir(output)
    if not source.is_dir() or not reviewed.is_dir(): raise ValueError('reference repository is missing')
    if any(overlap(destination,repo) for repo in (source,reviewed)):
        raise ValueError('control output overlaps a reviewed repository')
    buggy_copy=_fresh_copy(source,destination/'buggy')
    fixed_copy=_fresh_copy(source,destination/'fixed')
    receipt={'schema_version':1,'instance_id':identity,'target_nodes':list(targets),
        'source_repository':str(source),'reviewed_fixed_repository':str(reviewed),
        'plugin_autoload':False,'pytest_args':list(PYTEST_ARGS),'source_roots':list(binding.get('source_roots') or ('.',))}
    try:
        receipt['fix_patch']=apply_patch(fixed_copy,row.get('patch'),destination/'fix.patch','fix',
            expected_hash=binding.get('fix_patch_hash'))
        receipt['fixed_source_hash']=source_hash(fixed_copy)
        if receipt['fixed_source_hash']!=binding.get('fixed_source_hash'):
            raise ValueError('the applied fix patch does not reproduce the reviewed fixed source')
        receipt['buggy_source_hash']=source_hash(buggy_copy)
        tests=destination/'reference-tests.patch'
        receipt['reference_tests']=apply_patch(buggy_copy,row.get('test_patch'),tests,'reference tests')
        apply_patch(fixed_copy,row.get('test_patch'),tests,'reference tests')
    except (ValueError,OSError,subprocess.SubprocessError) as error:
        receipt.update(status='patch_error',discriminating=False,reason=str(error),duration=time.monotonic()-started)
        write_json(destination/'control.json',receipt)
        return receipt
    roles={role:_run_role(role,copy,binding,interpreters[role],targets,destination)
        for role,copy in (('buggy',buggy_copy),('fixed',fixed_copy))}
    receipt['roles']=roles
    broken=[f'{role}: {reason}' for role,summary in roles.items() for reason in _unusable(summary)]
    if broken:
        status,reason='not_discriminating',' and '.join(broken)
    elif not roles['buggy']['failed_calls']:
        status,reason='not_discriminating','no target test call failed on the original version'
    elif roles['fixed']['passed_calls']!=sorted(targets):
        status,reason='not_discriminating','the fixed version did not pass every target test call'
    else:
        status,reason='discriminating',''
    receipt.update(status=status,discriminating=status=='discriminating',reason=reason,duration=time.monotonic()-started)
    write_json(destination/'control.json',receipt)
    return receipt


def _snapshot_rows(path):
    """Only the gold columns a control needs, keyed by instance ID."""
    rows={}
    for row in read_json(path):
        identity=row.get('instance_id') if isinstance(row,dict) else None
        if isinstance(identity,str):
            rows[identity]={key:row.get(key) for key in
                ('instance_id','repo','base_commit','patch','test_patch','FAIL_TO_PASS')}
    return rows


def run_controls(snapshot,manifest,bindings,output):
    """Run the reference controls of a selected manifest, keeping every case visible.

    A case without a pinned binding stays in the denominator as ``not_run``, and a
    bound case whose control cannot be configured is recorded rather than dropped.
    """
    selected=read_json(manifest); verify_seal(selected,'manifest_hash')
    bindings=load_bindings(bindings)
    for binding in bindings.values():
        for key in ('buggy_repo','fixed_repo'):
            if key in binding and overlap(output,binding[key]): raise ValueError('output overlaps a target repository')
    rows=_snapshot_rows(snapshot); snapshot_hash=file_hash(snapshot); destination=fresh_dir(output)
    receipts={}
    def save():
        # Written after every case, so a long control run leaves a readable partial receipt.
        counts=Counter(value['status'] for value in receipts.values())
        write_json(destination/'verification.json',{'schema_version':1,'manifest_hash':selected['manifest_hash'],
            'snapshot_hash':snapshot_hash,'all_cases':len(selected['cases']),'counts':dict(sorted(counts.items())),
            'cases':receipts})
        return counts
    for case in selected['cases']:
        identity=case['instance_id']; row=rows.get(identity); binding=bindings.get(identity)
        if binding is None:
            receipts[identity]={'status':'not_run','discriminating':False,'reason':'no pinned environment binding'}
        elif row is None or any(row[key]!=case[key] for key in ('repo','base_commit')):
            receipts[identity]={'status':'configuration_error','discriminating':False,
                'reason':'snapshot row does not match the selected manifest case'}
        else:
            try: receipts[identity]=run_reference_control(row,binding,destination/identity)
            except (ValueError,KeyError,TypeError,OSError,subprocess.SubprocessError) as error:
                receipts[identity]={'status':'configuration_error','discriminating':False,
                    'reason':type(error).__name__+': '+str(error)}
        counts=save()
    if not selected['cases']: counts=save()
    return {'all_cases':len(selected['cases']),'controlled':sum(value['discriminating'] for value in receipts.values()),
        'counts':dict(sorted(counts.items()))}
