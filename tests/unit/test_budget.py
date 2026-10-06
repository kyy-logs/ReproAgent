import importlib
from decimal import Decimal

import pytest

from reproagent.core.models import BudgetLimits


def api():
    return importlib.import_module("reproagent.core.budget")


def test_twenty_steps_then_stop():
    module = api()
    budget = module.Budget(BudgetLimits(), clock=lambda: 0)
    for _ in range(20):
        budget.take_step()
    assert budget.steps_used == 20
    with pytest.raises(module.BudgetStopped) as error:
        budget.take_step()
    assert error.value.reason == "EXHAUSTED"


def test_command_uses_remaining_time():
    module = api()
    now = [0.0]
    budget = module.Budget(BudgetLimits(), clock=lambda: now[0])
    now[0] = 888
    assert budget.command_timeout() == 12
    now[0] = 900
    with pytest.raises(module.BudgetStopped):
        budget.check()


def test_unknown_cost_cannot_enable_hard_cost_limit():
    module = api()
    budget = module.Budget(BudgetLimits(model_cost_limit=0.5), clock=lambda: 0)
    with pytest.raises(ValueError, match="cost"):
        budget.reserve_cost(None)
    reservation = budget.reserve_cost(Decimal("0.4"))
    budget.settle_cost(reservation, None)
    with pytest.raises(module.BudgetStopped):
        budget.reserve_cost(Decimal("0.2"))


def test_cancel_blocks_new_work_but_allows_bounded_cleanup():
    module = api()
    budget = module.Budget(BudgetLimits(), clock=lambda: 0)
    budget.cancel()
    with pytest.raises(module.BudgetStopped) as error:
        budget.take_step()
    assert error.value.reason == "CANCELLED"
    assert budget.limits.cleanup_timeout_seconds == 10
    assert budget.limits.finalize_timeout_seconds == 5
