"""Pinned SWT-Lite import and sampling; gold material never enters catalog."""
import hashlib
import re
from pathlib import Path

from reproagent.core.serialization import bytes_hash
from ..swt_bench.io import file_hash,loads,seal,verify_seal

DATASET='princeton-nlp/SWE-bench_Lite'
REVISION='6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2'
HARNESS_COMMIT='330a649a764fab2fadaea632776eeae87272f74b'
FILTER_HASH='d88e9ecfc5ab4f68cb961cea90bf54ddcd7d8af58b77c15e7def8a5b2fd00f2b'
SEED='reproagent-swt-dev-v1'
HOLDOUT_SEED='reproagent-swt-holdout-v1'
DEV_REPOS=('pallets/flask','psf/requests','pytest-dev/pytest','sphinx-doc/sphinx','sympy/sympy')


def generation_fields(row):
    return {key:row[key] for key in ('instance_id','repo','base_commit','problem_statement','description_hash')}


def load_snapshot(path,filter_path,source,*,expected_count=300):
    if source.get('dataset')!=DATASET or source.get('revision')!=REVISION or source.get('harness_commit')!=HARNESS_COMMIT or source.get('split')!='test':
        raise ValueError('dataset/harness identity mismatch')
    if file_hash(path)!=source.get('snapshot_sha256') or file_hash(filter_path)!=source.get('filter_sha256'):
        raise ValueError('snapshot/filter hash mismatch')
    text=Path(path).read_text(encoding='utf-8')
    rows=loads(text) if text.lstrip().startswith('[') else [loads(line) for line in text.splitlines() if line.strip()]
    if not isinstance(rows,list) or len(rows)!=expected_count: raise ValueError('snapshot row count mismatch')
    excluded=set(Path(filter_path).read_text(encoding='utf-8').splitlines())
    if expected_count==300 and (source['filter_sha256']!=FILTER_HASH or len(excluded)!=24):
        raise ValueError('official exclusion hash/count mismatch')
    seen=set(); entries=[]
    for row in rows:
        required=('instance_id','repo','base_commit','problem_statement','version','patch','test_patch')
        if not isinstance(row,dict) or any(type(row.get(key)) is not str for key in required):
            raise ValueError('missing or invalid dataset field')
        identity=row['instance_id']; repo=row['repo']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) or not re.fullmatch(re.escape(repo.replace('/','__'))+r'-[0-9]+',identity):
            raise ValueError('instance/repo identity mismatch')
        if identity in seen: raise ValueError('duplicate instance ID')
        seen.add(identity)
        if not re.fullmatch(r'[0-9a-f]{40}',row['base_commit']) or not row['problem_statement'].strip():
            raise ValueError('invalid commit or empty issue')
        if identity in excluded: continue
        entries.append({**{key:row[key] for key in required if key not in ('patch','test_patch')},
            'description_hash':bytes_hash(row['problem_statement'].encode('utf-8')),
            'fix_patch_hash':bytes_hash(row['patch'].encode('utf-8')),
            'oracle_test_hash':bytes_hash(row['test_patch'].encode('utf-8')),
            'environment_setup_commit':row.get('environment_setup_commit') or ''})
    if excluded-seen: raise ValueError('exclusion contains unknown IDs')
    return seal({'schema_version':1,'source':dict(source),'scope':'lite' if expected_count==300 else 'test_fixture',
        'total_rows':len(rows),'excluded_ids':sorted(excluded),'entries':entries},'catalog_hash')


def select_dev(catalog,count=20):
    verify_seal(catalog,'catalog_hash')
    if type(count) is not int or not 1<=count<=20: raise ValueError('dev count must be 1..20')
    pools={repo:sorted([entry for entry in catalog['entries'] if entry['repo']==repo],
        key=lambda entry:hashlib.sha256((SEED+':'+entry['instance_id']).encode()).hexdigest()) for repo in DEV_REPOS}
    selected=[]
    while len(selected)<count:
        previous=len(selected)
        for repo in DEV_REPOS:
            if pools[repo] and len(selected)<count: selected.append(pools[repo].pop(0))
        if len(selected)==previous: raise ValueError('not enough eligible development samples')
    cases=[{key:entry[key] for key in ('instance_id','repo','base_commit','description_hash')} for entry in selected]
    return seal({'schema_version':1,'purpose':'development','seed':SEED,'selection':'repo-round-robin-sha256-v1',
        'source':catalog['source'],'catalog_hash':catalog['catalog_hash'],'cases':cases},'manifest_hash')


def select_holdout(catalog,excluded_ids,*,repos,count=10,seed=HOLDOUT_SEED):
    """Select a frozen holdout manifest from public generation-side metadata only.

    ``repos`` is the caller's list of projects whose Python/pytest configuration is
    actually supported, in the order the pools are consumed; each pool is ordered by
    ``sha256(seed + ':' + instance_id)`` and drawn round-robin, so the same catalog,
    exclusions, repositories and seed always produce the same sealed manifest.
    ``excluded_ids`` holds every identifier that must not be re-selected -- the pinned
    development set and any already-debugged case -- and an identifier the catalog does
    not contain is simply an identifier nothing can be selected from, so it is recorded
    but not rejected.

    The hidden material stays hidden: dataset patches, reference tests and reference
    control results are never read, and nothing derived from generation results is an
    input here. Failures keep their place in the denominator because the caller freezes
    this manifest before any case runs.
    """
    verify_seal(catalog,'catalog_hash')
    if type(count) is not int or count<1: raise ValueError('holdout count must be a positive integer')
    if not isinstance(repos,tuple) or not repos or any(type(repo) is not str for repo in repos) or len(set(repos))!=len(repos):
        raise ValueError('holdout repositories must be a non-empty unique sequence')
    excluded=set(excluded_ids)
    pools={repo:sorted((entry for entry in catalog['entries']
        if entry['repo']==repo and entry['instance_id'] not in excluded),
        key=lambda entry:hashlib.sha256((seed+':'+entry['instance_id']).encode()).hexdigest()) for repo in repos}
    selected=[]
    while len(selected)<count:
        previous=len(selected)
        for repo in repos:
            if pools[repo] and len(selected)<count: selected.append(pools[repo].pop(0))
        if len(selected)==previous: raise ValueError('not enough eligible holdout samples')
    cases=[{key:entry[key] for key in ('instance_id','repo','base_commit','description_hash')} for entry in selected]
    return seal({'schema_version':1,'purpose':'holdout','seed':seed,'selection':'repo-round-robin-sha256-v1',
        'repos':list(repos),'excluded_ids':sorted(excluded),'source':catalog['source'],
        'catalog_hash':catalog['catalog_hash'],'cases':cases},'manifest_hash')
