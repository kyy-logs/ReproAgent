"""The model must classify each assertion's basis without a legacy default."""
import pytest

from reproagent.core.protocol import validate_verdict, verdict_schema


BASES = ('current_requirement', 'unaffected_contract', 'target_old_behavior', 'unknown')
REF = {'path': 'input/issue.md', 'content_hash': 'h', 'start_line': 1, 'end_line': 1}


def answer(basis='current_requirement'):
    return {'classification': 'REPRODUCED', 'reason': '受控协议样本', 'evidence_refs': [dict(REF)],
        'expected_assertion': True, 'target_triggered': True, 'failure_matches_issue': True,
        'assertion_reviews': [{'assertion_id': 'A1', 'supported': True, 'basis': basis,
            'reason': '明确列出这条断言的依据类别', 'evidence_refs': [dict(REF)]}]}


def test_wire_schema_requires_a_closed_basis_enum_without_a_default():
    schema = verdict_schema(('A1',))['properties']['assertion_reviews']['items']
    assert 'basis' in schema['required']
    assert schema['properties']['basis'] == {'type': 'string', 'enum': list(BASES)}


@pytest.mark.parametrize('basis', BASES)
def test_valid_basis_is_preserved_for_the_program_acceptance_gate(basis):
    value = answer(basis)
    # Protocol validates structure, not semantics; the Verifier rejects a negative
    # basis even when this provider claimed supported=true. Never rewrite its claim.
    assert validate_verdict(value, ('A1',)) is value
    assert value['assertion_reviews'][0]['basis'] == basis
    assert value['assertion_reviews'][0]['supported'] is True


def test_missing_basis_is_not_silently_treated_as_a_current_requirement():
    value = answer()
    del value['assertion_reviews'][0]['basis']
    with pytest.raises(ValueError, match='basis'):
        validate_verdict(value, ('A1',))


@pytest.mark.parametrize('basis', ['existing_test', '', None, [], {}, 1])
def test_unknown_or_malformed_basis_is_not_accepted(basis):
    with pytest.raises(ValueError, match='basis'):
        validate_verdict(answer(basis), ('A1',))
