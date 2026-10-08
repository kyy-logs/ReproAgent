import hashlib
import json

import pytest


def fixture_snapshot(tmp_path, rows=None):
    from evals.datasets.swt_bench import DATASET, REVISION, HARNESS_COMMIT
    rows=rows or [row('sympy/sympy',1),row('psf/requests',2),row('sympy/sympy',3)]
    snapshot=tmp_path/'snapshot.json'; snapshot.write_text(json.dumps(rows),encoding='utf-8')
    exclusions=tmp_path/'exclude.txt'; exclusions.write_text('sympy__sympy-1\n',encoding='utf-8')
    source={'dataset':DATASET,'revision':REVISION,'harness_commit':HARNESS_COMMIT,'split':'test',
        'snapshot_sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        'filter_sha256':hashlib.sha256(exclusions.read_bytes()).hexdigest()}
    return snapshot,exclusions,source


def row(repo,number):
    return {'instance_id':repo.replace('/','__')+'-'+str(number),'repo':repo,'base_commit':'a'*40,
        'problem_statement':'  Original issue\nUnicode 中文\n','version':'1.0','environment_setup_commit':'b'*40,
        'patch':'private fix','test_patch':'hidden oracle','hints_text':'private hint'}


def test_exclusions_and_generation_whitelist_preserve_original_issue(tmp_path):
    from evals.datasets.swt_bench import load_snapshot,generation_fields
    snapshot,filters,source=fixture_snapshot(tmp_path)
    catalog=load_snapshot(snapshot,filters,source,expected_count=3)
    assert catalog['total_rows']==3 and len(catalog['entries'])==2
    assert [entry['instance_id'] for entry in catalog['entries']]==['psf__requests-2','sympy__sympy-3']
    entry=catalog['entries'][0]
    assert entry['problem_statement']=='  Original issue\nUnicode 中文\n'
    assert not any(word in json.dumps(catalog) for word in ('private fix','hidden oracle','private hint'))
    assert generation_fields({**entry,'patch':'forbidden','test_patch':'forbidden','hints_text':'forbidden'})=={
        key:entry[key] for key in ('instance_id','repo','base_commit','problem_statement','description_hash')}


@pytest.mark.parametrize('mutation',['duplicate','missing','wrong_repo','wrong_commit','empty'])
def test_invalid_records_are_rejected(tmp_path,mutation):
    from evals.datasets.swt_bench import load_snapshot
    rows=[row('sympy/sympy',1),row('sympy/sympy',2)]
    if mutation=='duplicate': rows[1]=rows[0]
    if mutation=='missing': rows[1].pop('patch')
    if mutation=='wrong_repo': rows[1]['repo']='other/repo'
    if mutation=='wrong_commit': rows[1]['base_commit']='not a commit'
    if mutation=='empty': rows[1]['problem_statement']='  '
    snapshot,filters,source=fixture_snapshot(tmp_path,rows)
    with pytest.raises(ValueError): load_snapshot(snapshot,filters,source,expected_count=2)


def test_changed_source_and_duplicate_json_fields_are_rejected(tmp_path):
    from evals.datasets.swt_bench import load_snapshot
    snapshot,filters,source=fixture_snapshot(tmp_path)
    filters.write_text('changed',encoding='utf-8')
    with pytest.raises(ValueError,match='hash'): load_snapshot(snapshot,filters,source,expected_count=3)
    snapshot.write_text('[{"instance_id":"one","instance_id":"two"}]',encoding='utf-8')
    source['snapshot_sha256']=hashlib.sha256(snapshot.read_bytes()).hexdigest()
    source['filter_sha256']=hashlib.sha256(filters.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='duplicate'): load_snapshot(snapshot,filters,source,expected_count=1)


def test_selection_is_deterministic_and_does_not_use_oracles(tmp_path):
    from evals.datasets.swt_bench import load_snapshot,select_dev
    rows=[row(repo,number) for repo in ('sympy/sympy','psf/requests','pallets/flask') for number in range(1,10)]
    snapshot,filters,source=fixture_snapshot(tmp_path,rows)
    catalog=load_snapshot(snapshot,filters,source,expected_count=len(rows))
    first=select_dev(catalog,count=20); second=select_dev(catalog,count=20)
    assert first==second and len(first['cases'])==20 and len({case['instance_id'] for case in first['cases']})==20
    assert first['catalog_hash']==catalog['catalog_hash']
    assert not any(word in json.dumps(first) for word in ('private fix','hidden oracle','private hint'))


def test_holdout_is_frozen_and_not_selected_by_hidden_results(tmp_path):
    from evals.datasets.swt_bench import load_snapshot,select_dev,select_holdout
    from evals.swt_bench.io import seal,verify_seal
    repos=('sympy/sympy','psf/requests','pallets/flask')
    rows=[row(repo,number) for repo in repos for number in range(1,12)]
    snapshot,filters,source=fixture_snapshot(tmp_path,rows)
    catalog=load_snapshot(snapshot,filters,source,expected_count=len(rows))
    developed={case['instance_id'] for case in select_dev(catalog,count=20)['cases']}
    excluded=developed|{'sympy__sympy-7','psf__requests-11'}
    before=sorted(path.name for path in tmp_path.iterdir())
    first=select_holdout(catalog,excluded,repos=repos,count=10); second=select_holdout(catalog,excluded,repos=repos,count=10)
    # A fixed seed is reproducible, and the seed is what orders the selection.
    verify_seal(first,'manifest_hash')
    assert first==second and first['purpose']=='holdout' and first['catalog_hash']==catalog['catalog_hash']
    assert first['source']==catalog['source'] and first['excluded_ids']==sorted(excluded)
    cases=[case['instance_id'] for case in first['cases']]
    assert len(cases)==10 and len(set(cases))==10
    # Development and already-debugged samples are frozen out, and the round robin keeps the
    # spread over every requested repository.
    assert not set(cases)&excluded and set(cases)<={entry['instance_id'] for entry in catalog['entries']}
    assert {case['repo'] for case in first['cases']}==set(repos)
    # Every case is the catalog row verbatim, which is what a round re-validates before it runs.
    by_id={entry['instance_id']:entry for entry in catalog['entries']}
    assert all(case=={key:by_id[case['instance_id']][key] for key in case} for case in first['cases'])
    assert select_holdout(catalog,excluded,repos=repos,count=10,seed='reproagent-swt-holdout-v2')['cases']!=first['cases']
    # Selection reads only the public catalog: a catalog whose gold columns changed, or lost
    # them entirely, and one whose entries are reordered, select the very same cases.
    def reseal(entries):
        return seal({**{key:value for key,value in catalog.items() if key!='catalog_hash'},'entries':entries},'catalog_hash')
    altered=reseal([{**entry,'fix_patch_hash':'x'*64,'oracle_test_hash':'y'*64} for entry in catalog['entries']])
    stripped=reseal([{key:value for key,value in entry.items() if key not in ('fix_patch_hash','oracle_test_hash')}
        for entry in reversed(catalog['entries'])])
    assert select_holdout(altered,excluded,repos=repos,count=10)['cases']==first['cases']
    assert select_holdout(stripped,excluded,repos=repos,count=10)['cases']==first['cases']
    assert not any(word in json.dumps(first) for word in ('private fix','hidden oracle','private hint'))
    assert all(set(case)=={'instance_id','repo','base_commit','description_hash'} for case in first['cases'])
    # Freezing a holdout rewrites nothing: a prior dev manifest and a round whose official
    # grading is still pending stay readable and unchanged.
    dev=select_dev(catalog,count=20)
    round_data=seal({'run_id':'dev20','model_name':'model','manifest':dev,
        'outcomes':{'sympy__sympy-2':{'status':'DONE','official_status':'not_run','official_resolved':None}}},'round_hash')
    recorded=json.dumps([dev,round_data],sort_keys=True)
    select_holdout(catalog,excluded,repos=repos,count=10)
    verify_seal(dev,'manifest_hash'); verify_seal(round_data,'round_hash')
    assert json.dumps([dev,round_data],sort_keys=True)==recorded
    assert round_data['outcomes']['sympy__sympy-2']['official_status']=='not_run'
    assert sorted(path.name for path in tmp_path.iterdir())==before


def test_holdout_refuses_an_exclusion_list_or_pool_it_cannot_honour(tmp_path):
    from evals.datasets.swt_bench import load_snapshot,select_holdout
    rows=[row('sympy/sympy',number) for number in range(1,5)]
    snapshot,filters,source=fixture_snapshot(tmp_path,rows)
    catalog=load_snapshot(snapshot,filters,source,expected_count=len(rows))
    with pytest.raises(ValueError,match='unknown'): select_holdout(catalog,{'sympy__sympy-99'},repos=('sympy/sympy',),count=1)
    with pytest.raises(ValueError,match='not enough'): select_holdout(catalog,set(),repos=('sympy/sympy',),count=10)
    with pytest.raises(ValueError): select_holdout(catalog,set(),repos=('other/repo',),count=1)
    with pytest.raises(ValueError): select_holdout(catalog,set(),repos=(),count=1)
    with pytest.raises(ValueError): select_holdout(catalog,set(),repos=('sympy/sympy',),count=0)
    with pytest.raises(ValueError): select_holdout(catalog,set(),repos=('sympy/sympy',),count=1,seed='')
    tampered={**catalog,'catalog_hash':'tampered'}
    with pytest.raises(ValueError,match='catalog_hash'): select_holdout(tampered,set(),repos=('sympy/sympy',),count=1)


def test_holdout_is_deterministic_and_excludes_development_ids(tmp_path):
    import collections
    from evals.datasets.swt_bench import load_snapshot,select_dev,select_holdout
    repos=('pallets/flask','psf/requests','pytest-dev/pytest','sphinx-doc/sphinx','sympy/sympy')
    rows=[row(repo,number) for repo in repos for number in range(1,8)]
    snapshot,filters,source=fixture_snapshot(tmp_path,rows)
    catalog=load_snapshot(snapshot,filters,source,expected_count=len(rows))
    development=select_dev(catalog,count=20); development_ids={case['instance_id'] for case in development['cases']}
    first=select_holdout(catalog,development_ids,repos=repos,count=10)
    second=select_holdout(catalog,development_ids,repos=repos,count=10)
    assert first==second and first['catalog_hash']==catalog['catalog_hash'] and first['manifest_hash']
    selected=[case['instance_id'] for case in first['cases']]
    assert len(selected)==10 and len(set(selected))==10 and not set(selected)&development_ids
    # Repository round-robin over the requested order, then the fixed-seed hash order.
    assert collections.Counter(case['repo'] for case in first['cases'])=={repo:2 for repo in repos}
    assert all(set(case)=={'instance_id','repo','base_commit','description_hash'} for case in first['cases'])
    by_id={entry['instance_id']:entry for entry in catalog['entries']}
    assert all(case=={key:by_id[case['instance_id']][key] for key in ('instance_id','repo','base_commit','description_hash')}
        for case in first['cases'])
    assert not any(word in json.dumps(first) for word in ('private fix','hidden oracle','private hint'))


def test_selection_does_not_depend_on_hidden_patch_or_control_results(tmp_path):
    from evals.datasets.swt_bench import load_snapshot,select_holdout
    from evals.swt_bench.io import seal
    repos=('sympy/sympy','pytest-dev/pytest')
    rows=[row(repo,number) for repo in repos for number in range(1,8)]
    snapshot,filters,source=fixture_snapshot(tmp_path,rows)
    catalog=load_snapshot(snapshot,filters,source,expected_count=len(rows))
    selected=select_holdout(catalog,set(),repos=repos,count=6)
    identities=[case['instance_id'] for case in selected['cases']]
    # The dataset's fix patch, its reference tests and every control result behind them
    # are hidden from generation: a catalog whose opaque hashes differ, and one that
    # never carried them at all, must select exactly the same cases in the same order.
    public={key:value for key,value in catalog.items() if key!='catalog_hash'}
    scrambled=seal({**public,'entries':[{**entry,'fix_patch_hash':'0'*64,'oracle_test_hash':'f'*64} for entry in catalog['entries']]},'catalog_hash')
    stripped=seal({**public,'entries':[{key:value for key,value in entry.items()
        if key not in ('fix_patch_hash','oracle_test_hash')} for entry in catalog['entries']]},'catalog_hash')
    assert [case['instance_id'] for case in select_holdout(scrambled,set(),repos=repos,count=6)['cases']]==identities
    assert [case['instance_id'] for case in select_holdout(stripped,set(),repos=repos,count=6)['cases']]==identities
    assert not any(key in json.dumps(selected) for key in ('fix_patch_hash','oracle_test_hash'))


def test_decoder_handles_unicode_independent_of_windows_console(tmp_path,monkeypatch):
    import subprocess
    from evals.datasets import fetch_swt
    from evals.datasets.swt_bench import FILTER_HASH
    def decoder(argv,**kwargs):
        assert kwargs['env']['PYTHONIOENCODING']=='utf-8'
        return subprocess.CompletedProcess(argv,0,json.dumps([{'text':'👋 中文'}],ensure_ascii=False).encode('utf-8'),b'')
    monkeypatch.setattr(fetch_swt.subprocess,'run',decoder)
    monkeypatch.setattr(fetch_swt,'download',lambda *args,**kwargs:b'fixture')
    original=fetch_swt.file_hash
    monkeypatch.setattr(fetch_swt,'file_hash',lambda path:FILTER_HASH if path.name=='excluded.txt' else original(path))
    source=fetch_swt.fetch_snapshot(tmp_path/'download','decoder')
    assert json.loads((tmp_path/'download'/'snapshot.json').read_text(encoding='utf-8'))[0]['text']=='👋 中文'
