"""Explicit scoped unknowns must not erase blocking prerequisites or source checks."""
import asyncio
from dataclasses import asdict, replace
import json

import pytest

from reproagent.core.models import EvidenceContext, IssueContract, IssueDescription, SourceRef
from reproagent.core.protocol import contract_schema, validate_contract
from reproagent.core.serialization import decode_record
from tests.unit.test_agent import agent
from tests.integration.test_agentscope_runtime import environment, candidate_ids, write_payload
from tests.integration.test_agentscope_domain_tools import call, text_of


def payload(**updates):
    return dict(trigger="empty list", expected="parse([]) returns []", reported_actual="IndexError",
                source_indices=[0], observable_checks=[], assumptions=[], missing_information=[], **updates)


def test_schema_exposes_optional_nonblocking_scope_without_changing_legacy_requirements():
    schema=contract_schema(1)
    assert schema["properties"]["nonblocking_information"]["type"] == "array"
    assert "nonblocking_information" not in schema["required"]
    assert "current" in schema["properties"]["missing_information"]["description"]
    assert validate_contract(payload(), 1) == payload()


def test_analyzer_keeps_nonblocking_questions_separate_from_real_blockers(facts):
    data=payload(nonblocking_information=["Other input types remain unconfirmed."])
    data["missing_information"]=["Current required input is unavailable."]
    strategy,_,_=agent(data,facts)
    contract=asyncio.run(strategy.analyze(IssueDescription("bug","hash"),EvidenceContext((SourceRef("issue.md","hash"),),("bug",))))
    assert contract.nonblocking_information == tuple(data["nonblocking_information"])
    assert contract.missing_information == tuple(data["missing_information"])


@pytest.mark.parametrize("value", ["not a list", [3], None])
def test_nonblocking_questions_have_checked_types(value):
    with pytest.raises(ValueError):
        validate_contract(payload(nonblocking_information=value),1)


def test_the_same_question_cannot_be_blocking_and_nonblocking():
    data=payload(nonblocking_information=[" Unknown input " ])
    data["missing_information"]=["unknown input"]
    with pytest.raises(ValueError):
        validate_contract(data,1)


def test_legacy_stored_contract_defaults_to_no_nonblocking_questions():
    raw=json.loads(json.dumps({"schema_version":1, **asdict(IssueContract("old", expected="return []"))}))
    raw.pop("nonblocking_information",None)
    loaded=decode_record("contracts",raw)
    assert loaded.nonblocking_information == ()


def test_clearing_a_blocker_without_new_evidence_is_rejected(facts):
    source=SourceRef("issue.md","hash")
    previous=IssueContract("contract",trigger="empty list",expected="parse([]) returns []",reported_actual="IndexError",
                           sources=(source,),missing_information=("Need original input evidence.",))
    strategy,_,_=agent(payload(),facts)
    with pytest.raises(ValueError,match="new evidence"):
        asyncio.run(strategy.analyze(IssueDescription("bug","hash"),EvidenceContext((source,),("bug",),previous)))


def test_scope_revision_with_new_original_evidence_preserves_the_unresolved_question(facts):
    issue=SourceRef("issue.md","issuehash")
    code=SourceRef("example/parser.py","codehash",1,2)
    previous=IssueContract("contract",trigger="empty list",expected="parse([]) returns []",reported_actual="IndexError",
                           sources=(issue,),missing_information=("Other input types remain unconfirmed.",))
    data=payload(nonblocking_information=list(previous.missing_information))
    data["source_indices"]=[0,1]
    strategy,_,_=agent(data,facts)
    contract=asyncio.run(strategy.analyze(IssueDescription("bug","hash"),EvidenceContext((issue,code),("bug","source"),previous)))
    assert not contract.missing_information
    assert contract.nonblocking_information == previous.missing_information
    assert contract.version == previous.version+1 and contract.contract_id == previous.contract_id


def test_actual_publish_allows_nonblocking_scope_but_still_rejects_a_real_blocker(tmp_path,projects,facts):
    env=environment(tmp_path,projects,facts)
    nonblocking=("Other input types remain unconfirmed.",)
    contract=replace(env.service.contract,nonblocking_information=nonblocking,missing_information=("Current oracle unknown.",))
    env.service.bind_contract(contract)
    response=call(env,"write_candidate",write_payload())
    refused=json.loads(text_of(response))
    assert refused["code"]=="GROUNDED_CONTRACT_REQUIRED"
    assert refused["nonblocking_information"]==list(nonblocking)
    assert not candidate_ids(env) and not env.gate.finished
    env.service.bind_contract(replace(contract,missing_information=()))
    response=call(env,"write_candidate",write_payload())
    assert json.loads(text_of(response))["status"]=="candidate_published"
    assert len(candidate_ids(env))==1


def test_verifier_view_keeps_nonblocking_context_explicit():
    from reproagent.core.review_context import _contract_view
    contract=IssueContract("contract",nonblocking_information=("Outside current trigger.",))
    assert _contract_view(contract)["nonblocking_information"]==["Outside current trigger."]


def test_nonblocking_notes_cannot_supply_missing_expectation_evidence(facts):
    data=payload(nonblocking_information=["Outside variant unknown."])
    data["source_indices"]=[]
    strategy,_,_=agent(data,facts)
    contract=asyncio.run(strategy.analyze(IssueDescription("bug","hash"),EvidenceContext()))
    assert not contract.expected and not contract.sources
    assert contract.missing_information and contract.nonblocking_information


@pytest.mark.parametrize("experience_enabled",[False,True])
def test_scope_and_trigger_guidance_is_present_with_or_without_experience(tmp_path,projects,facts,experience_enabled):
    from tests.unit.test_agent import exploration_prompt,phase_context
    text=exploration_prompt(phase_context(facts,tmp_path,projects),experience_enabled=experience_enabled)
    assert "nonblocking_information" in text
    assert "trace the reported diagnostic" in text
    assert "actual return type" in text
