"""One infrastructure, whichever backend a caller remembers asking for.

A default run and a run that still passes the old backend flags assemble the same SDK
gateway and the same SDK toolkit, so the same task reaches the same evidence under either
spelling; a legacy flag only warns.  Nothing here keeps a second runtime alive: the native
model path is gone, and a missing SDK is an install error rather than a fall back to it.
"""
import asyncio
import json
import subprocess
import sys

import httpx
import pytest

pytest.importorskip('agentscope')

from reproagent.adapters.agentscope.explorer import AgentScopeExplorer
from reproagent.adapters.agentscope.gateway import AgentScopeModelGateway
from reproagent.adapters.agentscope.tools import TOOL_NAMES
from reproagent.app import create_controller
from reproagent.core.models import INFRASTRUCTURE, FixValidationRequest, ModelConfig, ModelRequest, installed_agentscope_version
from tests.unit.test_controller import ScriptedModel, setup

CANDIDATE = 'from example.parser import parse\ndef test_empty(): assert parse([]) == []\n'

#: The default, and every spelling of the two flags the product used to offer.
LEGACY_RUNS = [{}, {'model_backend': 'native', 'agent_backend': 'native'},
               {'model_backend': 'agentscope', 'agent_backend': 'agentscope'},
               {'model_backend': 'native', 'agent_backend': 'agentscope'},
               {'model_backend': 'agentscope', 'agent_backend': 'native'}]


def scripted_transport(script, ctx, calls, toolkit):
    """One mock provider for the whole task: the SDK phase and the domain calls alike."""
    async def handle(http_request):
        payload = json.loads(http_request.content)
        if payload.get('tools'):
            # The exploration phase is a real SDK request with the phase toolkit attached.
            calls.append('exploration')
            toolkit.append([entry['function']['name'] for entry in payload['tools']])
            return httpx.Response(200, json={'id': 'mock', 'object': 'chat.completion', 'model': 'offline', 'created': 1,
                'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': 'call-1', 'type': 'function', 'function': {'name': 'write_candidate', 'arguments': json.dumps(
                        {'files': [{'path': 'tests/test_repro.py', 'content': CANDIDATE, 'role': 'test'}], 'hypothesis': 'empty input'})}}]}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})
        data = json.loads(payload['messages'][-1]['content'])
        kind = 'verdict' if 'available_refs' in data else 'contract'
        response = await script.complete(ModelRequest(tuple(payload['messages']), kind), ctx)
        calls.append(kind)
        return httpx.Response(200, json={'id': 'mock', 'object': 'chat.completion', 'model': 'offline', 'created': 1,
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': response.text}}]})
    return handle


@pytest.mark.parametrize('flags', LEGACY_RUNS, ids=['default', 'native/native', 'agentscope/agentscope',
                                                    'native/agentscope', 'agentscope/native'])
def test_default_and_legacy_flags_select_same_sdk_infrastructure(tmp_path, projects, facts, flags):
    request, _, _, ctx = setup(tmp_path, projects, facts)
    script, calls, toolkit = ScriptedModel(), [], []
    model = ModelConfig(base_url='https://offline.example/v1', model='offline', output_limit_field='max_tokens')
    if flags:
        with pytest.warns(DeprecationWarning):
            controller = create_controller(request, model, transport=httpx.MockTransport(scripted_transport(script, ctx, calls, toolkit)), **flags)
    else:
        controller = create_controller(request, model, transport=httpx.MockTransport(scripted_transport(script, ctx, calls, toolkit)))
    # Whatever flag was passed, this is the SDK gateway and the SDK phase runtime.
    assert isinstance(controller.gateway.gateway, AgentScopeModelGateway)
    fixed = projects.fixed(tmp_path / 'fixed-hidden')
    result = asyncio.run(controller.run(request, ctx, FixValidationRequest(fixed, sys.executable)))
    assert isinstance(controller.explorer, AgentScopeExplorer)
    assert result.status.value == 'DONE' and result.evidence_level.value == 'DIFFERENTIAL_VALIDATED', result
    assert calls == ['contract', 'exploration', 'verdict', 'verdict']
    assert toolkit == [list(TOOL_NAMES)]
    assert ctx.budget.steps_used == 1 and str(fixed) not in json.dumps(script.messages)
    root = request.output_dir / 'artifacts/reproduction'
    report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
    backends = report['backends']
    # The record names the component that ran, never the deprecated flag that selected it.
    assert backends['infrastructure'] == INFRASTRUCTURE
    assert backends['agentscope_version'] == installed_agentscope_version()
    assert backends['strategy'] == 'reproagent' and backends['strategy_version'] == '1'
    assert backends['model_backend'] == INFRASTRUCTURE and backends['agent_backend'] == INFRASTRUCTURE
    assert report['differential_validated'] is True
    for label, factory, expected in [('buggy', projects.plain, 1), ('fixed', projects.fixed, 0)]:
        fresh = factory(tmp_path / ('fresh-' + label))
        replay = subprocess.run([sys.executable, str(root / 'replay.py'), '--repo', str(fresh), '--python', sys.executable,
                                 '--output', str(tmp_path / ('replay-' + label)), '--install'], capture_output=True, timeout=30)
        assert replay.returncode == expected, replay.stderr.decode(errors='replace')


def test_entry_points_offer_one_infrastructure_and_never_a_native_fallback(tmp_path, projects, facts, monkeypatch):
    """The evaluation entry point records the same component, and a missing SDK is fatal."""
    import importlib
    import importlib.util
    from importlib.metadata import PackageNotFoundError
    from evals.schema import EvalResult
    from evals.swt_bench.io import read_json
    from evals.swt_bench.run import run_batch
    from tests.integration.test_swt_batch import inputs
    catalog, manifest, bindings = inputs(tmp_path, projects)
    async def run_one(case, model, output, **kwargs): return EvalResult(case.case_id, status='EXHAUSTED')
    round_data = asyncio.run(run_batch(catalog, manifest, bindings, ModelConfig(), tmp_path / 'batch', run_one=run_one))
    assert round_data['configuration']['model_backend'] == INFRASTRUCTURE
    assert round_data['configuration']['agent_backend'] == INFRASTRUCTURE
    assert read_json(tmp_path / 'batch/summary.json')['model_backend'] == INFRASTRUCTURE
    dependency = importlib.import_module('reproagent.adapters.agentscope.dependency')
    def missing(name): raise PackageNotFoundError(name)
    monkeypatch.setattr(dependency, 'version', missing)
    request, *_ = setup(tmp_path / 'missing-sdk', projects, facts)
    with pytest.raises(ValueError, match='agentscope.*install'):
        create_controller(request, ModelConfig(base_url='https://offline.example/v1', model='offline'))
    # No native model execution path is importable, so none can be fallen back to.
    assert importlib.util.find_spec('reproagent.adapters.models.provider') is None
