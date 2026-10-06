"""Export only frozen accepted candidate bytes as standard Git additions."""
import difflib
import hashlib
import re
from pathlib import Path

from reproagent.core.serialization import bytes_hash,canonical_bytes,canonical_hash
from reproagent.store import TaskStore,atomic_write,safe_child
from reproagent.workspace import candidate_hash
from .io import read_json,verify_seal,file_hash,seal,write_json
from .prepare import source_hash


def plain_child(root,relative):
    destination=safe_child(Path(root),relative)
    part=Path(root)
    for name in relative.split('/'):
        part=part/name
        if part.is_symlink() or getattr(part,'is_junction',lambda:False)(): raise ValueError('links cannot be exported')
    return destination


def new_file_patch(path,content):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*',path) or any(part in ('.','..') for part in path.split('/')):
        raise ValueError('unsupported or unsafe patch path')
    text=content.decode('utf-8')
    blob=hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()[:7]
    output=f'diff --git a/{path} b/{path}\nnew file mode 100644\nindex 0000000..{blob}\n'
    if not content: return output
    for line in difflib.unified_diff([],text.splitlines(keepends=True),fromfile='/dev/null',tofile='b/'+path):
        output+=line
        if not line.endswith('\n'): output+='\n\\ No newline at end of file\n'
    return output


def candidate_patch(task_dir,case):
    task_dir=Path(task_dir).resolve(); store=TaskStore(task_dir)
    task=store.load_record('task','task'); request=store.load_record('request','request')
    if task.status.value!='DONE' or task.export_state!='published' or task.task_id!=case.case_id or request.task_id!=case.case_id:
        raise ValueError('task identity/state mismatch')
    if request.repo.resolve()!=case.buggy_repo.resolve() or source_hash(case.buggy_repo)!=case.buggy_source_hash:
        raise ValueError('buggy source changed or mismatched')
    if source_hash(case.fixed_repo)!=case.fixed_source_hash: raise ValueError('fixed source changed')
    if bytes_hash(case.allowed_description.encode())!=case.issue_hash: raise ValueError('issue hash mismatch')
    candidate=store.load_record('candidates',task.accepted_candidate_id)
    if candidate_hash(candidate)!=candidate.manifest_hash: raise ValueError('candidate hash changed')
    contract=store.load_contract(candidate.contract_id,candidate.contract_version)
    if contract.description_hash!=case.issue_hash: raise ValueError('candidate issue binding mismatch')
    bundle=task_dir/'artifacts/reproduction'; manifest=read_json(bundle/'manifest.json')
    expected=canonical_hash({key:manifest[key] for key in ('package_kind','files','candidate_id','event_cutoff')})
    if manifest['manifest_hash']!=expected or manifest['candidate_id']!=candidate.candidate_id:
        raise ValueError('export manifest hash mismatch')
    for entry in manifest['files']:
        file=plain_child(bundle,entry['path'])
        if file_hash(file)!=entry['content_hash']: raise ValueError('export file hash changed')
    report=read_json(bundle/'report.json')
    if report['task_id']!=case.case_id or report['candidate_manifest_hash']!=candidate.manifest_hash or report['verified'] is not True:
        raise ValueError('export report identity mismatch')
    protected={entry['path'] for entry in report['protected_snapshot']['files']}
    output=[]
    for entry in candidate.files:
        if entry.path in protected or not entry.path.startswith(case.candidate_parent.rstrip('/')+'/'):
            raise ValueError('candidate overwrites source or lies outside candidate directory')
        if plain_child(case.buggy_repo,entry.path).exists() or plain_child(case.fixed_repo,entry.path).exists():
            raise ValueError('candidate would overwrite original or fixed file')
        if entry.role not in ('test','data'): raise ValueError('unsupported candidate file role')
        original=plain_child(candidate.storage_root,entry.path).read_bytes()
        exported=plain_child(bundle,'candidate/'+entry.path).read_bytes()
        if bytes_hash(original)!=entry.content_hash or exported!=original: raise ValueError('candidate bytes changed')
        output.append(new_file_patch(entry.path,original))
    if not output: raise ValueError('empty accepted candidate')
    return ''.join(output)


def write_predictions(manifest,outcomes,model_name,output):
    verify_seal(manifest,'manifest_hash')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',model_name): raise ValueError('model name must be a simple run label')
    identities=[case['instance_id'] for case in manifest['cases']]
    if set(outcomes)-set(identities): raise ValueError('unknown prediction instance')
    lines=[]
    for identity in identities:
        outcome=outcomes.get(identity,{})
        patch=outcome.get('prediction_patch','') if outcome.get('status')=='DONE' else ''
        if type(patch) is not str: raise ValueError('invalid prediction patch')
        lines.append(canonical_bytes({'instance_id':identity,'model_name_or_path':model_name,'model_patch':patch})+b'\n')
    atomic_write(Path(output),b''.join(lines))
    receipt=seal({'schema_version':1,'manifest_hash':manifest['manifest_hash'],'model_name':model_name,
        'predictions_hash':file_hash(output),'count':len(lines)},'receipt_hash')
    write_json(Path(output).with_suffix('.receipt.json'),receipt)
    return receipt
