"""An admitted logical call, a wire request and model compliance are distinct facts."""
import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from reproagent.core.models import BudgetLimits
from reproagent.observability import TraceRecorder, write_trace
from reproagent.trace_rendering import write_trace_html
from tests.integration.test_agentscope_runtime import (
    agent_context, environment, events, tool_reply, wire_progress, write_payload,
)


def test_convergence_is_recorded_once_per_task(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts, limits=BudgetLimits(agent_steps=6), answers=[
        tool_reply('request_information', {'question': 'First missing fact'}, call_id='first'),
        tool_reply('request_information', {'question': 'Second missing fact'}, call_id='second')])
    for _ in range(4):
        env.context.budget.take_step()
    async def run():
        try:
            assert (await env.runtime.explore(agent_context(env))).kind == 'request_information'
            env.service.bind_contract(replace(env.service.contract, version=2))
            return await env.runtime.explore(agent_context(env))
        finally:
            await env.runtime.aclose()
            await env.factory.aclose()
    assert asyncio.run(run()).kind == 'request_information'
    transitions = events(env, 'budget.convergence')
    assert len(transitions) == 1
    assert transitions[0]['budget']['used_steps'] == 4
    steps = events(env, 'exploration.step')
    assert [step['steps_used_before_call'] for step in steps] == [4, 5]
    assert all(step['remaining_steps_before_call'] - step['steps_remaining'] == 1 for step in steps)
    assert all(step['budget_mode'] == 'converging' for step in steps)


def test_progress_trace_correlates_with_wire_request(tmp_path, projects, facts):
    with TraceRecorder() as recorder:
        env = environment(tmp_path, projects, facts, limits=BudgetLimits(agent_steps=1), answers=[
            httpx.Response(503, json={'error': {'message': 'busy'}}),
            httpx.Response(503, json={'error': {'message': 'busy'}}),
            tool_reply('write_candidate', write_payload(), call_id='publish')])
        async def run():
            try:
                return await env.runtime.explore(agent_context(env))
            finally:
                await env.runtime.aclose()
                await env.factory.aclose()
        assert asyncio.run(run()).kind == 'candidate'
        doc = recorder.finish(task_id='offline-convergence', status='DONE', main_duration=1.0)
    by_id = {span['span_id']: span for span in doc['spans']}
    progress = [span for span in doc['spans'] if span['name'] == 'budget.progress']
    assert len(progress) == 1
    assert len([span for span in doc['spans'] if span['name'] == 'budget.convergence']) == 1
    wires = [item for item in doc['contents'] if item['source'] == 'wire_request']
    # Identical retry bodies are content-deduplicated, while all three HTTP leaves remain.
    assert len(wires) == 1 and len(env.requests) == 3
    attempts = [span for span in doc['spans'] if span['name'] == 'model.http_attempt']
    assert len(attempts) == 3
    assert all(wires[0]['content_id'] in span['content_refs'] for span in attempts)
    for wire in wires:
        data = wire_progress(json.loads(wire['text']))
        assert data['budget'] == progress[0]['attributes']['budget']
        ancestors = set()
        span = by_id[wire['owner_span_id']]
        while span.get('parent_span_id') in by_id:
            ancestors.add(span['parent_span_id'])
            span = by_id[span['parent_span_id']]
        assert progress[0]['parent_span_id'] in ancestors
    assert env.context.budget.steps_used == 1
    # Use the product writers (including containment/long-path handling), not custom HTML.
    assert write_trace(env.task, doc).exists()
    assert write_trace_html(env.task, doc).exists()


@pytest.mark.parametrize('traced', [False, True])
@pytest.mark.parametrize('fault', ['event', 'trace'])
def test_progress_observation_fault_does_not_change_phase_result(tmp_path, projects, facts, monkeypatch, traced, fault):
    from contextlib import nullcontext
    from reproagent.adapters.agentscope import middleware
    with TraceRecorder() if traced else nullcontext():
        env = environment(tmp_path, projects, facts, limits=BudgetLimits(agent_steps=1), answers=[
            tool_reply('write_candidate', write_payload())])
        if fault == 'event':
            original = env.store.append_event
            def append(kind, *args, **kwargs):
                if kind in ('exploration.step', 'budget.convergence'):
                    raise OSError('offline observation fault')
                return original(kind, *args, **kwargs)
            monkeypatch.setattr(env.store, 'append_event', append)
        else:
            original = middleware.mark
            def mark(name, **kwargs):
                if name.startswith('budget.'):
                    raise RuntimeError('offline observation fault')
                return original(name, **kwargs)
            monkeypatch.setattr(middleware, 'mark', mark)
        async def run():
            try:
                return await env.runtime.explore(agent_context(env))
            finally:
                await env.runtime.aclose()
                await env.factory.aclose()
        result = asyncio.run(run())
    assert result.kind == 'candidate'
    assert env.store.load_record('candidates', result.candidate_id).candidate_id == result.candidate_id
    assert env.context.budget.steps_used == 1 and len(env.requests) == 1
    assert [e['result_code'] for e in events(env, 'exploration.action')] == ['ALLOWED']
