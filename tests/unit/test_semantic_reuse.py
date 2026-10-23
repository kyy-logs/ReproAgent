import asyncio
from dataclasses import replace

import pytest

from reproagent.core.models import CandidateClass
from tests.unit.test_verifier import prepare


def pair(tmp_path, projects, facts):
    contract, candidate, first, verifier, model, runner, snapshot, request = prepare(tmp_path, projects, facts)
    environment = verifier.store.load_record('environments', first.environment_id)
    second = asyncio.run(runner.execute(candidate, snapshot, environment, facts.context()))
    return contract, candidate, first, second, verifier, model


def test_independent_replay_reuses_review_with_new_run_and_citations(tmp_path, projects, facts):
    contract, candidate, first, second, verifier, model = pair(tmp_path, projects, facts)
    a = asyncio.run(verifier.evaluate(contract, candidate, (first,), facts.context()))
    b = asyncio.run(verifier.evaluate(contract, candidate, (second,), facts.context()))
    assert a.classification == b.classification == CandidateClass.REPRODUCED
    assert any(frame.get('function') == 'parse' for test in first.observation.tests
               for frame in test.get('exception_frames', ()))
    assert len(model.calls) == 1
    assert b.run_ids == (second.run_id,) and second.run_id != first.run_id
    assert any(ref in second.observation.failure_refs for ref in b.evidence_refs)
    assert not any(ref in first.observation.failure_refs for ref in b.evidence_refs)
    assert verifier.confirm(contract, candidate, first, second, (a, b))
    events, _ = verifier.store.read_events()
    reused = [e for e in events if e.kind == 'verification.semantic_reused']
    assert len(reused) == 1
    assert reused[0].payload['first_run_id'] == first.run_id
    assert reused[0].payload['run_id'] == second.run_id


@pytest.mark.parametrize('change', ['same-run', 'contract', 'policy', 'observation', 'environment'])
def test_non_equivalent_review_falls_back_to_model(tmp_path, projects, facts, change):
    contract, candidate, first, second, verifier, model = pair(tmp_path, projects, facts)
    asyncio.run(verifier.evaluate(contract, candidate, (first,), facts.context()))
    if change == 'same-run': second = first
    elif change == 'contract': contract = replace(contract, expected=contract.expected + ' and keep controls')
    elif change == 'policy': verifier.gateway = type(model)()
    elif change == 'observation':
        second = replace(second, observation=replace(second.observation,
            framework_details={**second.observation.framework_details, 'target_calls': {'example.parser': 99}}))
    elif change == 'environment': second = replace(second, environment_id='another-environment')
    active = verifier.gateway
    before = len(active.calls)
    asyncio.run(verifier.evaluate(contract, candidate, (second,), facts.context()))
    assert len(active.calls) == before + 1


def test_hard_failure_never_uses_semantic_approval(tmp_path, projects, facts):
    contract, candidate, first, second, verifier, model = pair(tmp_path, projects, facts)
    asyncio.run(verifier.evaluate(contract, candidate, (first,), facts.context()))
    second = replace(second, observation=replace(second.observation, probe_complete=False))
    result = asyncio.run(verifier.evaluate(contract, candidate, (second,), facts.context()))
    assert result.classification != CandidateClass.REPRODUCED
    assert result.execution_diagnostic.code == 'PROBE_INCOMPLETE'
    assert len(model.calls) == 1


def test_negative_and_modified_evidence_are_not_cached(tmp_path, projects, facts):
    contract, candidate, first, second, verifier, model = pair(tmp_path, projects, facts)
    model.classification = 'NOT_REPRODUCED'
    asyncio.run(verifier.evaluate(contract, candidate, (first,), facts.context()))
    asyncio.run(verifier.evaluate(contract, candidate, (second,), facts.context()))
    assert len(model.calls) == 2


def test_reuse_record_failure_falls_back_to_real_review(tmp_path, projects, facts, monkeypatch):
    contract, candidate, first, second, verifier, model = pair(tmp_path, projects, facts)
    asyncio.run(verifier.evaluate(contract, candidate, (first,), facts.context()))
    append = verifier.store.append_event
    def fail_reuse(kind, *args, **kwargs):
        if kind == 'verification.semantic_reused':
            raise OSError('simulated audit failure')
        return append(kind, *args, **kwargs)
    monkeypatch.setattr(verifier.store, 'append_event', fail_reuse)
    result = asyncio.run(verifier.evaluate(contract, candidate, (second,), facts.context()))
    assert result.classification == CandidateClass.REPRODUCED
    assert len(model.calls) == 2


@pytest.mark.parametrize('change', ['inner-provider', 'model-config', 'redaction', 'byte-limit', 'selector'])
def test_effective_policy_and_candidate_changes_disable_reuse(tmp_path, projects, facts, change):
    from types import SimpleNamespace
    from reproagent.core.budget import BudgetedGateway
    from reproagent.core.models import BudgetLimits, ModelConfig
    contract, candidate, first, second, verifier, model = pair(tmp_path, projects, facts)
    model.factory = SimpleNamespace(config=ModelConfig(model='model-a'))
    wrapper = BudgetedGateway(model)
    verifier.gateway = wrapper
    asyncio.run(verifier.evaluate(contract, candidate, (first,), facts.context()))
    context = facts.context()
    if change == 'inner-provider': wrapper.gateway = type(model)()
    elif change == 'model-config': model.factory.config = ModelConfig(model='model-b')
    elif change == 'redaction': verifier.secrets = ('not-in-material',)
    elif change == 'byte-limit': context = facts.context(limits=BudgetLimits(tool_response_bytes=65536))
    elif change == 'selector': candidate = replace(candidate, selectors=('tests/test_repro.py::test_empty',))
    before = len(wrapper.gateway.calls)
    result = asyncio.run(verifier.evaluate(contract, candidate, (second,), context))
    if change == 'selector':
        assert result.classification == CandidateClass.INVALID_CANDIDATE
        assert result.execution_diagnostic.code == 'CANDIDATE_HASH_MISMATCH'
        assert len(wrapper.gateway.calls) == before
    else:
        assert len(wrapper.gateway.calls) == before + 1


def test_corrected_response_approval_is_not_reused(tmp_path, projects, facts):
    from tests.unit.test_verifier import EvidenceModel
    from reproagent.core.models import ModelResponse
    class Corrected(EvidenceModel):
        async def complete(self, request, context):
            if not self.calls:
                self.calls.append(request)
                return ModelResponse('{}')
            return await super().complete(request, context)
    contract, candidate, first, second, verifier, _ = pair(tmp_path, projects, facts)
    model = Corrected(); verifier.gateway = model
    a = asyncio.run(verifier.evaluate(contract, candidate, (first,), facts.context()))
    b = asyncio.run(verifier.evaluate(contract, candidate, (second,), facts.context()))
    assert a.classification == b.classification == CandidateClass.REPRODUCED
    assert len(model.calls) == 3


@pytest.mark.parametrize('stop', ['cancel', 'deadline'])
def test_stop_during_reuse_audit_is_not_swallowed(tmp_path, projects, facts, monkeypatch, stop):
    from reproagent.core.budget import BudgetStopped
    contract, candidate, first, second, verifier, model = pair(tmp_path, projects, facts)
    asyncio.run(verifier.evaluate(contract, candidate, (first,), facts.context()))
    context = facts.context()
    append = verifier.store.append_event
    def stopping(kind, *args, **kwargs):
        result = append(kind, *args, **kwargs)
        if kind == 'verification.semantic_reused':
            if stop == 'cancel': context.cancel_event.set()
            else: context.budget.deadline = 0
        return result
    monkeypatch.setattr(verifier.store, 'append_event', stopping)
    with pytest.raises(BudgetStopped):
        asyncio.run(verifier.evaluate(contract, candidate, (second,), context))
    assert len(model.calls) == 1


def test_changed_prior_evidence_does_not_supply_reuse_approval(tmp_path, projects, facts):
    contract, candidate, first, second, verifier, model = pair(tmp_path, projects, facts)
    asyncio.run(verifier.evaluate(contract, candidate, (first,), facts.context()))
    ref = first.observation.failure_refs[0]
    path = verifier.store.root / ref.path
    path.write_bytes(path.read_bytes() + b'\n')
    result = asyncio.run(verifier.evaluate(contract, candidate, (second,), facts.context()))
    assert result.classification == CandidateClass.REPRODUCED and len(model.calls) == 2
