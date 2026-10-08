"""One infrastructure, whichever backend a caller remembers asking for.

A default run and a run that still passes the old backend flags assemble the same SDK
gateway and the same SDK toolkit, so the same task reaches the same evidence under either
spelling; a legacy flag only warns.  Nothing here keeps a second runtime alive: the native
model path is gone, and a missing SDK is an install error rather than a fall back to it.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from dataclasses import replace

import httpx
import pytest

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
    # Both factories of the task are released with it: the exploration one through the
    # explorer, the contract/verdict one through the gateway.  A long serial batch would
    # otherwise keep one client per purpose per finished task.
    assert controller.explorer.model_factory is None
    assert controller.gateway.gateway.factory._clients == []
    assert calls == ['contract', 'exploration', 'verdict', 'verdict']
    assert toolkit == [list(TOOL_NAMES)]
    assert ctx.budget.steps_used == 1 and assert_hidden(script.messages, fixed) is None
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


# ---------------------------------------------------------------------------
# The whole chain: snapshot search, the candidate, and the Controller's own steps
# ---------------------------------------------------------------------------

#: The four tools the phase calls, in order, before anything is executed.  Each is its own
#: SDK reply: a response with two tool calls is refused whole, so a real phase's history is
#: exactly this chain.  The calls name the frozen snapshot's own paths, resolved when the
#: turn is answered, because that is where the snapshot lives.
CHAIN = (('Glob', lambda root: {'pattern': '**/*.py', 'path': str(root)}),
         ('Grep', lambda root: {'pattern': 'def parse', 'path': str(root), 'output_mode': 'content'}),
         ('Read', lambda root: {'file_path': str(root / 'example' / 'parser.py'), 'offset': 1, 'limit': 2}),
         ('write_candidate', lambda root: {'files': [{'path': 'tests/test_repro.py', 'content': CANDIDATE, 'role': 'test'}],
                                           'hypothesis': 'empty input'}))

#: What the phase answers with when the chain above has no turn left: a claim, not a result.
SUCCESS_PROSE = 'This is a success: parse([]) raises IndexError on the frozen snapshot, so the bug is reproduced.'


def find_ripgrep():
    """The ripgrep the SDK's Grep runs, or None when this host has none."""
    candidates = (os.environ.get('REPROAGENT_RG_PATH'), shutil.which('rg'), shutil.which('rg.exe'))
    return next((path for path in candidates if path and Path(path).is_file()), None)


def tool_response(name, arguments, call_id):
    return httpx.Response(200, json={'id': 'mock', 'object': 'chat.completion', 'model': 'offline', 'created': 1,
        'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
            'tool_calls': [{'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})


def text_response(text):
    return httpx.Response(200, json={'id': 'mock', 'object': 'chat.completion', 'model': 'offline', 'created': 1,
        'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': text}}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})


def chain_transport(script, ctx, seen, output_dir, chain=CHAIN):
    """One mock provider for a whole task: the phase's tools, then the domain requests.

    An exploration request is the one the phase toolkit is attached to; it is answered with
    the next tool of the chain, against the snapshot the task froze before the first request.
    Every other request is a structured domain request -- no tools, one JSON object back --
    and is answered by the scripted contract/verdict model, exactly as the gateway sends it.
    Once the chain is exhausted the phase is answered with :data:`SUCCESS_PROSE`, which is a
    claim about the snapshot and not a tool call.
    """
    answers = list(chain)

    async def handle(http_request):
        payload = json.loads(http_request.content)
        seen.append(payload)
        if 'tools' in payload:
            if not answers:
                return text_response(SUCCESS_PROSE)
            name, build = answers.pop(0)
            root = next((output_dir / 'snapshots').glob('*/code'))
            return tool_response(name, build(root), 'call-%d' % len(seen))
        data = json.loads(payload['messages'][-1]['content'])
        kind = 'verdict' if 'available_refs' in data else 'contract'
        response = await script.complete(ModelRequest(tuple(payload['messages']), kind), ctx)
        return text_response(response.text)

    return handle


def tool_messages(payload):
    """The tool results one request carried, as the SDK wrote them into the conversation."""
    return json.dumps([message for message in payload['messages'] if message.get('role') == 'tool'])


def assert_hidden(wire, path):
    """*path* appears nowhere in *wire*, however the JSON layering around it escaped it.

    The phase's message is itself a JSON document inside the JSON request, so a leaked path
    reaches the wire with its separators escaped once per layer.  Searching for one fixed
    spelling would pass on a wire that really carries the path -- verified: a plain search
    does not find a once-escaped path, and a once-escaped search does not find a twice-escaped
    one.  Both sides are collapsed to forward slashes first, which is the same at any depth.
    """
    def collapsed(text):
        return re.sub(r'\\+', '/', text)

    assert collapsed(str(path)) not in collapsed(json.dumps(wire))


def action_endings(events):
    return [(event.kind, event.payload['action'], event.payload['result_code'])
            for event in events if event.kind in ('action.completed', 'action.rejected')]


def test_the_whole_chain_runs_from_snapshot_search_to_exported_replay(tmp_path, projects, facts, monkeypatch):
    """One task, one wire: Glob, Grep, Read, write_candidate, then the Controller's own steps.

    The four exploration turns run through a real SDK agent over the real phase toolkit and
    the real frozen snapshot; everything after the last of them -- executing the candidate on
    the original version, the structured verification, the independent repetition, the
    supplied fixed version and the export -- is the Controller's, and the delivered package
    replays 1 on a fresh buggy copy and 0 on a fresh fixed one.
    """
    rg = find_ripgrep()
    if rg is not None:
        # The SDK's Grep runs ripgrep and is refused without it, so the located binary is put
        # on PATH: this chain then exercises a real search rather than the refusal.
        monkeypatch.setenv('PATH', str(Path(rg).parent) + os.pathsep + os.environ.get('PATH', ''))
    request, script, _, ctx = setup(tmp_path, projects, facts)
    fixed = projects.fixed(tmp_path / 'fixed-hidden')
    seen = []
    model = ModelConfig(base_url='https://offline.example/v1', model='offline', output_limit_field='max_tokens')
    transport = httpx.MockTransport(chain_transport(script, ctx, seen, request.output_dir))
    controller = create_controller(request, model, transport=transport)
    result = asyncio.run(controller.run(request, ctx, FixValidationRequest(fixed, sys.executable)))
    assert result.status.value == 'DONE' and result.evidence_level.value == 'DIFFERENTIAL_VALIDATED', result

    # The exploration wire is the SDK phase's: every turn carries the phase toolkit and
    # nothing else, one tool per turn, and the phase spends one step per turn.
    explorations = [payload for payload in seen if 'tools' in payload]
    assert len(explorations) == len(CHAIN) == 4
    for payload in explorations:
        assert [entry['function']['name'] for entry in payload['tools']] == list(TOOL_NAMES)
    assert ctx.budget.steps_used == len(CHAIN)
    # Each turn's own result reaches the next turn as the SDK wrote it: the Glob listing, the
    # Grep match, and the Read response with the citation sidecar the ledger issued.
    assert tool_messages(explorations[0]) == '[]'
    assert 'parser.py' in tool_messages(explorations[1])
    # With ripgrep the search returns the line; without it the backend says the search could
    # not run, which is never the same answer as "no matches".
    assert ('def parse' if rg else 'ripgrep') in tool_messages(explorations[2])
    assert '<evidence>' in tool_messages(explorations[3])

    # The verifier -- and the contract analysis -- use structured requests: no tools, one JSON
    # object back.  The model was never asked for a third kind of decision, so there is no
    # run/submit choice for it to make, and the Controller's own steps are its business.
    structured = [payload for payload in seen if 'tools' not in payload]
    assert script.kinds == ['contract', 'verdict', 'verdict']
    assert len(structured) == len(script.kinds) == 3 and len(seen) == 4 + 3
    for payload in structured:
        assert payload['response_format'] == {'type': 'json_object'}

    stored, errors = controller.store.read_events()
    assert not errors
    assert action_endings(stored) == [('action.completed', 'run_candidate', 'OK'),
                                      ('action.completed', 'submit_candidate', 'OK')]
    # Three real executions of one candidate -- original, repeat and the supplied fixed
    # version -- and two verifications; the fixed run is executed, never verified by the model.
    runs = [controller.store.load_record('runs', path.parent.name)
            for path in (request.output_dir / 'runs').glob('*/execution.json')]
    assert sorted(run.execution_role for run in runs) == ['fixed', 'original', 'original']
    assert len({run.candidate_id for run in runs}) == 1
    assert len(list((request.output_dir / 'verdicts').glob('*.json'))) == 2
    # The hidden fixed version never reaches the model, under any request.
    assert_hidden(seen, fixed)

    root = request.output_dir / 'artifacts/reproduction'
    report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
    assert report['differential_validated'] is True and report['fix_validation_status'] == 'passed'
    for label, factory, expected in [('buggy', projects.plain, 1), ('fixed', projects.fixed, 0)]:
        fresh = factory(tmp_path / ('fresh-' + label))
        replay = subprocess.run([sys.executable, str(root / 'replay.py'), '--repo', str(fresh), '--python', sys.executable,
                                 '--output', str(tmp_path / ('replay-' + label)), '--install'], capture_output=True, timeout=30)
        assert replay.returncode == expected, replay.stderr.decode(errors='replace')


def test_a_phase_that_only_claims_success_publishes_and_runs_nothing(tmp_path, projects, facts):
    """The SDK's natural-language ending is not a result; only a tool call publishes one.

    The same wire, with the turn after the three file tools answered in prose instead of by
    ``write_candidate``: the claim is in the conversation, the phase has no result, and no
    execution, verdict or reproduction package follows from it.
    """
    request, script, _, ctx = setup(tmp_path, projects, facts)
    seen = []
    model = ModelConfig(base_url='https://offline.example/v1', model='offline', output_limit_field='max_tokens')
    transport = httpx.MockTransport(chain_transport(script, ctx, seen, request.output_dir, chain=CHAIN[:3]))
    controller = create_controller(request, model, transport=transport)
    result = asyncio.run(controller.run(request, ctx))
    assert result.status.value == 'NEEDS_INFORMATION' and result.stop_reason == 'NO_CANDIDATE', result
    assert result.evidence_level.value == 'NONE'
    assert ctx.budget.steps_used == 4                    # the three tools, and the prose turn
    # The claim was really served -- the fourth exploration turn is the one the transport
    # answered with it -- and the SDK's own completed reply is what ended the phase.
    assert len([payload for payload in seen if 'tools' in payload]) == 4
    stored, errors = controller.store.read_events()
    assert not errors
    assert [(entry.payload['action'], entry.payload['result_code'], entry.payload['sdk_end_reason'])
            for entry in stored if entry.kind == 'exploration.finished'] == [('no_candidate', 'no_candidate', 'completed')]
    assert not (request.output_dir / 'runs').exists()
    assert not list((request.output_dir / 'candidates').glob('*'))
    assert not (request.output_dir / 'artifacts/reproduction').exists()
    assert (request.output_dir / 'artifacts/diagnostic/report.json').exists()
    assert script.kinds == ['contract']                  # nothing was ever submitted for a verdict


def test_external_original_issue_reaches_exploration_without_known_key(tmp_path, projects, facts, monkeypatch):
    from reproagent.core.serialization import bytes_hash
    request, script, _, ctx = setup(tmp_path, projects, facts)
    issue_text = 'Return a ValidationError object; do not raise it. ORIGINAL_FACT_8e219\n'
    key = 'offline-review-key-"quoted"-8e219'
    external = tmp_path / 'external-issue.md'
    external.write_text(issue_text + key, encoding='utf-8')
    request = replace(request, issue_file=external)
    monkeypatch.setenv('REVIEW_OFFLINE_KEY', key)
    model = ModelConfig(base_url='https://offline.example/v1', model='offline',
                        output_limit_field='max_tokens', api_key_env='REVIEW_OFFLINE_KEY')
    wire = []

    async def handle(http_request):
        payload = json.loads(http_request.content)
        wire.append(payload)
        if payload.get('tools'):
            return httpx.Response(200, json={'id':'mock', 'object':'chat.completion', 'model':'offline', 'created':1,
                'choices':[{'index':0, 'finish_reason':'stop', 'message':{'role':'assistant','content':'Need information.'}}]})
        reply = await script.complete(ModelRequest(tuple(payload['messages']), 'contract'), ctx)
        return httpx.Response(200, json={'id':'mock', 'object':'chat.completion', 'model':'offline', 'created':1,
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':reply.text}}]})

    controller = create_controller(request, model, transport=httpx.MockTransport(handle))
    result = asyncio.run(controller.run(request, ctx))
    phase = next(payload for payload in wire if payload.get('tools'))
    text = json.dumps(phase, ensure_ascii=False)
    assert issue_text.strip() in text
    phase_data = json.loads(phase['messages'][-1]['content'])
    assert phase_data['issue']['content_hash'] == bytes_hash(external.read_bytes())
    assert key not in text and '[REDACTED]' in phase_data['issue']['text']
    assert result.status.value == 'NEEDS_INFORMATION'
