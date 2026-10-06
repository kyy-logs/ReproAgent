"""Explicit network fetch of pinned public data; optional isolated decoder."""
import subprocess
import sys
import urllib.request
import os

from .swt_bench import DATASET,REVISION,HARNESS_COMMIT,FILTER_HASH
from ..swt_bench.io import file_hash,fresh_dir,loads,write_json
from reproagent.store import atomic_write


def download(url,limit=8388608):
    request=urllib.request.Request(url,headers={'User-Agent':'ReproAgent-SWT-fetch'})
    chunks=[]; size=0
    with urllib.request.urlopen(request,timeout=60) as response:
        while chunk:=response.read(65536):
            size+=len(chunk)
            if size>limit: raise ValueError('dataset download exceeds bound')
            chunks.append(chunk)
    return b''.join(chunks)


def fetch_snapshot(output,decoder_python=None):
    output=fresh_dir(output)
    parquet=output/'test.parquet'
    atomic_write(parquet,download(f'https://huggingface.co/datasets/{DATASET}/resolve/{REVISION}/data/test-00000-of-00001.parquet'))
    filters=output/'excluded.txt'
    atomic_write(filters,download(f'https://raw.githubusercontent.com/logic-star-ai/swt-bench/{HARNESS_COMMIT}/dataset/filter_cases_lite.txt',limit=32768))
    if file_hash(filters)!=FILTER_HASH: raise ValueError('official filter hash mismatch')
    decoder=subprocess.run([decoder_python or sys.executable,'-c',
        'import json,sys,pyarrow.parquet as pq; print(json.dumps(pq.read_table(sys.argv[1]).to_pylist(),ensure_ascii=False))',str(parquet)],
        capture_output=True,timeout=90,env=dict(os.environ,PYTHONIOENCODING='utf-8'))
    if decoder.returncode: raise ValueError('parquet decoder unavailable; install pyarrow in a separate interpreter and pass --decoder-python')
    if len(decoder.stdout)>33554432: raise ValueError('decoded snapshot exceeds bound')
    rows=loads(decoder.stdout.decode('utf-8')); write_json(output/'snapshot.json',rows)
    source={'dataset':DATASET,'revision':REVISION,'split':'test','harness_commit':HARNESS_COMMIT,
        'parquet_sha256':file_hash(parquet),'snapshot_sha256':file_hash(output/'snapshot.json'),'filter_sha256':file_hash(filters)}
    write_json(output/'source.json',source)
    return source
