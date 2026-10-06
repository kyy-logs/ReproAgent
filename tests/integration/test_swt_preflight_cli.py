import subprocess
import sys

from tests.integration.test_swt_batch import inputs


def test_preflight_cli_records_all_ids_without_model_configuration(tmp_path,projects):
    from evals.swt_bench.io import write_json,read_json
    catalog,manifest,bindings=inputs(tmp_path,projects)
    data=tmp_path/'data'; data.mkdir()
    for name,value in [('catalog',catalog),('manifest',manifest),('bindings',bindings)]: write_json(data/(name+'.json'),value)
    output=tmp_path/'preflight'
    result=subprocess.run([sys.executable,'-m','evals.swt_bench','preflight','--catalog',str(data/'catalog.json'),
        '--manifest',str(data/'manifest.json'),'--bindings',str(data/'bindings.json'),'--output',str(output)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    receipts=read_json(output/'preparation.json')
    assert len(receipts)==2 and receipts['fixture__repo-1']['status']=='ready' and receipts['fixture__repo-2']['status']=='pending'
