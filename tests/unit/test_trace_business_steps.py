"""Business steps reflect retained spans, never guessed lifecycle history."""
import copy
import pytest
from tests.unit.test_trace_view_model import fixture, node
from reproagent.trace_view_model import build_trace_view
from reproagent.trace_rendering import render_trace


def complete_document():
    d=fixture(2)
    d['spans'][0]['attributes']['purpose']='business_steps_v1'
    d['capture_health']={'structure_complete':True,'counts_complete':True,'content_complete':True,'issues':[]}
    return d


def add(d,name,parent=None,offset=0,duration=1,**attrs):
    ident=f"{len(d['spans'])+10:016x}"
    n=node(ident,name,parent or d['root_span_id'],**attrs)
    n.update(offset_seconds=offset,duration_seconds=duration)
    d['spans'].append(n)
    return ident


def test_loop_and_replay_have_actual_order_and_original_node_targets():
    d=complete_document()
    add(d,'prepare',offset=0)
    add(d,'analyze',offset=1)
    add(d,'explore',offset=2)
    add(d,'run_candidate',offset=3,candidate_id='c1')
    v=add(d,'verify',offset=4)
    add(d,'verdict',parent=v,offset=4,result_code='NOT_REPRODUCED')
    add(d,'explore',offset=5)
    add(d,'run_candidate',offset=6,candidate_id='c2')
    v=add(d,'verify',offset=7)
    add(d,'verdict',parent=v,offset=7,result_code='REPRODUCED')
    replay=add(d,'replay',offset=8,duration=3,candidate_id='c2')
    add(d,'runner.execute',parent=replay,offset=8,execution_role='original')
    add(d,'verify',parent=replay,offset=9)
    add(d,'runner.prepare',parent=replay,offset=10)
    add(d,'runner.execute',parent=replay,offset=10,execution_role='fixed')
    add(d,'action.completed',parent=replay,offset=10,purpose='submit_candidate',result_code='OK')
    add(d,'export',offset=11)
    before=copy.deepcopy(d)
    view=build_trace_view(d)
    b=view['business_steps']
    assert [o['step'] for o in b['occurrences']]==[1,2,3,4,5,3,4,5,6,7]
    assert [o['ordinal'] for o in b['occurrences'] if o['step']==3]==[1,2]
    assert [o['status'] for o in b['occurrences'] if o['step']==5]==['未通过','通过']
    r=next(o for o in b['occurrences'] if o['step']==6)
    assert r['duration_seconds']==3 and r['status']=='已确认'
    assert view['nodes'][r['node_key']]['span_id']==replay
    assert d==before
    html=render_trace(d)
    assert 'id="business-steps"' in html and 'data-business-node=' in html
    assert 'Step 7' in html and html.count('unique body')==1
    assert 'class="business-step"><strong><a data-business-node=' in html
    assert '最近：通过' in html


def test_missing_steps_only_mean_not_executed_when_coverage_is_known_complete():
    d=complete_document();add(d,'prepare');add(d,'analyze');add(d,'explore')
    d['status']='EXHAUSTED';add(d,'export')
    view=build_trace_view(d)['business_steps']
    assert [s['status'] for s in view['steps']][3:6]==['未执行']*3
    d['capture_health']['structure_complete']=False
    assert all(s['status']=='未记录' for s in build_trace_view(d)['business_steps']['steps'][3:6])
    d['capture_health']['structure_complete']=True;d['status']='RUNNING'
    assert all(s['status']=='未记录' for s in build_trace_view(d)['business_steps']['steps'][3:6])


def test_old_original_runs_do_not_fabricate_replay_or_claim_completion():
    d=fixture(1)
    add(d,'runner.execute',execution_role='original')
    add(d,'runner.execute',execution_role='original',offset=1)
    b=build_trace_view(d)['business_steps']
    assert [o['step'] for o in b['occurrences']]==[4,4]
    assert all(o['variant']=='旧记录：执行轮次未知' for o in b['occurrences'])
    assert b['steps'][5]['status']=='未记录'


def test_revision_and_cancellation_show_actual_outcome_not_span_ok():
    d=complete_document()
    revision=add(d,'revise_contract',contract_version=1)
    add(d,'action.rejected',parent=revision,purpose='revise_contract',result_code='CITATION_NOT_DISPLAYED')
    exp=add(d,'explore',offset=1)
    d['spans'][-1]['status']='cancelled'
    b=build_trace_view(d)['business_steps']
    assert b['occurrences'][0]['status']=='被拒绝'
    assert b['occurrences'][0]['result']=='CITATION_NOT_DISPLAYED'
    assert b['occurrences'][1]['status']=='已取消'
    assert b['occurrences'][1]['node_key']==next(i for i,s in enumerate(d['spans']) if s['span_id']==exp)


def test_missing_parent_does_not_guess_a_runner_is_the_replay():
    d=complete_document();d['partial']=True;add(d,'runner.execute',parent='f'*16,execution_role='original')
    b=build_trace_view(d)['business_steps']
    assert not any(o['step']==6 for o in b['occurrences'])
    assert b['steps'][5]['status']=='未记录'


def test_saved_contract_versions_are_shown_from_actual_child_event():
    d=complete_document()
    a=add(d,'analyze')
    add(d,'contract.established',parent=a,contract_version=1)
    revision=add(d,'revise_contract',offset=1,contract_version=1)
    add(d,'contract.revised',parent=revision,offset=1,previous_contract_version=1,contract_version=2)
    add(d,'action.completed',parent=revision,offset=1,purpose='revise_contract',result_code='OK')
    b=build_trace_view(d)['business_steps']
    assert b['occurrences'][0]['contract_version']==1
    assert b['occurrences'][1]['contract_version']==2
    assert b['occurrences'][1]['previous_contract_version']==1
    assert '契约 v1 → v2' in render_trace(d)


@pytest.mark.parametrize('bad',[['REPRODUCED'],{'classification':'REPRODUCED'}])
def test_unexpected_verdict_types_are_unknown_and_do_not_break_the_report(bad):
    d=complete_document();v=add(d,'verify');add(d,'verdict',parent=v,result_code=bad)
    before=copy.deepcopy(d)
    from reproagent.trace_rendering import validate_trace
    assert validate_trace(d)==d
    view=build_trace_view(d)
    assert view['business_steps']['occurrences'][0]['status']=='未确认'
    assert view['business_steps']['occurrences'][0]['result'] is None
    assert view['nodes'][-1]['attributes']['result_code']==bad
    assert d==before and '未确认' in render_trace(d)


@pytest.mark.parametrize('bad',[['DONE'],{'state':'DONE'}])
def test_unexpected_task_status_never_claims_missing_stages_were_skipped(bad):
    d=complete_document();d['status']=bad
    b=build_trace_view(d)['business_steps']
    assert not b['coverage_complete']
    assert all(s['status']=='未记录' for s in b['steps'])
