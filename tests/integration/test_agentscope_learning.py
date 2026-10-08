import asyncio
import json
import pytest
import httpx
from reproagent.core.budget import BudgetStopped
from reproagent.store import TaskStore
from tests.integration.test_agentscope_gateway import build_gateway, context, reply
from tests.unit.test_experience_store import exp


def empty_material():
    return exp().LearningInput({"task_status": "EXHAUSTED", "evidence": []}, {}, "task@scope", -1)


def test_learning_request_has_no_tools_and_own_deadline(tmp_path):
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        return reply('{"experience":null}', usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    store = TaskStore(tmp_path)
    gateway = build_gateway(handle, store)
    ctx = context(task_timeout_seconds=30)
    async def run():
        try:
            return await exp().extract_experience(empty_material(), gateway, ctx, store)
        finally:
            await gateway.aclose()
    assert asyncio.run(run()) is None
    assert len(calls) == 1 and "tools" not in calls[0]
    assert ctx.budget.steps_used == 0
    events, _ = store.read_events()
    attempts = [e.payload for e in events if e.kind == "model.attempt"]
    assert len(attempts) == 1 and attempts[0]["response_kind"] == "learning"
    assert attempts[0]["usage"]["total_tokens"] == 15


def test_learning_retry_is_not_protocol_correction(tmp_path):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(503) if len(calls) < 3 else reply('{"experience":null}')
    store = TaskStore(tmp_path)
    gateway = build_gateway(handle, store)
    async def run():
        try:
            return await exp().extract_experience(empty_material(), gateway, context(task_timeout_seconds=30), store)
        finally:
            await gateway.aclose()
    assert asyncio.run(run()) is None and len(calls) == 3
    bad_calls = []
    def bad(request):
        bad_calls.append(request)
        return reply('{"experience":null,"extra":true}')
    gateway = build_gateway(bad, store)
    async def invalid():
        try:
            with pytest.raises(ValueError):
                await exp().extract_experience(empty_material(), gateway, context(task_timeout_seconds=30), store)
        finally:
            await gateway.aclose()
    asyncio.run(invalid())
    assert len(bad_calls) == 1


@pytest.mark.parametrize("cancel", [False, True])
def test_learning_cancellation_and_timeout_close_clients(tmp_path, cancel):
    async def stalled(request):
        await asyncio.Event().wait()
    store = TaskStore(tmp_path)
    gateway = build_gateway(stalled, store)
    ctx = context(task_timeout_seconds=0.15 if not cancel else 30)
    async def run():
        if cancel:
            asyncio.get_running_loop().call_later(0.03, ctx.cancel_event.set)
        try:
            with pytest.raises(BudgetStopped):
                await exp().extract_experience(empty_material(), gateway, ctx, store)
        finally:
            await gateway.aclose()
        assert gateway.factory._clients == []
    asyncio.run(run())
