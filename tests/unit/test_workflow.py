"""The deterministic half of the candidate lifecycle.

The policy is a pure function of the task state, so every rule below is asserted
without a model, a runner or a store: who decides is the whole subject here.
"""

from reproagent.core.models import AgentAction
from reproagent.core.workflow import WorkflowState, choose_workflow


def state(**overrides):
    values = {'steps_remaining':12, 'grounded':True, 'candidate_id':'candidate-1', 'candidate_contract_version':1,
              'current_contract_version':1, 'has_execution':False, 'reproduced':False, 'repeated_action':None}
    return WorkflowState(**{**values, **overrides})


def read(path='example/parser.py'):
    return AgentAction('read_file', {'path':path, 'start':1, 'end':2})


def test_an_unexecuted_candidate_is_forced_to_run_with_its_real_id():
    decision = choose_workflow(state(has_execution=False, reproduced=False))
    assert decision.forced_action == AgentAction('run_candidate', {'candidate_id':'candidate-1'})
    assert decision.allowed_actions == ('run_candidate',)
    assert decision.blocked_actions == ()
    assert decision.reason_code == 'FORCE_RUN_CANDIDATE'


def test_a_reproduced_candidate_is_forced_to_submit_with_its_real_id():
    decision = choose_workflow(state(has_execution=True, reproduced=True))
    assert decision.forced_action == AgentAction('submit_candidate', {'candidate_id':'candidate-1'})
    assert decision.allowed_actions == ('submit_candidate',)
    assert decision.reason_code == 'FORCE_SUBMIT_CANDIDATE'


def test_an_executed_candidate_without_a_reproduced_verdict_is_not_forced():
    decision = choose_workflow(state(has_execution=True, reproduced=False))
    assert decision.forced_action is None and decision.reason_code == 'MODEL_SELECTS'
    assert {'write_candidate','run_candidate','submit_candidate'} <= set(decision.allowed_actions)


def test_two_remaining_steps_reserve_write_run_submit():
    # With no candidate and two steps left, only the write/run/submit sequence is
    # worth spending them on: a read here would leave the new candidate unrunnable.
    decision = choose_workflow(state(steps_remaining=2, candidate_id='', candidate_contract_version=0))
    assert decision.forced_action is None
    assert 'write_candidate' in decision.allowed_actions
    assert 'read_file' not in decision.allowed_actions and 'search_code' not in decision.allowed_actions
    # One step above the reserve, reading is still allowed.
    assert 'read_file' in choose_workflow(state(steps_remaining=5, candidate_id='', candidate_contract_version=0)).allowed_actions


def test_missing_facts_still_allow_information():
    decision = choose_workflow(state(grounded=False, candidate_id='', candidate_contract_version=0))
    assert decision.forced_action is None
    assert set(decision.allowed_actions) == {'search_code','read_file','revise_contract','request_information'}
    assert not {'write_candidate','run_candidate','submit_candidate'} & set(decision.allowed_actions)


def test_a_revised_contract_cannot_submit_a_stale_candidate():
    stale = state(candidate_contract_version=1, current_contract_version=2, has_execution=True, reproduced=True)
    decision = choose_workflow(stale)
    assert decision.forced_action is None
    # The explorer may still ask for it; the Controller's own verdict check refuses it.
    assert 'submit_candidate' in decision.allowed_actions
    # Nor may the stale candidate be run again.
    assert choose_workflow(state(candidate_contract_version=1, current_contract_version=2)).forced_action is None


def test_a_repeated_action_is_blocked_without_blocking_other_files():
    decision = choose_workflow(state(candidate_id='', candidate_contract_version=0, repeated_action=read()))
    assert decision.blocked_actions == (read(),)
    assert read('example/other.py') not in decision.blocked_actions
    # Reading a different legitimate file stays possible.
    assert 'read_file' in decision.allowed_actions


def test_a_repeated_forced_action_hands_the_choice_back():
    repeated = AgentAction('run_candidate', {'candidate_id':'candidate-1'})
    decision = choose_workflow(state(has_execution=False, repeated_action=repeated))
    assert decision.forced_action is None and decision.reason_code == 'REPEATED_ACTION'
    assert decision.blocked_actions == (repeated,)
