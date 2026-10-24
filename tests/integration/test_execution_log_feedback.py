"""A real pytest warning must reach both review and the next SDK wire."""
import asyncio
from dataclasses import replace
import json

import httpx

from reproagent.app import create_controller
from reproagent.core.models import ModelConfig, ModelRequest, TaskState
from tests.integration.test_agentscope_runtime import tool_reply
from tests.unit.test_controller import ScriptedModel, setup

WARNING = "WARNING: autodoc: failed to import class 'MissingInput'"
CANDIDATE = ("from example.parser import parse\n"
             "def test_missing_input():\n"
             "    print(" + repr(WARNING) + ")\n"
             "    assert parse([]) == []\n")


def test_original_log_warning_reaches_verifier_and_next_real_sdk_request(tmp_path, projects, facts):
    request, _, _, ctx = setup(tmp_path, projects, facts)
    script = ScriptedModel(classification="NOT_REPRODUCED")
    exploration, reviews = [], []

    async def handle(http_request):
        payload = json.loads(http_request.content)
        if payload.get("tools"):
            exploration.append(payload)
            if len(exploration) == 1:
                return tool_reply("write_candidate", {"files": [
                    {"path": "tests/test_repro.py", "content": CANDIDATE, "role": "test"}],
                    "hypothesis": "missing input prevents target observation"}, call_id="publish")
            return tool_reply("request_information", {"question": "check missing input"}, call_id="ask")
        data = json.loads(payload["messages"][-1]["content"])
        kind = "verdict" if "available_refs" in data else "contract"
        if kind == "verdict":
            reviews.append(data)
        reply = await script.complete(ModelRequest(tuple(payload["messages"]), kind), ctx)
        return httpx.Response(200, json={"id": "mock", "object": "chat.completion", "created": 1,
            "model": "offline", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": reply.text}}]})

    controller = create_controller(request, ModelConfig(base_url="https://offline.example/v1",
        model="offline", output_limit_field="max_tokens"), transport=httpx.MockTransport(handle))
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION and len(exploration) == 2
    assert len(reviews) == 1
    assert WARNING in json.dumps(reviews[0].get("execution_logs", []))
    # Supplementary log lines cannot be used as expectation/source references.
    assert all(not ref["path"].endswith(("stdout.log", "stderr.log"))
               for ref in reviews[0]["available_refs"])
    feedbacks = []
    for message in exploration[1]["messages"]:
        if message["role"] == "user" and isinstance(message["content"], str):
            try:
                data = json.loads(message["content"])
            except ValueError:
                continue
            if data.get("feedback"):
                feedbacks.append(json.loads(data["feedback"]))
    feedback = feedbacks[-1]
    assert WARNING in json.dumps(feedback.get("execution_logs", []))
    assert len(json.dumps(feedback, ensure_ascii=False, separators=(",", ":")).encode()) <= 8192
    for item in feedback["execution_logs"]:
        assert item["execution_role"] == "original"
        for signal in item["signals"]:
            path = request.output_dir / signal["path"]
            lines = path.read_text(encoding="utf-8").splitlines()
            assert signal["text"] == "\n".join(lines[signal["start_line"] - 1:signal["end_line"]])
    assert feedback["verdict"]["issues"][0]["code"] == "TARGET_TRIGGER_UNCONFIRMED"
    assert not result.accepted_candidate_id


def test_controller_does_not_project_unbound_or_fixed_run_logs(tmp_path, projects, facts, monkeypatch):
    from types import SimpleNamespace
    from reproagent.core.models import Verdict
    _, _, controller, ctx = setup(tmp_path, projects, facts)
    verdict = Verdict(None, "unconfirmed", candidate_id="current", contract_id="contract",
                      contract_version=1, manifest_hash="manifest", run_ids=("run-test",))
    run = SimpleNamespace(run_id="run-test", execution_role="fixed", candidate_id="current",
                          contract_id="contract", contract_version=1, manifest_hash="manifest")
    monkeypatch.setattr(controller.store, "load_record", lambda *a: run)
    monkeypatch.setattr(controller.verifier, "resolve",
                        lambda *a: (_ for _ in ()).throw(AssertionError("must not read fixed logs")))
    feedback = json.loads(controller.candidate_feedback(verdict, ctx, result_code="INVALID_CANDIDATE"))
    assert not feedback.get("execution_logs")
    run.execution_role = "original"
    run.candidate_id = "unrelated"
    feedback = json.loads(controller.candidate_feedback(verdict, ctx, result_code="INVALID_CANDIDATE"))
    assert not feedback.get("execution_logs")


def test_optional_log_envelope_cannot_overflow_the_fitting_core_review(tmp_path, projects, facts):
    from reproagent.core.models import EvidenceRef
    from reproagent.core.serialization import bytes_hash
    from reproagent.core.review_context import build_review_context
    from reproagent.store import safe_child
    from tests.unit.test_verifier import prepare
    contract, candidate, run, verifier, *_ = prepare(tmp_path, projects, facts)
    reader = lambda e: safe_child(candidate.storage_root, e.path).read_bytes()
    from reproagent.core.review_context import ReviewContextTooLarge
    try:
        build_review_context(contract, candidate, run, read_ref=verifier.resolve,
                             read_file=reader, max_bytes=1)
    except ReviewContextTooLarge as error:
        minimum_bytes = error.needed_bytes
    core = build_review_context(contract, candidate, run, read_ref=verifier.resolve,
                                read_file=reader, max_bytes=minimum_bytes)
    path = verifier.store.root / run.raw.stdout_ref.path
    path.write_bytes(b"WARNING: missing optional input\n")
    changed = replace(run, raw=replace(run.raw, stdout_ref=replace(
        run.raw.stdout_ref, content_hash=bytes_hash(path.read_bytes()))))
    projected = build_review_context(contract, candidate, changed, read_ref=verifier.resolve,
                                     read_file=reader, max_bytes=core.output_bytes)
    assert projected.output_bytes <= core.output_bytes
    assert "execution_logs" in projected.omitted_fields


def test_identical_checked_log_signals_preserve_semantic_replay_reuse(tmp_path, projects, facts):
    from tests.unit.test_verifier import prepare
    text = ("from example.parser import parse\n"
            "def test_empty():\n"
            "    print(" + repr(WARNING) + ")\n"
            "    assert parse([]) == []\n")
    contract, candidate, first, verifier, model, runner, snapshot, request = prepare(
        tmp_path, projects, facts, text=text)
    environment = verifier.store.load_record('environments', first.environment_id)
    second = asyncio.run(runner.execute(candidate, snapshot, environment, facts.context()))
    first_verdict = asyncio.run(verifier.evaluate(contract, candidate, (first,), facts.context()))
    second_verdict = asyncio.run(verifier.evaluate(contract, candidate, (second,), facts.context()))
    assert first_verdict.classification.value == second_verdict.classification.value == "REPRODUCED"
    assert len(model.calls) == 1
    assert verifier.confirm(contract, candidate, first, second, (first_verdict, second_verdict))


def test_redaction_expansion_sheds_logs_before_refusing_valid_core(tmp_path, projects, facts):
    from reproagent.core.models import BudgetLimits
    from reproagent.core.serialization import bytes_hash
    from reproagent.core.review_context import build_review_context
    from reproagent.store import safe_child
    from tests.unit.test_verifier import prepare
    contract, candidate, run, verifier, model, *_ = prepare(tmp_path, projects, facts)
    reader = lambda e: safe_child(candidate.storage_root, e.path).read_bytes()
    core = build_review_context(contract, candidate, run, read_ref=verifier.resolve,
                                read_file=reader, max_bytes=32768)
    path = verifier.store.root / run.raw.stdout_ref.path
    path.write_bytes(("WARNING: " + "abcd" * 200 + "\n").encode())
    changed = replace(run, raw=replace(run.raw, stdout_ref=replace(
        run.raw.stdout_ref, content_hash=bytes_hash(path.read_bytes()))))
    cap = core.output_bytes + 800
    verifier.secrets = ("abcd",)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (changed,),
        facts.context(limits=BudgetLimits(tool_response_bytes=cap))))
    assert verdict.classification.value == "REPRODUCED"
    sent = model.calls[-1].messages[-1]["content"]
    assert "abcd" not in sent and len(sent.encode()) <= cap
    assert not json.loads(sent).get("execution_logs", [{}])[0].get("signals")


def test_changed_log_signal_cannot_reuse_previous_semantic_approval(tmp_path, projects, facts):
    from tests.unit.test_verifier import prepare
    from reproagent.core.serialization import bytes_hash
    text = ("from example.parser import parse\n"
            "def test_empty():\n"
            "    print(" + repr(WARNING) + ")\n"
            "    assert parse([]) == []\n")
    contract, candidate, first, verifier, model, runner, snapshot, request = prepare(
        tmp_path, projects, facts, text=text)
    environment = verifier.store.load_record('environments', first.environment_id)
    second = asyncio.run(runner.execute(candidate, snapshot, environment, facts.context()))
    path = verifier.store.root / second.raw.stdout_ref.path
    path.write_bytes(path.read_bytes().replace(b"MissingInput", b"DifferentInput"))
    second = replace(second, raw=replace(second.raw, stdout_ref=replace(
        second.raw.stdout_ref, content_hash=bytes_hash(path.read_bytes()))))
    asyncio.run(verifier.evaluate(contract, candidate, (first,), facts.context()))
    asyncio.run(verifier.evaluate(contract, candidate, (second,), facts.context()))
    assert len(model.calls) == 2


def test_optional_logs_restore_original_core_when_dedup_cannot_shrink_it(tmp_path, projects, facts):
    from tests.unit.test_verifier import prepare
    from reproagent.core.review_context import build_review_context
    from reproagent.core.serialization import bytes_hash
    from reproagent.store import safe_child
    contract, candidate, run, verifier, *_ = prepare(tmp_path, projects, facts)
    tests = tuple({k: v for k, v in phase.items()
                   if k not in ("longrepr", "exception_frames", "crash")}
                  for phase in run.observation.tests)
    run = replace(run, observation=replace(run.observation, tests=tests))
    reader = lambda e: safe_child(candidate.storage_root, e.path).read_bytes()
    core = build_review_context(contract, candidate, run, read_ref=verifier.resolve,
                                read_file=reader, max_bytes=32768)
    path = verifier.store.root / run.raw.stdout_ref.path
    path.write_bytes(b"WARNING: optional diagnostic\n")
    changed = replace(run, raw=replace(run.raw, stdout_ref=replace(
        run.raw.stdout_ref, content_hash=bytes_hash(path.read_bytes()))))
    projected = build_review_context(contract, candidate, changed, read_ref=verifier.resolve,
                                     read_file=reader, max_bytes=core.output_bytes)
    assert projected.payload == core.payload
    assert projected.output_bytes == core.output_bytes
    assert "execution_logs" in projected.omitted_fields
