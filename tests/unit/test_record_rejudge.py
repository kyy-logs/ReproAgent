import pytest
from evals.swt_bench.io import read_json
from tests.unit.test_repair_attach import round_with


def test_confirmed_run_is_validated_recorded_and_leaves_official_result(tmp_path,monkeypatch):
    from evals.repairs import record_rejudge as module
    root,before=round_with(tmp_path)
    path=tmp_path/'diagnostic/receipt.json'
    calls=[]
    def run(*args,**kwargs):
        calls.append((args,kwargs));return read_json(path)
    monkeypatch.setattr(module,'run_rejudge',run)
    result=module.run_and_record(root,'logs','image',path.parent,timeout=60)
    assert result['recorded'] is True and result['official_effect']=='none'
    assert read_json(root/'round.json')['outcomes']['sphinx-doc__sphinx-8627']['official_resolved'] is False
    assert calls==[((root,'logs','image',path.parent),{'timeout':60})]


def test_inconclusive_run_is_retained_without_attachment(tmp_path,monkeypatch):
    from evals.repairs import record_rejudge as module
    root,before=round_with(tmp_path)
    monkeypatch.setattr(module,'run_rejudge',lambda *args,**kwargs:{'result':'inconclusive'})
    result=module.run_and_record(root,'logs','image',tmp_path/'unused')
    assert not result['recorded']
    assert read_json(root/'round.json')==before


@pytest.mark.parametrize('timeout',[0,-1,float('inf'),float('nan'),240.0,60.5,True,'bad'])
def test_invalid_deadline_cannot_start_environment_execution(monkeypatch,timeout):
    from evals.repairs import record_rejudge as module
    def forbidden(*args,**kwargs): raise AssertionError('must not execute')
    monkeypatch.setattr(module,'run_rejudge',forbidden)
    with pytest.raises(ValueError):module.run_and_record('round','logs','image','output',timeout=timeout)


def test_cli_timeout_is_an_integer_before_external_execution(monkeypatch):
    from evals.repairs import record_rejudge as module
    seen=[]
    def run(*args,**kwargs):
        seen.append(kwargs['timeout'])
        return {'result':'inconclusive'}
    monkeypatch.setattr(module,'run_rejudge',run)
    assert module.main(['--round','r','--logs','l','--image','i','--output','o','--timeout','240'])==1
    assert seen==[240] and type(seen[0]) is int
