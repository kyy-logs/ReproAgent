import asyncio
import json
from types import SimpleNamespace
import pytest
from reproagent.core.models import TaskState
from tests.unit.test_controller import setup,publish,ask


def emit_pass(monkeypatch, text, when="call", outcome="passed"):
    from reproagent.adapters.languages.python_pytest.probe import reproagent_pytest_probe as probe
    rows=[]
    monkeypatch.setattr(probe,"emit",lambda event,payload:rows.append(payload))
    report=SimpleNamespace(longrepr=None,failed=outcome=="failed",nodeid="test_x.py::test_one",when=when,outcome=outcome,capstdout=text,capstderr="err")
    probe.pytest_runtest_logreport(report)
    return rows[0]


def test_passing_call_preserves_captured_output(monkeypatch):
    row=emit_pass(monkeypatch,"OBSERVED_DIAGNOSTIC")
    assert row["captured_output"]["stdout"]["text"]=="OBSERVED_DIAGNOSTIC"
    assert row["captured_output"]["stdout"]["truncated"] is False


def test_large_unicode_output_has_explicit_bounded_head_and_tail(monkeypatch):
    text="HEAD"+"中文"*5000+"TAIL"
    out=emit_pass(monkeypatch,text)["captured_output"]["stdout"]
    assert len(out["text"].encode("utf-8"))<=4096
    assert out["text"].startswith("HEAD") and out["text"].endswith("TAIL")
    assert out["truncated"] and out["original_bytes"]==len(text.encode("utf-8"))
    assert out["captured_bytes"]==len(out["text"].encode("utf-8"))


@pytest.mark.parametrize("when,outcome",[("setup","passed"),("teardown","passed"),("call","failed")])
def test_other_phases_do_not_duplicate_passing_capture(monkeypatch,when,outcome):
    assert "captured_output" not in emit_pass(monkeypatch,"x",when,outcome)


def test_real_original_pass_output_reaches_next_phase_and_is_redacted(tmp_path,projects,facts):
    text="from example.parser import parse\ndef test_one():\n    print('OBSERVED_DIAGNOSTIC secret-value')\n    assert parse([1]) == [1]\n"
    request,model,controller,ctx=setup(tmp_path,projects,facts,plan=[publish(content=text),ask()])
    controller.secrets=("secret-value",)
    result=asyncio.run(controller.run(request,ctx))
    assert result.status==TaskState.NEEDS_INFORMATION
    raw=controller.explorer.contexts[1].feedback
    data=json.loads(raw)
    measurements=data["verdict"]["issues"][0]["measurements"]
    outputs=[json.loads(item) for item in measurements["passing_outputs"]]
    assert any("OBSERVED_DIAGNOSTIC [REDACTED]" in item["stdout"]["text"] for item in outputs)
    assert "secret-value" not in raw and len(raw.encode("utf-8"))<=8192
    assert outputs[0]["run_id"]==data["verdict"]["run_ids"][0]
    assert outputs[0]["evidence_ref"]["content_hash"]
    assert model.kinds==["contract"]


def saved_run(tmp_path, monkeypatch, text="observed output"):
    from reproagent.core.serialization import bytes_hash
    from reproagent.core.models import EvidenceRef
    payload=emit_pass(monkeypatch,text)
    path=tmp_path/"probe.jsonl"
    content=json.dumps({"run_id":"run-one","event":"test_phase","payload":payload},ensure_ascii=False).encode()+b"\n"
    path.write_bytes(content)
    ref={"path":"probe.jsonl","content_hash":bytes_hash(content),"start_line":1,"end_line":1}
    phase={**payload,"captured_output_ref":ref}
    run=SimpleNamespace(run_id="run-one",execution_role="original",raw=SimpleNamespace(probe_artifacts=SimpleNamespace(path=path)),observation=SimpleNamespace(tests=(phase,)))
    def read(reference):
        assert isinstance(reference,EvidenceRef)
        data=(tmp_path/reference.path).read_bytes()
        if bytes_hash(data)!=reference.content_hash:raise ValueError("hash differs")
        return data.decode()
    return run,read,path


def test_passing_output_is_verified_against_saved_probe_line(tmp_path,monkeypatch):
    from reproagent.core.passing_output import passing_outputs
    run,read,path=saved_run(tmp_path,monkeypatch)
    rows,refs,omitted=passing_outputs(run,read,tmp_path)
    assert len(rows)==len(refs)==1 and omitted==0
    assert json.loads(rows[0])["stdout"]["text"]=="observed output"
    path.write_text("tampered",encoding="utf-8")
    assert passing_outputs(run,read,tmp_path)==((),(),1)


def test_forged_observation_text_is_not_used_even_with_real_probe_ref(tmp_path,monkeypatch):
    from reproagent.core.passing_output import passing_outputs
    run,read,path=saved_run(tmp_path,monkeypatch)
    run.observation.tests[0]["captured_output"]["stdout"]["text"]="forged"
    assert passing_outputs(run,read,tmp_path)==((),(),1)


def test_fixed_version_output_never_reaches_exploration(tmp_path,monkeypatch):
    from reproagent.core.passing_output import passing_outputs
    run,read,path=saved_run(tmp_path,monkeypatch)
    run.execution_role="fixed"
    def forbidden(ref):raise AssertionError("must not read fixed output")
    assert passing_outputs(run,forbidden,tmp_path)==((),(),0)


def test_old_observation_without_capture_is_not_fabricated(tmp_path,monkeypatch):
    from reproagent.core.passing_output import passing_outputs
    run,read,path=saved_run(tmp_path,monkeypatch)
    del run.observation.tests[0]["captured_output"]
    assert passing_outputs(run,read,tmp_path)==((),(),0)


def test_capacity_omission_counts_passing_output_records():
    from reproagent.core.models import Verdict,VerificationIssue
    from reproagent.core.verification_feedback import render_verification_feedback
    item=VerificationIssue("CANDIDATE_NOT_REPRODUCED","candidate_reproduced","program_check","passes",measurements={"passing_outputs":[json.dumps({"stdout":"x"*5000})],"omitted_outputs":0},recovery="revise_candidate")
    verdict=Verdict(None,"passes",issues=(item,),run_ids=("run-one",))
    data=json.loads(render_verification_feedback(verdict,result_code="NOT_REPRODUCED",byte_limit=2048))
    m=data["verdict"]["issues"][0]["measurements"]
    assert "passing_outputs" not in m and m["omitted_outputs"]==1
    assert data["verdict"]["run_ids"]==["run-one"]


@pytest.mark.parametrize("field,value",[("text",42),("original_bytes",True),("original_bytes",-1),("captured_bytes",99999),("truncated","no"),("excerpt_mode","invented")])
def test_malformed_captured_stream_is_optional_not_a_task_crash(tmp_path,monkeypatch,field,value):
    from reproagent.core.passing_output import passing_outputs
    from reproagent.core.serialization import bytes_hash
    run,read,path=saved_run(tmp_path,monkeypatch)
    phase=run.observation.tests[0]
    phase["captured_output"]["stdout"][field]=value
    payload={k:v for k,v in phase.items() if k!="captured_output_ref"}
    content=json.dumps({"run_id":"run-one","event":"test_phase","payload":payload},ensure_ascii=False).encode()+b"\n"
    path.write_bytes(content);phase["captured_output_ref"]["content_hash"]=bytes_hash(content)
    assert passing_outputs(run,read,tmp_path)==((),(),1)


@pytest.mark.parametrize("separator",["\u2028","\u0085"])
def test_unicode_stdout_separators_are_not_jsonl_record_boundaries(tmp_path,monkeypatch,separator):
    from reproagent.core.passing_output import passing_outputs
    text="first"+separator+"last"
    run,read,path=saved_run(tmp_path,monkeypatch,text)
    rows,refs,omitted=passing_outputs(run,read,tmp_path)
    assert len(rows)==1 and omitted==0
    assert json.loads(rows[0])["stdout"]["text"]==text


@pytest.mark.parametrize("secret",['key"with\\escapes',"line\nsecret"])
def test_json_encoded_observation_does_not_bypass_secret_redaction(secret):
    from reproagent.core.models import Verdict,VerificationIssue
    from reproagent.core.verification_feedback import render_verification_feedback
    encoded=json.dumps({"stdout":{"text":secret}},ensure_ascii=False)
    issue=VerificationIssue("CANDIDATE_NOT_REPRODUCED","candidate_reproduced","program_check","passes",measurements={"passing_outputs":[encoded],"omitted_outputs":0},recovery="revise_candidate")
    data=json.loads(render_verification_feedback(Verdict(None,"passes",issues=(issue,)),result_code="NOT_REPRODUCED",byte_limit=8192,secrets=(secret,)))
    row=json.loads(data["verdict"]["issues"][0]["measurements"]["passing_outputs"][0])
    assert row["stdout"]["text"]=="[REDACTED]"
    assert row["redacted"] is True
