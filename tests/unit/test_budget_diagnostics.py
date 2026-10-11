import pytest
from decimal import Decimal
from reproagent.core.budget import Budget,BudgetStopped
from reproagent.core.models import BudgetLimits
from reproagent.observability import TraceRecorder,mark


@pytest.mark.parametrize("dimension",["steps","time","cost","cancelled"])
def test_stop_dimension_comes_from_budget_branch(dimension):
    now=[0.]
    budget=Budget(BudgetLimits(agent_steps=1,task_timeout_seconds=10,model_cost_limit=1),clock=lambda:now[0])
    if dimension=="steps": budget.take_step()
    elif dimension=="time": now[0]=10
    elif dimension=="cost": budget.cost_spent=Decimal("1")
    else: budget.cancel()
    with pytest.raises(BudgetStopped) as caught: budget.take_step()
    assert caught.value.dimension==dimension
    assert caught.value.reason==("CANCELLED" if dimension=="cancelled" else "EXHAUSTED")


def test_budget_snapshot_is_read_only():
    from reproagent.observability_decisions import budget_snapshot
    b=Budget(BudgetLimits(agent_steps=2),clock=lambda:7.)
    b.take_step();deadline=b.deadline
    data=budget_snapshot(b)
    assert data["steps_remaining"]==1 and data["cost_limit"] is None
    assert b.steps_used==1 and b.deadline==deadline and not b._reservations


def test_stop_summary_survives_span_cap(monkeypatch):
    import reproagent.observability as module
    monkeypatch.setattr(module,"MAX_SPANS",1)
    with TraceRecorder() as recorder:
        mark("task.stopped",attributes={"stop_reason":"EXHAUSTED","budget_dimension":"steps"})
        doc=recorder.finish(task_id="one",status="EXHAUSTED",main_duration=1,
            stop_reason="EXHAUSTED",budget={"steps_remaining":0})
    assert doc["diagnostic_summary"]["stop_reason"]=="EXHAUSTED"
    assert doc["diagnostic_summary"]["budget"]["steps_remaining"]==0
    assert doc["diagnostic_summary"]["budget_dimension"]=="steps"
