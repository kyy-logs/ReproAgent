import pytest
from reproagent.core.protocol import verdict_schema, validate_verdict

REF={'path':'issue.md','content_hash':'a'*64,'start_line':1,'end_line':1}


def answer():
    return {'classification':'REPRODUCED','reason':'fixture','evidence_refs':[REF],
        'expected_assertion':True,'target_triggered':True,'failure_matches_issue':True,
        'assertion_reviews':[], 'oracle_reviews':[{'check_id':'O1','role':'regression',
            'supported':True,'basis':'desired_behavior','reason':'documented option',
            'evidence_refs':[REF],'source_quote':'Use new_option=True'}]}


def test_oracle_reviews_are_required_and_cover_every_generated_check():
    assert 'oracle_reviews' in verdict_schema((),('O1',))['required']
    assert validate_verdict(answer(),(),('O1',))['oracle_reviews'][0]['check_id']=='O1'
    for rows in ([],[answer()['oracle_reviews'][0]]*2):
        data=answer();data['oracle_reviews']=rows
        with pytest.raises(ValueError): validate_verdict(data,(),('O1',))


@pytest.mark.parametrize('fault',['reported_actual','unknown','no_quote','no_refs','bad_role'])
def test_unsupported_or_unquoted_basis_cannot_approve_an_oracle(fault):
    data=answer();row=data['oracle_reviews'][0]
    if fault in ('reported_actual','unknown'): row['basis']=fault
    elif fault=='no_quote': row['source_quote']=''
    elif fault=='no_refs': row['evidence_refs']=[]
    else: row['role']='unconditional_success'
    with pytest.raises(ValueError): validate_verdict(data,(),('O1',))


def test_execution_control_can_use_existing_semantics_instead_of_issue_literal():
    data=answer();data['oracle_reviews'][0].update(role='execution_control',basis='existing_semantics')
    assert validate_verdict(data,(),('O1',))
