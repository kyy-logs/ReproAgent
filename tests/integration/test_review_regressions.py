import asyncio
import json
import sys
from dataclasses import asdict, replace

import httpx
import pytest

from reproagent.app import create_controller
from reproagent.core.agent import ReproAgent
from reproagent.core.budget import BudgetedGateway
from reproagent.core.models import (BudgetLimits, CandidateDraft, DraftFile, EvidenceContext, EvidenceLevel,
    FixValidationRequest, IssueDescription, ModelConfig, ModelResponse, PythonPytestConfig, SourceRef, TaskRequest, TaskState)
from reproagent.adapters.languages.python_pytest.collector import read_probe
from reproagent.store import TaskStore
from reproagent.workspace import Workspace
from tests.integration.test_exporter import export_setup
from tests.integration.test_runner import execute, setup_runner
from tests.unit.test_controller import ScriptedModel, setup


def test_partial_node_phases_cannot_be_complete(tmp_path):
    path = tmp_path / 'probe.jsonl'
    events = [('session_start', {}), ('collected', {'nodeids':['test.py::a','test.py::b']}),
        ('test_phase', {'nodeid':'test.py::a','when':'call','outcome':'passed'}), ('session_finish', {'exitstatus':0})]
    path.write_text(''.join(json.dumps(dict(probe_version=1,run_id='r',seq=i,event=name,payload=payload)) + '\n' for i,(name,payload) in enumerate(events)))
    assert not read_probe(path, 'r').probe_complete


def test_fixed_cannot_deselect_accepted_failure_node(tmp_path, projects, facts):
    from tests.unit.test_controller import PhasePlan, publish
    two_tests = ('from example.parser import parse\ndef test_smoke(): assert parse([1]) == [1]\n'
                 'def test_bug(): assert parse([]) == []\n')
    request, model, _, ctx = setup(tmp_path, projects, facts)
    controller = create_controller(request, ModelConfig(), gateway=model,
                                   explorer_factory=PhasePlan([publish(content=two_tests)]))
    fixed = projects.plain(tmp_path / 'still-buggy')
    (fixed / 'pytest.ini').write_text('[pytest]\naddopts = -k test_smoke\n')
    result = asyncio.run(controller.run(request, ctx, FixValidationRequest(fixed, sys.executable)))
    assert result.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert result.uncertainties


@pytest.mark.parametrize('path', ['tests/conftest.py','tests/pytest.ini','tests/pyproject.toml','new_business.py'])
def test_candidate_data_cannot_introduce_config_or_business_code(tmp_path, projects, facts, path):
    _, _, workspace, snapshot, _, _ = setup_runner(tmp_path, projects, facts)
    with pytest.raises(ValueError):
        workspace.publish(CandidateDraft((DraftFile('tests/test_new.py', b'def test_new(): assert True\n'), DraftFile(path, b'# injected', 'data')), snapshot.snapshot_id,'c',1,'bad'), snapshot)


def test_target_origin_must_belong_to_protected_manifest(tmp_path, projects, facts):
    _, request, workspace, _, _, runner = setup_runner(tmp_path, projects, facts, 'src_layout')
    (request.repo / 'src/target_shadow.py').write_text('def parse(): return 1\n')
    request = replace(request, language=replace(request.language, target_modules=('target_shadow',)))
    snapshot = workspace.freeze(request, facts.context())
    candidate = workspace.publish(CandidateDraft((DraftFile('tests/test_shadow.py', b'from target_shadow import parse\ndef test_bad(): assert parse() == 1\n'), DraftFile('tests/target_shadow.py', b'def parse(): return 2\n', 'data')),snapshot.snapshot_id,'c',1,'shadow'),snapshot)
    result = execute(request, snapshot, candidate, runner, facts.context())
    assert result.observation.framework_details['source_binding_ok'] is False


def test_finalization_timeout_preserves_evidence_without_success_package(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path, projects, facts, BudgetLimits(finalize_timeout_seconds=.00001))
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.EXHAUSTED
    assert result.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert not (request.output_dir / 'artifacts/reproduction').exists()


def test_probe_size_is_bounded_before_loading(tmp_path):
    path = tmp_path / 'probe.jsonl'; path.write_bytes(b'x' * 4096)
    observation = read_probe(path, 'r', max_bytes=1024)
    assert not observation.probe_complete and 'limit' in str(observation.framework_details)


def test_probe_bytes_count_toward_execution_limit(tmp_path, facts):
    from reproagent.adapters.runtimes.local import LocalBackend
    from reproagent.core.models import ExecutionSpec
    path = tmp_path / 'probe.jsonl'
    script = f"from pathlib import Path; Path({str(path)!r}).write_bytes(b'x' * 100000)"
    spec = ExecutionSpec('s',(sys.executable,'-c',script),tmp_path,probe_path=path)
    raw = asyncio.run(LocalBackend().execute(spec,facts.context(limits=BudgetLimits(log_bytes=1024))))
    assert raw.log_truncated and raw.stop_reason == 'LOG_LIMIT'


def test_common_model_boundary_redacts_analysis_and_phase_feedback(tmp_path, projects, facts, monkeypatch):
    """A provider secret that reaches a model answer never reaches the next phase or an event."""
    from tests.unit.test_controller import PhasePlan, ask, publish
    secret = 'known-private-key'
    monkeypatch.setenv('REPROAGENT_API_KEY', secret)
    class EchoingModel(ScriptedModel):
        def __init__(self):
            super().__init__(classification='NOT_REPRODUCED')
        async def complete(self, request, context):
            response = await super().complete(request, context)
            if request.response_kind == 'verdict':
                return replace(response, text=json.dumps({**json.loads(response.text), 'reason':secret}))
            return response
    request, _model, _, ctx = setup(tmp_path, projects, facts)
    controller = create_controller(request, ModelConfig(), gateway=EchoingModel(),
                                   explorer_factory=PhasePlan([publish(), ask('stop')]))
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION and result.stop_reason == 'MISSING_INFORMATION'
    # The Controller's own feedback boundary redacts the secret before a phase sees it.
    assert len(controller.explorer.contexts) == 2
    assert '[REDACTED]' in controller.explorer.contexts[1].feedback
    assert secret not in json.dumps([context.feedback for context in controller.explorer.contexts])
    assert secret not in json.dumps([{'kind':event.kind,'payload':event.payload} for event in controller.store.read_events()[0]])


def test_analysis_redacts_provider_secrets_before_the_model(tmp_path, projects, facts):
    secret = 'known-private-key'
    class CapturingModel:
        def __init__(self): self.messages = []
        async def complete(self, request, context):
            self.messages.extend(request.messages)
            data = {'expected':'return []','trigger':'empty','reported_actual':'IndexError','source_indices':[0],
                    'observable_checks':[],'assumptions':[],'missing_information':[]}
            return ModelResponse(json.dumps(data))
    model = CapturingModel(); context = facts.context()
    agent = ReproAgent(BudgetedGateway(model, secrets=(secret,)), context)
    contract = asyncio.run(agent.analyze(IssueDescription(secret,'h'),EvidenceContext((SourceRef('issue','h'),),(secret,))))
    assert contract.expected == 'return []'
    assert secret not in json.dumps(model.messages)


def test_ambiguous_retry_keeps_total_cost_unknown(facts):
    """A first attempt that never answered has no tokens to bill, so the call stays unknown."""
    from reproagent.adapters.agentscope.gateway import AgentScopeModelGateway
    from reproagent.adapters.agentscope.model_factory import AgentScopeModelFactory
    count = []
    def handler(request):
        count.append(request)
        if len(count) == 1: raise httpx.ReadTimeout('ambiguous',request=request)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{}'}}],'usage':{'prompt_tokens':10,'completion_tokens':5}})
    factory = AgentScopeModelFactory(ModelConfig(base_url='https://offline.example/v1',model='test',input_cost_per_million=1,output_cost_per_million=1),
                                     None, transport=httpx.MockTransport(handler))
    gateway = BudgetedGateway(AgentScopeModelGateway(factory))
    from reproagent.core.models import ModelRequest
    ctx = facts.context(); result = asyncio.run(gateway.complete(ModelRequest(({'role':'user','content':'x'},)),ctx))
    assert len(count) == 2 and result.cost_kind == 'unknown' and result.cost_value is None
    assert ctx.budget.unknown_cost_calls >= 1 and ctx.budget.cost_spent > 0


def test_inspect_completed_task_ignores_project_json_and_specs(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path, projects, facts)
    (request.repo / 'data.json').write_text('{"ordinary":"project-data"}')
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.DONE
    inspected = controller.store.inspect()
    assert not inspected['incomplete_records'], inspected['errors']


def test_export_retains_sources_verdict_and_snapshot_provenance(tmp_path, projects, facts):
    exporter,result,store,candidate,execution,snapshot,request = export_setup(tmp_path, projects, facts)
    from reproagent.core.models import Verdict, CandidateClass
    store.save_record('verdicts','accepted',Verdict(CandidateClass.REPRODUCED,'accepted with citations',execution.observation.failure_refs,candidate_id=candidate.candidate_id,manifest_hash=candidate.manifest_hash,contract_id=candidate.contract_id,run_ids=(execution.run_id,)))
    manifest=exporter.export(result,store)
    report=json.loads((manifest.root / 'report.json').read_text())
    assert report['protected_snapshot']['manifest_hash'] == snapshot.manifest_hash
    assert execution.run_id in report['accepted_run_ids']
    assert report['source_mapping'] and report['verdict_mapping']
    for mapping in (*report['source_mapping'],*report['verdict_mapping']):
        assert (manifest.root / mapping['export_path']).is_file()


def test_target_collection_dependency_failure_is_blocked(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path, projects, facts)
    (request.repo / 'example/parser.py').write_text('import missing_target_dependency_123\ndef parse(x): return x\n')
    result=asyncio.run(controller.run(request,ctx))
    assert result.status == TaskState.BLOCKED
    report=json.loads((request.output_dir/'artifacts/diagnostic/report.json').read_text())
    assert report['log_mapping']


def test_contract_revision_from_read_documentation_is_persisted(tmp_path, projects, facts):
    """A phase that read the repository document asks for the revision it justifies.

    The ungrounded first contract is replaced by a version derived from the lines the phase
    cited, and the whole task then runs on that version.
    """
    from tests.unit.test_controller import PhasePlan, publish, revise, source_ref
    class RevisingModel(ScriptedModel):
        def __init__(self): super().__init__(missing=True); self.analysis = 0
        async def complete(self, request, context):
            data = json.loads(request.messages[-1]['content'])
            if request.response_kind == 'contract':
                self.messages.extend(request.messages); self.kinds.append(request.response_kind)
                self.analysis += 1
                return ModelResponse(json.dumps({'expected':'' if self.analysis == 1 else 'parse([]) returns []',
                    'trigger':'empty','reported_actual':'IndexError','source_indices':[] if self.analysis == 1 else [1],
                    'missing_information':['expectation unclear'] if self.analysis == 1 else [],
                    'observable_checks':[],'assumptions':[]}))
            return await super().complete(request, context)
    request, _, _, ctx = setup(tmp_path, projects, facts)
    (request.repo / 'README.md').write_text('parse([]) must return an empty list.\n')
    model = RevisingModel()
    controller = create_controller(request, ModelConfig(), gateway=model,
        explorer_factory=PhasePlan([lambda explorer, context: revise([source_ref(explorer, 'README.md', 1, 1)],
                                                                     'new documented expectation')(explorer, context),
                                    publish()]))
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.DONE
    contracts = [controller.store.load_record('contracts', p.stem) for p in (request.output_dir/'contracts').glob('*.json')]
    assert {c.version for c in contracts} == {1, 2}
    assert len({c.contract_id for c in contracts}) == 1
