"""The trace follows the whole task without changing any of it.

A trace is only worth having if switching it on cannot move the reproduction: the same
status, the same evidence level, the same candidate bytes, the same model calls and the
same steps.  These tests run the real SDK task (and the real pytest runs inside it)
twice over, once with the recorder installed and once without, and compare what the
task concluded.  The trace also has to survive the awkward endings -- cancellation, a
failed write -- without rewriting the sealed result.
"""
import asyncio
import importlib
import json
from dataclasses import replace

import httpx
import pytest

from reproagent.app import create_controller
from reproagent.core.models import ModelConfig, ModelRequest, TaskState
from reproagent.observability import trace_session, write_trace
from tests.integration.test_agentscope_backends import CANDIDATE, text_response, tool_response
from tests.unit.test_controller import ScriptedModel, setup


def run_sdk_task(tmp_path, projects, facts, *, trace=False, seed=True):
    """One real SDK task with learning, optionally under a recorder.

    The handler is the one the experience lifecycle tests use: an exploration phase
    publishes the regression candidate, contract and verdict come from the scripted
    domain model, and the learning call answers with one card.
    """
    request, _, _, ctx = setup(tmp_path, projects, facts)
    path = tmp_path / "shared-experiences.json"
    request = replace(request, experience_file=path, learn_experience=True)
    script = ScriptedModel()
    seen, main_calls = [], []
    read = False

    async def handle(http_request):
        nonlocal read
        payload = json.loads(http_request.content)
        seen.append(payload)
        data = json.loads(payload["messages"][-1]["content"]) if not payload.get("tools") else None
        if data is not None and "evidence" in data and "task_status" in data:
            return text_response(json.dumps({"experience": {
                "category": "model", "tags": ["pytest"],
                "summary": "Empty input assertions need original behaviour evidence.",
                "detail": "Condition: empty input. Observation: the original test raised IndexError.",
                "evidence_ids": [data["evidence"][0]["id"]]}}))
        if payload.get("tools"):
            main_calls.append("exploration")
            return tool_response("write_candidate", {"files": [{"path": "tests/test_repro.py",
                "content": CANDIDATE, "role": "test"}], "hypothesis": "empty input"}, "write")
        kind = "verdict" if "available_refs" in data else "contract"
        main_calls.append(kind)
        response = await script.complete(ModelRequest(tuple(payload["messages"]), kind), ctx)
        return text_response(response.text)

    model = ModelConfig(base_url="https://offline.example/v1", model="offline",
                        output_limit_field="max_tokens")
    controller = create_controller(request, model, transport=httpx.MockTransport(handle))

    async def main():
        with trace_session(enabled=trace) as recorder:
            result = await controller.run(request, ctx)
            return result, recorder

    result, recorder = asyncio.run(main())
    return request, controller, result, recorder, seen, main_calls


def document_for(request, controller, result, recorder):
    """The document the CLI would write, with the learning result it already has."""
    learning = None
    if getattr(controller, 'learning_result', None) is not None:
        from dataclasses import asdict
        learning = asdict(controller.learning_result)
    return recorder.finish(task_id=result.task_id, status=result.status.value,
                           main_duration=result.duration, learning=learning)


def candidate_bytes(request):
    """The candidate's own files, keyed by their path inside it.

    The stored manifest is deliberately left out: it carries the run's own candidate id
    and storage paths, which differ between two runs by construction.  What must not
    move is the source the task produced.
    """
    package = request.output_dir / 'candidates'
    produced = {}
    for item in sorted(package.rglob('*')):
        if not item.is_file():
            continue
        parts = item.relative_to(package).parts
        if 'files' not in parts:
            continue
        produced['/'.join(parts[parts.index('files') + 1:])] = item.read_bytes()
    return produced


def named(document, name):
    return [entry for entry in document['spans'] if entry['name'] == name]


def test_trace_on_off_preserves_domain_outcomes(tmp_path, projects, facts):
    plain = run_sdk_task(tmp_path / 'plain', projects, facts, trace=False)
    traced = run_sdk_task(tmp_path / 'traced', projects, facts, trace=True)

    plain_result, traced_result = plain[2], traced[2]

    # Same conclusion, same evidence, same package.
    assert traced_result.status is plain_result.status
    assert traced_result.evidence_level is plain_result.evidence_level
    assert traced_result.export_state == plain_result.export_state
    assert candidate_bytes(plain[0]) == candidate_bytes(traced[0])
    # Same work: the same model requests, in the same kinds, and the same exploration steps.
    assert traced[5] == plain[5]
    assert traced[0].limits is not None

    # And the traced run really recorded the lifecycle rather than nothing at all.
    document = document_for(traced[0], traced[1], traced_result, traced[3])
    assert named(document, 'task')
    assert named(document, 'runner.execute')
    assert named(document, 'verify')
    assert named(document, 'learn')


def test_main_and_learning_have_separate_budget_metrics(tmp_path, projects, facts):
    traced = run_sdk_task(tmp_path, projects, facts, trace=True)
    request, controller, result, recorder = traced[0], traced[1], traced[2], traced[3]
    document = document_for(request, controller, result, recorder)

    learning = named(document, 'learn')
    assert learning, 'the learning step has its own span'
    root = named(document, 'task')[0]

    # Learning runs inside the task scope but is not folded into the main duration.
    assert {entry['parent_span_id'] for entry in learning} == {root['span_id']}
    assert document['main_duration'] == result.duration
    assert document['total_duration'] >= document['main_duration']
    assert document['summary']['learning']['event_recorded'] is True


def test_trace_not_in_manifest_or_learning_material(tmp_path, projects, facts):
    traced = run_sdk_task(tmp_path, projects, facts, trace=True)
    request, controller, result, recorder, seen = traced[0], traced[1], traced[2], traced[3], traced[4]
    document = document_for(request, controller, result, recorder)
    write_trace(request.output_dir, document)

    package = request.output_dir / 'artifacts/reproduction'
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    listed = json.dumps(manifest)
    # The trace is a local diagnostic, never part of what the task delivers.
    assert 'observability' not in listed
    assert 'trace.json' not in listed

    # No learning request carried any of it either.
    assert document['trace_id'] not in json.dumps(seen)


def test_trace_write_failure_keeps_original_exit_code(tmp_path, projects, facts, monkeypatch):
    """A trace that cannot be written is a warning, never a rewritten task result."""
    import reproagent.cli as cli

    request, _, _, ctx = setup(tmp_path, projects, facts)
    result = asyncio.run(_finished_result(request, ctx))

    def explode(*_args, **_kwargs):
        raise OSError('trace destination unavailable')

    monkeypatch.setattr(cli, 'write_trace', explode, raising=False)
    code = cli.EXIT_CODES[result.status]

    written = cli._write_trace_or_warn(request.output_dir, {'schema_version': 1})
    assert written is False
    assert code == cli.EXIT_CODES[TaskState.DONE]


async def _finished_result(request, ctx):
    from reproagent.core.models import TaskResult
    return TaskResult(request.task_id or 'task-x', TaskState.DONE, duration=1.0)


def test_cancelled_task_closes_trace(tmp_path, projects, facts):
    """A cancelled task still closes its scope: the trace survives the ending."""
    from reproagent.core.models import BudgetLimits
    from tests.unit.test_controller import PhasePlan, fail, publish

    request, _, controller, ctx = setup(tmp_path, projects, facts, plan=[_then_cancel()])

    async def main():
        with trace_session(enabled=True) as recorder:
            result = await controller.run(request, ctx)
            return result, recorder

    result, recorder = asyncio.run(main())
    document = document_for(request, controller, result, recorder)

    assert result.status is TaskState.CANCELLED
    assert document['status'] == 'CANCELLED'
    assert named(document, 'task'), 'the root scope is still closed'
    assert controller._explorer_closed


def _then_cancel():
    """One phase that publishes nothing and reports the task as cancelled instead."""
    def step(explorer, context):
        raise asyncio.CancelledError()
    return step
