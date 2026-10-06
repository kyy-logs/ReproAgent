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
