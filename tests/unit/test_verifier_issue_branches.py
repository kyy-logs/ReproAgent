import asyncio
import json
from dataclasses import replace
import pytest
from reproagent.core.models import BudgetLimits, CandidateClass, ModelResponse
from tests.unit.test_verifier import prepare, EvidenceModel


@pytest.mark.parametrize('change,code', [
    ('missing','EXECUTION_EVIDENCE_MISSING'), ('binding','RUN_BINDING_MISMATCH'),
    ('probe','PROBE_INCOMPLETE'), ('framework','FRAMEWORK_CHECK_FAILED'),
    ('import','CANDIDATE_IMPORT_BLOCKED'), ('environment','ENVIRONMENT_BLOCKED'),
    ('unexecuted','TEST_EXECUTION_INVALID'), ('passes','CANDIDATE_NOT_REPRODUCED'),
    ('expectation','EXPECTATION_UNGROUNDED'), ('capacity','REVIEW_CONTEXT_TOO_LARGE'),
    ('source','EVIDENCE_UNAVAILABLE'), ('changed','CANDIDATE_HASH_MISMATCH'),
])
def test_program_rejections_have_structured_factual_gaps(tmp_path, projects, facts, change, code):
    contract, candidate, run, verifier, model, *_ = prepare(tmp_path, projects, facts)
    runs, ctx = (run,), facts.context()
    if change == 'missing': runs = ()
    elif change == 'binding': runs = (replace(run, manifest_hash='wrong'),)
    elif change == 'probe': runs = (replace(run, observation=replace(run.observation, probe_complete=False)),)
    elif change == 'framework':
        runs = (replace(run, observation=replace(run.observation, tests=({'when':'call','outcome':'skipped','wasxfail':'known'},))),)
    elif change in ('import','environment'):
        observation = replace(run.observation, tests=({'when':'call','outcome':'failed','longrepr':'ModuleNotFoundError'},),
            target_origins={} if change == 'import' else run.observation.target_origins)
        runs = (replace(run, observation=observation),)
    elif change == 'unexecuted': runs = (replace(run, observation=replace(run.observation, executed=False)),)
    elif change == 'passes': runs = (replace(run, raw=replace(run.raw, exit_code=0)),)
    elif change == 'expectation': contract = replace(contract, expected='')
    elif change == 'capacity': ctx = facts.context(limits=BudgetLimits(tool_response_bytes=1024))
    elif change == 'source': verifier.resolve = lambda _: (_ for _ in ()).throw(ValueError('unavailable'))
    elif change == 'changed':
        path = candidate.storage_root / candidate.files[0].path
        path.write_text('changed', encoding='utf-8')
    verdict = asyncio.run(verifier.evaluate(contract, candidate, runs, ctx))
    assert verdict.issues, 'rejection lost its structured diagnostic'
    item = verdict.issues[0]
    assert item.code == code and item.origin == 'program_check'
    assert verdict.unmet_checks
    assert not model.calls
    if change == 'capacity':
        assert item.measurements['needed_bytes'] > item.measurements['budget_bytes'] == 1024
    if change == 'source': assert not item.evidence_refs
    if change == 'probe': assert item.measurements['protection_checked'] is True  # complete exited run was protected


class Claims(EvidenceModel):
    def __init__(self, values=None, invalid=False, citations='both'):
        super().__init__(); self.values=values or {}; self.invalid=invalid; self.citations=citations
    async def complete(self, request, context):
        self.calls.append(request)
        data=json.loads(request.messages[-1]['content'])
        refs=data['available_refs']
        if self.citations == 'expectation': refs=[refs[0]]
        if self.citations == 'unknown': refs=[dict(refs[0], content_hash='bad')]
        reply=dict(classification='REPRODUCED', reason='需核查目标调用', evidence_refs=refs,
            expected_assertion=True,target_triggered=True,failure_matches_issue=True)
        reply.update(self.values)
        if self.invalid: reply.pop('reason')
        return ModelResponse(json.dumps(reply))


@pytest.mark.parametrize('field,code', [('expected_assertion','EXPECTED_ASSERTION_UNCONFIRMED'),
    ('target_triggered','TARGET_TRIGGER_UNCONFIRMED'),('failure_matches_issue','ISSUE_FAILURE_MATCH_UNCONFIRMED')])
def test_semantic_false_is_an_unconfirmed_provider_claim(tmp_path, projects, facts, field, code):
    model=Claims({field:False})
    contract,candidate,run,verifier,*_=prepare(tmp_path,projects,facts,model=model)
    verdict=asyncio.run(verifier.evaluate(contract,candidate,(run,),facts.context()))
    assert verdict.classification is None
    assert verdict.issues[0].code==code and verdict.issues[0].origin=='provider_claim'
    assert verdict.issues[0].check==field and field in verdict.unmet_checks
    assert len(model.calls)==1 and verdict.issues[0].evidence_refs


@pytest.mark.parametrize('classification', ['NOT_REPRODUCED','INVALID_CANDIDATE',None,'ENVIRONMENT_BLOCKED'])
def test_valid_non_success_classification_gets_general_semantic_gap(tmp_path,projects,facts,classification):
    model=Claims({'classification':classification})
    contract,candidate,run,verifier,*_=prepare(tmp_path,projects,facts,model=model)
    verdict=asyncio.run(verifier.evaluate(contract,candidate,(run,),facts.context()))
    assert verdict.issues and verdict.issues[0].origin=='provider_claim'
    assert verdict.issues[0].code==('ENVIRONMENT_BLOCKED' if classification=='ENVIRONMENT_BLOCKED' else 'SEMANTIC_REPRODUCTION_UNCONFIRMED')


def test_invalid_answer_with_false_is_protocol_failure_without_semantic_promotion(tmp_path,projects,facts):
    model=Claims({'target_triggered':False},invalid=True)
    contract,candidate,run,verifier,*_=prepare(tmp_path,projects,facts,model=model)
    verdict=asyncio.run(verifier.evaluate(contract,candidate,(run,),facts.context()))
    assert verdict.classification is None and len(model.calls)==1
    assert {i.code for i in verdict.issues}=={'MODEL_PROTOCOL_ERROR'}
    assert all(i.origin=='program_check' and not i.evidence_refs for i in verdict.issues)


@pytest.mark.parametrize('citations,code', [('unknown','MODEL_PROTOCOL_ERROR'),('expectation','SEMANTIC_CITATIONS_INCOMPLETE')])
def test_bad_or_one_sided_citations_never_confirm_success(tmp_path,projects,facts,citations,code):
    model=Claims(citations=citations)
    contract,candidate,run,verifier,*_=prepare(tmp_path,projects,facts,model=model)
    verdict=asyncio.run(verifier.evaluate(contract,candidate,(run,),facts.context()))
    assert verdict.classification is None and len(model.calls)==3
    assert code in {i.code for i in verdict.issues}
    assert all(i.origin=='program_check' for i in verdict.issues)


def test_accepted_verdict_has_no_failure_diagnostics(tmp_path,projects,facts):
    contract,candidate,run,verifier,*_=prepare(tmp_path,projects,facts)
    verdict=asyncio.run(verifier.evaluate(contract,candidate,(run,),facts.context()))
    assert verdict.classification==CandidateClass.REPRODUCED and verdict.issues==()


def test_candidate_change_after_integrity_check_is_not_trusted_as_evidence(tmp_path,projects,facts):
    contract,candidate,run,verifier,*_=prepare(tmp_path,projects,facts)
    old=verifier.adapter.check_framework
    def change_after_check(*args):
        result=old(*args)
        (candidate.storage_root / candidate.files[0].path).write_text('changed',encoding='utf-8')
        return result
    verifier.adapter.check_framework=change_after_check
    verdict=asyncio.run(verifier.evaluate(contract,candidate,(run,),facts.context()))
    assert verdict.issues[0].code=='CANDIDATE_HASH_MISMATCH'
    assert verdict.issues[0].check=='candidate_file_hash' and not verdict.issues[0].evidence_refs


def test_redaction_expansion_reports_measured_review_overflow(tmp_path,projects,facts):
    from reproagent.core.review_context import build_review_context
    from reproagent.store import safe_child
    contract,candidate,run,verifier,model,*_=prepare(tmp_path,projects,facts)
    projection=build_review_context(contract,candidate,run,read_ref=verifier.resolve,
        read_file=lambda e: safe_child(candidate.storage_root,e.path).read_bytes(),max_bytes=1000000)
    from reproagent.core.serialization import canonical_bytes
    cap=len(canonical_bytes(projection.payload))+1
    verifier.secrets=('a',)
    verdict=asyncio.run(verifier.evaluate(contract,candidate,(run,),facts.context(limits=BudgetLimits(tool_response_bytes=cap))))
    item=verdict.issues[0]
    assert item.code=='REVIEW_CONTEXT_TOO_LARGE' and item.measurements['stage']=='redaction'
    assert item.measurements['needed_bytes']>cap and not model.calls
