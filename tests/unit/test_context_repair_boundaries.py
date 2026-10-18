import asyncio
import json
import pytest
from dataclasses import replace

from reproagent.core.models import CandidateClass, IssueContract
from reproagent.core.serialization import canonical_bytes
from reproagent.store import TaskStore
from tests.unit.test_verifier import EvidenceModel, prepare


WARNING_TEST = ('import warnings\nfrom example.parser import parse\n'
                'def test_empty():\n'
                '    warnings.warn("candidate constructor warning", DeprecationWarning)\n'
                '    assert parse([]) == []\n')


def preload_target(projects):
    original=projects.plain
    def plain(root):
        repo=original(root)
        (repo/'tests/conftest.py').write_text('import example.parser\n',encoding='utf-8')
        return repo
    projects.plain=plain


def test_pre_target_candidate_warning_is_revisable_without_semantic_call(tmp_path,projects,facts):
    preload_target(projects)
    # The real pytest runner must turn the warning into an exception.
    text='import pytest\npytestmark=pytest.mark.filterwarnings("error")\n'+WARNING_TEST
    contract,candidate,run,verifier,model,*_=prepare(tmp_path,projects,facts,text=text)
    assert run.observation.framework_details['target_calls']=={}
    assert any(t.get('exception_type')=='builtins.DeprecationWarning' for t in run.observation.tests)
    verdict=asyncio.run(verifier.evaluate(contract,candidate,(run,),facts.context()))
    assert verdict.classification==CandidateClass.INVALID_CANDIDATE
    assert verdict.issues[0].code=='CANDIDATE_PRE_TARGET_WARNING'
    assert verdict.issues[0].origin=='program_check' and verdict.issues[0].recovery=='revise_candidate'
    assert not model.calls


@pytest.mark.parametrize('reported,checks',[('DeprecationWarning on the reported input',()),('初始化时出现弃用警告',()),('API raises a deprecation exception',()),('reported input fails',('The operation must not emit DeprecationWarning',))])
def test_warning_reported_by_issue_still_requires_semantic_review(tmp_path,projects,facts,reported,checks):
    preload_target(projects)
    text='import pytest\npytestmark=pytest.mark.filterwarnings("error")\n'+WARNING_TEST
    contract,candidate,run,verifier,model,*_=prepare(tmp_path,projects,facts,text=text)
    contract=replace(contract,reported_actual=reported,observable_checks=checks)
    verdict=asyncio.run(verifier.evaluate(contract,candidate,(run,),facts.context()))
    assert model.calls
    assert all(i.code!='CANDIDATE_PRE_TARGET_WARNING' for i in verdict.issues)


def test_warning_after_target_call_does_not_get_setup_classification(tmp_path,projects,facts):
    text=('import pytest\nimport warnings\nfrom example.parser import parse\n'
          'pytestmark=pytest.mark.filterwarnings("error")\n'
          'def test_empty():\n    parse([1])\n    warnings.warn("late",DeprecationWarning)\n')
    contract,candidate,run,verifier,model,*_=prepare(tmp_path,projects,facts,text=text)
    assert run.observation.framework_details['target_calls']
    verdict=asyncio.run(verifier.evaluate(contract,candidate,(run,),facts.context()))
    assert model.calls
    assert all(i.code!='CANDIDATE_PRE_TARGET_WARNING' for i in verdict.issues)


def test_current_state_pairs_expectation_with_nonbinding_caveats(tmp_path):
    store=TaskStore(tmp_path)
    caveat='Column title and full hostname are illustrative, not adopted requirements.'
    store.save_contract(IssueContract('c1',expected='Output distinguishes route subdomains',
        assumptions=(caveat,),observable_checks=('Each route has distinguishable subdomain information',)))
    data=store.progress.view()
    assert data['contract']['assumptions']==[caveat]
    assert data['contract']['observable_checks']==['Each route has distinguishable subdomain information']
    assert len(canonical_bytes({'task_state':data}))<=4096


def test_long_caveats_remain_bounded_and_excerpts_are_marked(tmp_path):
    store=TaskStore(tmp_path)
    store.save_contract(IssueContract('c1',expected='预期行为'*120,
        assumptions=tuple('未确认的展示格式'+str(i)+'示例'*100 for i in range(4)),
        observable_checks=('能够区分路由所属子域'*80,)))
    view=store.progress.view()
    assert view['contract']['assumptions'] and view['contract']['observable_checks']
    assert 'assumptions' in view['contract']['excerpted_fields']
    assert len(canonical_bytes({'task_state':view}))<=4096


def test_ascii_expectation_truncation_is_never_silent(tmp_path):
    store=TaskStore(tmp_path)
    store.save_contract(IssueContract('c1',expected='a'*513))
    view=store.progress.view()
    assert 'expected' in view['contract']['excerpted_fields']
    assert len(canonical_bytes({'task_state':view}))<=4096


def test_list_excerpt_budget_includes_json_escaping(tmp_path):
    store=TaskStore(tmp_path)
    store.save_contract(IssueContract('c1',expected='behavior',
        assumptions=tuple(('"\\\n'*100)+str(i) for i in range(8))))
    view=store.progress.view()
    assert len(canonical_bytes(view['contract']['assumptions']))<=512
    assert len(view['contract']['assumptions'])==8
    assert 'assumptions' in view['contract']['excerpted_fields']
