import json
from dataclasses import replace

import pytest

from reproagent.core.models import BudgetLimits
from tests.integration.test_agentscope_domain_tools import call, text_of
from tests.integration.test_agentscope_runtime import candidate_ids, environment, write_payload


@pytest.mark.parametrize('expected,sources,missing,names', [
    ('', (), ('The required return value is not specified.',), ['expected', 'sources', 'missing_information']),
    ('parse([]) returns []', None, ('The proposed parameter is not defined.',), ['missing_information']),
])
def test_real_missing_prerequisites_are_explained_without_publication(tmp_path, projects, facts, expected, sources, missing, names):
    env = environment(tmp_path, projects, facts)
    contract = replace(env.service.contract, expected=expected, missing_information=missing,
        sources=env.service.contract.sources if sources is None else sources)
    env.service.bind_contract(contract)
    response = call(env, 'write_candidate', write_payload())
    payload = json.loads(text_of(response))
    assert payload['code'] == response.metadata['reproagent_error_code'] == 'GROUNDED_CONTRACT_REQUIRED'
    assert payload['missing'] == names
    assert payload['missing_information'] == list(missing)
    assert payload['contract_id'] == contract.contract_id and payload['contract_version'] == contract.version
    assert env.service.contract is contract and not env.gate.finished and candidate_ids(env) == []


def test_long_grounded_refusal_remains_bounded_and_keeps_error_code(tmp_path, projects, facts):
    env = environment(tmp_path, projects, facts, limits=BudgetLimits(tool_response_bytes=1024))
    contract = replace(env.service.contract, missing_information=('未确认的必要接口' * 300,))
    env.service.bind_contract(contract)
    response = call(env, 'write_candidate', write_payload())
    assert len(text_of(response).encode('utf-8')) <= 1024
    assert response.metadata['reproagent_error_code'] == 'GROUNDED_CONTRACT_REQUIRED'
    assert env.service.contract is contract and candidate_ids(env) == [] and not env.gate.finished
