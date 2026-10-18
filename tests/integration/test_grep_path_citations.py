import asyncio
from pathlib import Path
import pytest
import test_agentscope_domain_tools as helper
from reproagent.adapters.agentscope.tools import SnapshotResponseMiddleware


@pytest.mark.parametrize('separator,path_form', [(':','absolute'),('-','absolute'),(':','relative')])
def test_registered_date_path_and_colons_in_code_are_citable(tmp_path,projects,facts,monkeypatch,separator,path_form):
    source='value: int = 6  # needle:12: context-23-end\n'
    monkeypatch.setattr(helper,'MODULE_TEXT',source)
    original=projects.plain
    def create(root):
        repo=original(root)
        (repo/'example'/'part-2026-10.py').write_text(source,encoding='utf-8')
        return repo
    monkeypatch.setattr(projects,'plain',create)
    env=helper.environment(tmp_path/'verification-2026-10-18',projects,facts)
    path='example/part-2026-10.py'
    displayed=str(env.root/path) if path_form=='absolute' else path
    middleware=SnapshotResponseMiddleware(env.backend,env.ledger,env.context,view='search')
    shown=asyncio.run(middleware._shown({'path':str(env.root)},[displayed+separator+'1'+separator+source.rstrip('\n')]))
    assert [(item.path,item.number) for item in shown]==[(path,1)]


def test_single_file_line_with_numeric_colons_remains_citable(tmp_path,projects,facts,monkeypatch):
    source='value:12: body-23-end\n'
    monkeypatch.setattr(helper,'MODULE_TEXT',source)
    env=helper.environment(tmp_path,projects,facts)
    middleware=SnapshotResponseMiddleware(env.backend,env.ledger,env.context,view='search')
    shown=asyncio.run(middleware._shown({'path':env.module},['1:'+source.rstrip('\n')]))
    assert len(shown)==1 and shown[0].number==1


def test_real_grep_date_directory_keeps_evidence(tmp_path,projects,facts):
    rg=helper.find_ripgrep()
    assert rg, 'Set REPROAGENT_RG_PATH for this regression; do not silently skip it'
    env=helper.environment(tmp_path/'verification-2026-10-18',projects,facts,rg=rg)
    text=helper.text_of(helper.call(env,'Grep',{'pattern':helper.MARKER,'path':str(env.root),'output_mode':'content'}))
    refs=helper.sidecar_refs(text)
    assert [(ref['start_line'],ref['end_line']) for ref in refs]==[(helper.MARKER_LINE,helper.MARKER_LINE)]


def test_numeric_body_does_not_create_path_cache_or_quadratic_allocations(tmp_path,projects,facts,monkeypatch):
    import tracemalloc
    env=helper.environment(tmp_path,projects,facts)
    middleware=SnapshotResponseMiddleware(env.backend,env.ledger,env.context,view='search')
    calls=[]
    original=env.ledger.snapshot.registered
    def lookup(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(env.ledger.snapshot,'registered',lookup)
    cache={}
    line=env.module+':1:'+':1:'*4000
    tracemalloc.start()
    try:
        list(middleware._parse_search_line(line,None,cache))
        _,peak=tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(calls)<=2
    assert len(cache)<=1
    assert peak<2*1024*1024
