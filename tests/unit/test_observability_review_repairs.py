import json
import pytest
from reproagent.observability import TraceRecorder,span,mark,capture
from reproagent.observability_content import ContentStore
from reproagent.core.budget import BudgetStopped
from tests.integration.test_cli import run_cli
from tests.integration.test_agentscope_observability import traced
from tests.unit.test_trace_view_model import fixture,node
from reproagent.trace_rendering import render_trace


def test_final_snapshot_failure_preserves_cli_result(tmp_path,monkeypatch,capsys):
    import reproagent.cli as cli
    def fail(*args): raise RuntimeError('snapshot observation failed')
    monkeypatch.setattr(cli,'budget_snapshot',fail)
    code,_=run_cli(tmp_path,monkeypatch,[])
    output=capsys.readouterr()
    assert code==0 and json.loads(output.out)['status']=='DONE'
    assert cli.TRACE_WRITE_WARNING in output.err


def test_trace_finalization_failure_preserves_cli_result(tmp_path,monkeypatch,capsys):
    import reproagent.cli as cli
    def fail(*args,**kwargs): raise RuntimeError('finalization observation failed')
    monkeypatch.setattr(TraceRecorder,'finish',fail)
    code,_=run_cli(tmp_path,monkeypatch,[])
    output=capsys.readouterr()
    assert code==0 and json.loads(output.out)['status']=='DONE'
    assert cli.TRACE_WRITE_WARNING in output.err


def test_retained_points_do_not_make_duplicate_otel_event_drops_statistical():
    with TraceRecorder() as recorder:
        for i in range(33): mark('test.point')
        with span('model.http_attempt',attributes={'usage':{'input_tokens':10,'output_tokens':5}}): pass
        doc=recorder.finish(task_id='one',status='DONE',main_duration=1)
    assert sum(s['name']=='test.point' for s in doc['spans'])==33
    assert doc['capture_health']['counts_complete']
    assert doc['summary']['usage']=={'input_tokens':10,'output_tokens':5,'complete':True}
    assert any(i['code']=='OTEL_DROPPED_FIELDS' for i in doc['capture_health']['issues'])


def test_module_capture_forwards_all_missing_metadata():
    with TraceRecorder() as recorder:
        capture('wire_request',None,source='wire_request',availability='omitted_limit',
                reason_code='RECORD_BYTE_LIMIT',limit_scope='record_bytes',limit_value=262144,original_bytes=300000)
        doc=recorder.finish(task_id='one',status='DONE',main_duration=1)
    r=doc['contents'][0]
    assert r['reason_code']=='RECORD_BYTE_LIMIT'
    assert (r['limit_scope'],r['limit_value'],r['original_bytes'])==('record_bytes',262144,300000)


def test_aggregate_bound_includes_array_overhead(monkeypatch):
    import reproagent.observability_content as module
    def add(store,text): store.capture(owner_span_id='a'*16,kind='wire_request',source='wire_request',value=text)
    sample=ContentStore();add(sample,'a'*100)
    cap=2*len(json.dumps(sample.records()[0],ensure_ascii=False).encode())
    monkeypatch.setattr(module,'MAX_CONTENTS_BYTES',cap)
    store=ContentStore();add(store,'a'*100);add(store,'b'*100)
    assert len(json.dumps(store.records(),ensure_ascii=False).encode())<=cap
    assert not store.complete and store.issues()[-1]['code']=='CONTENT_TOTAL_BYTE_LIMIT'


def test_known_cancel_event_has_explicit_dimension(tmp_path,projects,facts):
    with traced(tmp_path,projects,facts) as env:
        env.context.cancel_event.set()
        with pytest.raises(BudgetStopped) as stopped: env.runtime.middleware.before_model_call()
    assert stopped.value.reason=='CANCELLED' and stopped.value.dimension=='cancelled'
    assert BudgetStopped('CANCELLED').dimension is None


def test_diagnostic_rows_show_revision_transition_and_call_group():
    d=fixture(2);root=d['root_span_id']
    d['spans'].append(node(f'point:{root}:2','contract.revised',root,kind='point',contract_version=2,previous_contract_version=1))
    html=render_trace(d)
    assert 'v1 → v2' in html and '调用 call' in html
    assert '工具权限' in html and '契约变更' in html
