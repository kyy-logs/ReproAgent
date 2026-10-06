from tests.unit.test_verifier import prepare
from reproagent.core.models import CandidateClass
import asyncio


def test_correct_behavior_assertion_reproduces_actual_bug(tmp_path, projects, facts):
    contract, candidate, execution, verifier, model, *_ = prepare(tmp_path, projects, facts)
    verdict = asyncio.run(verifier.evaluate(contract, candidate, (execution,), facts.context()))
    assert execution.raw.exit_code == 1 and execution.observation.executed
    assert verdict.classification == CandidateClass.REPRODUCED
    assert verdict.evidence_refs and model.calls
