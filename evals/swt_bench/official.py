"""Optional explicit invocation of the pinned external Linux/Docker harness."""
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .io import read_json,verify_seal,write_json,file_hash
from .prepare import git_output


def check_harness(harness,commit):
    from reproagent.workspace import inventory
    from reproagent.core.serialization import canonical_hash,bytes_hash
    harness=Path(harness).resolve()
    if git_output(harness,'rev-parse','HEAD')!=commit or git_output(harness,'diff','HEAD','--'):
        raise ValueError('harness commit/source mismatch')
    # The harness writes these while it runs. locks/ is created by its Locker and
    # is not always released, so counting it as input aborted a completed run's
    # results with 'source changed'; the other three are its log/report trees.
    exclusions=tuple(harness/name for name in ('image_build_logs','run_instance_swt_logs','evaluation_results','locks'))
    contents=inventory(harness,exclusions)
    tracked=set(git_output(harness,'ls-files','-z').split('\0'))-{''}
    if set(contents)-tracked: raise ValueError('untracked or ignored harness input')
    return canonical_hash({path:bytes_hash(data) for path,data in contents.items()})


def official_command(round_dir,data):
    ids=[case['instance_id'] for case in data['manifest']['cases']]
    args=['python','-m','src.main','--dataset_name','PINNED_SNAPSHOT',
          '--predictions_path','ROUND_PREDICTIONS','--run_id',data['run_id'],'--instance_ids',*ids,
          '--max_workers','1','--compute_coverage','false','--exec_mode','unit_test']
    command=shlex.join(args).replace('ROUND_PREDICTIONS','"${REPROAGENT_ROUND:?Set the copied round directory}/predictions.jsonl"')
    command=command.replace('PINNED_SNAPSHOT','"${REPROAGENT_SWT_SNAPSHOT:?Set the pinned snapshot JSON}"')
    return '# Run inside the pinned SWT-Bench harness on Linux with Docker.\n# Harness commit: '+data['harness_commit']+'\n'+command+'\n'


def prepare_retry(root):
    """Clear a failed official attempt so its round can be graded again.

    A recorded verdict is never re-run: the round keeps the one official result it has.  An
    attempt that failed for infrastructure is not a verdict -- it leaves ``infra_error`` on
    every case and no report hashes -- so it must not cost the round.  It must not require
    deleting its leftovers by hand either: the attempt owns ``official.stdout.log`` and
    ``official.stderr.log``, and the runner opens those exclusively, so a retry otherwise
    died on them with a FileExistsError that named neither the file nor the reason.

    Returns:
        str | None: the stop reason being retried, or None when this is a first attempt.
    """
    receipt=Path(root).resolve()/'official-execution.receipt.json'
    if not receipt.exists(): return None
    recorded=read_json(receipt)
    if not recorded.get('stop_reason'): raise ValueError('official execution already recorded; choose a new round')
    for name in ('official.stdout.log','official.stderr.log'):
        (receipt.parent/name).unlink(missing_ok=True)
    receipt.unlink()
    return recorded['stop_reason']


def run_official(round_dir,harness_dir,python=None,snapshot_path=None):
    root=Path(round_dir).resolve(); harness=Path(harness_dir).resolve()
    data=read_json(root/'round.json'); verify_seal(data,'round_hash')
    if os.name=='nt': raise ValueError('official harness requires a prepared Linux/Docker environment; use official-command.sh')
    harness_hash=check_harness(harness,data['harness_commit'])
    if snapshot_path is None or file_hash(snapshot_path)!=data['manifest']['source']['snapshot_sha256']:
        raise ValueError('pinned dataset snapshot missing or changed')
    if file_hash(root/'predictions.jsonl')!=data['predictions_hash']: raise ValueError('prediction hash mismatch')
    ids=[case['instance_id'] for case in data['manifest']['cases']]
    args=[python or sys.executable,'-m','src.main','--dataset_name',str(Path(snapshot_path).resolve()),
        '--predictions_path',str(root/'predictions.jsonl'),'--run_id',data['run_id'],'--instance_ids',*ids,
        '--max_workers','1','--compute_coverage','false','--exec_mode','unit_test']
    if (harness/'run_instance_swt_logs'/data['run_id']).exists(): raise ValueError('harness run already exists')
    # Last, so a retry that fails a precondition above leaves the attempt it would replace
    # recorded rather than erasing the only evidence of it.
    retried_after=prepare_retry(root)
    return execute_official(root,harness,data,args,harness_hash,retried_after=retried_after)


def execute_official(root,harness,data,args,harness_hash,retried_after=None):
    from .io import seal
    from .results import save_summary,import_reports
    ids=[case['instance_id'] for case in data['manifest']['cases']]
    receipt={'executor':'reproagent-swt-official-run-v1','run_id':data['run_id'],'model_name':data['model_name'],
        'harness_commit':data['harness_commit'],'predictions_hash':data['predictions_hash'],
        'manifest_hash':data['manifest']['manifest_hash'],'report_hashes':{},'command':args,'harness_source_hash':harness_hash,
        'dataset_snapshot_hash':data['manifest'].get('source',{}).get('snapshot_sha256'),'exit_code':None,'stop_reason':'',
        #: The stop reason of the attempt this one retries, so a retry is visible in the
        #: record that survives rather than only in the attempt it replaced.
        'retried_after':retried_after}
    started=time.monotonic()
    def failure(reason):
        receipt.update(stop_reason=reason,duration=time.monotonic()-started)
        write_json(root/'official-execution.receipt.json',receipt)
        updated={key:value for key,value in data.items() if key!='round_hash'}
        updated['outcomes']={identity:{**value,'official_status':'infra_error','official_resolved':None,'official_stop_reason':reason}
            for identity,value in data['outcomes'].items()}
        updated=seal(updated,'round_hash'); write_json(root/'round.json',updated); save_summary(root,updated)
        raise ValueError('official execution failed; logs and failure receipt retained')
    with (root/'official.stdout.log').open('xb') as stdout,(root/'official.stderr.log').open('xb') as stderr:
        try:
            process=subprocess.run(args,cwd=harness,stdout=stdout,stderr=stderr,timeout=3600*len(ids))
            receipt['exit_code']=process.returncode
        except subprocess.TimeoutExpired: failure('HARNESS_TIMEOUT')
        except OSError: failure('HARNESS_START_FAILED')
    if process.returncode: failure('HARNESS_NONZERO')
    try: after_hash=check_harness(harness,data['harness_commit'])
    except ValueError: failure('HARNESS_SOURCE_CHANGED')
    if after_hash!=harness_hash: failure('HARNESS_SOURCE_CHANGED')
    hashes={}
    for identity in ids:
        path=harness/'run_instance_swt_logs'/data['run_id']/data['model_name']/identity/'report.json'
        if path.is_file(): hashes[identity]=file_hash(path)
    receipt.update(report_hashes=hashes,duration=time.monotonic()-started)
    write_json(root/'official-execution.receipt.json',receipt)
    return import_reports(root,harness/'run_instance_swt_logs',receipt=receipt)
