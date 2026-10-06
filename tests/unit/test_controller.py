import asyncio
import importlib
import json
import sys
from dataclasses import replace

from reproagent.core.models import BudgetLimits, EvidenceLevel, FixValidationRequest, ModelConfig, ModelResponse, PythonPytestConfig, TaskRequest, TaskState


class ScriptedModel:
    def __init__(self, missing=False): self.messages=[]; self.step=0; self.candidate_id=''; self.missing=missing
    async def complete(self, request, context):
        self.messages.extend(request.messages)
        data = json.loads(request.messages[-1]['content'])
        if request.response_kind == 'contract':
            result = {'trigger':'empty list','expected':'' if self.missing else 'parse([]) returns []','reported_actual':'IndexError','source_indices':[] if self.missing else [0], 'missing_information':['expected?'] if self.missing else [], 'observable_checks':[], 'assumptions':[]}
        elif request.response_kind == 'verdict':
            result = {'classification':'REPRODUCED','reason':'reported IndexError on empty input', 'evidence_refs':data['available_refs'], 'expected_assertion':True,'target_triggered':True,'failure_matches_issue':True}
        else:
            if self.missing:
                return ModelResponse('{"name":"request_information","parameters":{"question":"Provide expected behavior"}}')
            names = ('write_candidate', 'run_candidate', 'submit_candidate')
            name = names[min(self.step, 2)]; self.step += 1
            self.candidate_id = (data['candidate_ids'] or [''])[0]
            parameters = {'files':[{'path':'tests/test_repro.py','content':'from example.parser import parse\ndef test_empty(): assert parse([]) == []\n','role':'test'}], 'hypothesis':'empty input'} if name == 'write_candidate' else {'candidate_id':self.candidate_id}
            result = {'name':name, 'parameters':parameters}
        return ModelResponse(json.dumps(result))


def setup(tmp_path, projects, facts, limits=None, missing=False):
    repo = projects.plain(tmp_path / 'repo')
    request = TaskRequest(repo, tmp_path / '任务', repo / 'issue.md', language=PythonPytestConfig(python=sys.executable, target_modules=('example.parser',)), limits=limits or BudgetLimits())
    model = ScriptedModel(missing)
    app = importlib.import_module('reproagent.app')
    controller = app.create_controller(request, ModelConfig(), gateway=model)
    return request, model, controller, facts.context(limits=request.limits)


def test_controller_requires_replay_export_and_cleanup_before_done(tmp_path, projects, facts):
    request, model, controller, ctx = setup(tmp_path, projects, facts)
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.DONE and result.export_state == 'published'
    assert result.evidence_level == EvidenceLevel.REPEATED_OBSERVATION
    assert len(list((request.output_dir / 'runs').glob('*/execution.json'))) == 2
    assert (request.output_dir / 'artifacts/reproduction/replay.py').exists()


def test_budget_after_first_observation_preserves_partial_evidence(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path, projects, facts, BudgetLimits(agent_steps=2))
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.EXHAUSTED
    assert result.evidence_level == EvidenceLevel.SINGLE_OBSERVATION and result.accepted_candidate_id
    assert (request.output_dir / 'artifacts/diagnostic/report.json').exists()


def test_cleanup_failure_overrides_cancel_with_original_reason_saved(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path, projects, facts)
    original = controller.runner.execute
    async def failed(*args, **kwargs):
        execution = await original(*args, **kwargs)
        return replace(execution, raw=replace(execution.raw, cleanup_ok=False, stop_reason='CANCELLED'))
    controller.runner.execute = failed
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.FAILED and result.stop_reason == 'CANCELLED'


def test_missing_information_and_environment_block_have_diagnostic_packages(tmp_path, projects, facts):
    request, _, controller, ctx = setup(tmp_path / 'missing', projects, facts, missing=True)
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.NEEDS_INFORMATION
    assert (request.output_dir / 'artifacts/diagnostic/report.json').exists()
    request, _, controller, ctx = setup(tmp_path / 'blocked', projects, facts)
    request = replace(request, language=replace(request.language, python=str(tmp_path / 'no-python.exe')))
    result = asyncio.run(controller.run(request, ctx))
    assert result.status == TaskState.BLOCKED
    assert (request.output_dir / 'artifacts/diagnostic/report.json').exists()


def test_fixed_validation_is_separate_and_same_candidate(tmp_path, projects, facts):
    request, model, controller, ctx = setup(tmp_path, projects, facts)
    fixed = projects.fixed(tmp_path / 'fixed-secret-location')
    result = asyncio.run(controller.run(request, ctx, FixValidationRequest(fixed, sys.executable)))
    assert result.status == TaskState.DONE and result.evidence_level == EvidenceLevel.DIFFERENTIAL_VALIDATED
    runs = [controller.store.load_record('runs', p.parent.name) for p in (request.output_dir / 'runs').glob('*/execution.json')]
    assert sorted(r.execution_role for r in runs) == ['fixed','original','original']
    assert len({r.candidate_id for r in runs}) == 1
    assert str(fixed) not in json.dumps(model.messages)
