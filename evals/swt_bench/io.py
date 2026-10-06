import json
from pathlib import Path

from reproagent.core.serialization import bytes_hash, canonical_bytes, canonical_hash
from reproagent.store import atomic_write


def loads(text):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ValueError('duplicate JSON field')
            result[key]=value
        return result
    def invalid(_): raise ValueError('non-finite JSON value')
    return json.loads(text,object_pairs_hook=pairs,parse_constant=invalid)


def read_json(path):
    path=Path(path)
    if path.stat().st_size>33554432: raise ValueError('evaluation input exceeds 32 MiB')
    return loads(path.read_text(encoding='utf-8'))


def write_json(path,value):
    atomic_write(Path(path),canonical_bytes(value)+b'\n')


def file_hash(path): return bytes_hash(Path(path).read_bytes())


def seal(value,key):
    return {**value,key:canonical_hash(value)}


def verify_seal(value,key):
    if not isinstance(value,dict) or value.get(key)!=canonical_hash({k:v for k,v in value.items() if k!=key}):
        raise ValueError(f'{key} mismatch')


def fresh_dir(path):
    path=Path(path).resolve()
    path.mkdir(parents=True,exist_ok=False)
    return path
