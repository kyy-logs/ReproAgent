import asyncio
import pytest
from reproagent.core.failure_signature import stable_failure_message
from reproagent.core.models import TaskState
from tests.unit.test_controller import setup,publish,ask


def generator_message(address=123,name="test_one"):
    return "assert False\n + where False = any(<generator object "+name+".<locals>.<genexpr> at "+hex(address)+">)"


def test_observed_generator_identity_is_normalized():
    assert stable_failure_message(generator_message(123),(123,))==stable_failure_message(generator_message(456),(456,))


@pytest.mark.parametrize("ids",[(),(999,)])
def test_unobserved_generator_identity_stays_literal(ids):
    text=generator_message(123)
    assert stable_failure_message(text,ids)==text


def test_quoted_generator_display_and_business_timing_stay_literal():
    text="expected '"+generator_message(123).split("any(")[1][:-1]+"' in 0.20 seconds"
    assert stable_failure_message(text,(123,))==text
    assert stable_failure_message(generator_message(123)+" duration=0.20s",(123,))!=stable_failure_message(generator_message(456)+" duration=0.21s",(456,))


def test_different_generator_function_names_stay_different():
    assert stable_failure_message(generator_message(123,"a"),(123,))!=stable_failure_message(generator_message(456,"b"),(456,))


def test_two_real_rewritten_assertions_confirm_without_address_noise(tmp_path,projects,facts):
    # The real fixture bug raises IndexError for empty input instead of returning [].
    # Catching that reported exception yields a false source-backed [] predicate.
    text="from example.parser import parse\ndef test_empty():\n    try:\n        value=parse([])\n    except IndexError:\n        value=None\n    assert any(item == [] for item in [value])\n"
    request,model,controller,ctx=setup(tmp_path,projects,facts,plan=[publish(content=text),ask()])
    result=asyncio.run(controller.run(request,ctx))
    assert result.status==TaskState.DONE
    runs=[controller.store.load_record('runs',p.parent.name) for p in (request.output_dir/'runs').glob('*/execution.json')]
    assert len(runs)==2
    for run in runs:
        phase=next(p for p in run.observation.tests if p['outcome']=='failed')
        assert phase['exception_type']=='builtins.AssertionError'
        assert phase['assertion_context_object_ids'] and len(phase['assertion_context_object_ids'])<=64
        assert not phase.get('assertion_comparison')
    assert model.kinds==['contract','verdict','verdict']



def test_no_rewrite_temporaries_means_no_observed_objects():
    from types import SimpleNamespace
    from reproagent.adapters.languages.python_pytest.probe.reproagent_pytest_probe import assertion_context_ids
    value=object()
    frame=SimpleNamespace(f_locals={"ordinary":value},f_code=SimpleNamespace(co_varnames=("ordinary",)))
    assert assertion_context_ids(frame)==[]


def test_only_bounded_rewrite_values_are_observed_without_repr_or_iteration():
    from types import SimpleNamespace
    from reproagent.adapters.languages.python_pytest.probe.reproagent_pytest_probe import assertion_context_ids
    class Untouched:
        def __repr__(self):raise AssertionError("must not repr")
        def __iter__(self):raise AssertionError("must not iterate")
    value=Untouched()
    frame=SimpleNamespace(f_locals={"@py_assert0":value},f_code=SimpleNamespace(co_varnames=("@py_assert0",)))
    ids=assertion_context_ids(frame)
    assert id(value) in ids and len(ids)<=64
    frame.f_code.co_varnames=tuple("local"+str(i) for i in range(256))+("@py_assert0",)
    assert assertion_context_ids(frame)==[]


@pytest.mark.parametrize("bad",[None,42,[True],[-1],list(range(1,66))])
def test_malformed_context_identity_metadata_is_not_accepted(tmp_path,bad):
    import json
    from tests.integration.test_pytest_probe import run_probe
    from reproagent.adapters.languages.python_pytest.collector import read_probe
    observation,path=run_probe(tmp_path,'def test_bad(): assert False\n')
    rows=[json.loads(line) for line in path.read_text(encoding='utf-8').split('\n') if line]
    phase=next(row['payload'] for row in rows if row['event']=='test_phase' and row['payload']['when']=='call')
    phase['assertion_context_object_ids']=bad
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
    result=read_probe(path,'run-1')
    assert not result.probe_complete
    assert any('assertion context' in message for message in result.framework_details['errors'])
