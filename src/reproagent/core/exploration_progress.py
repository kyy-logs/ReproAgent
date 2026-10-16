"""Read-only exploration budget modes; admission remains the Budget's job."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExplorationStepView:
    mode: str
    total_steps: int
    used_steps: int
    remaining_steps: int
    soft_threshold: int
    next_call_index: int | None


def step_budget_view(total_steps: int, used_steps: int) -> ExplorationStepView:
    if (type(total_steps) is not int or total_steps <= 0
            or type(used_steps) is not int or used_steps < 0):
        raise ValueError('step counters must be positive total and nonnegative used integers')
    threshold = (3 * total_steps) // 4
    mode = ('exhausted' if used_steps >= total_steps else
            'converging' if used_steps >= threshold else 'normal')
    return ExplorationStepView(mode, total_steps, used_steps, max(0, total_steps - used_steps),
                               threshold, None if used_steps >= total_steps else used_steps + 1)


@dataclass(frozen=True, slots=True)
class ExecutionBudgetPolicy:
    mode: str
    reader_reserve: int
    original_execution_count: int
    readers_available_next_call: bool


def execution_budget_policy(total_steps: int, used_steps: int, original_execution_count: int) -> ExecutionBudgetPolicy:
    view = step_budget_view(total_steps, used_steps)
    if type(original_execution_count) is not int or original_execution_count < 0:
        raise ValueError('original execution count must be a nonnegative integer')
    repair = original_execution_count > 0
    reserve = min(2, (total_steps+9)//10) if repair else min(6, (3*total_steps+9)//10)
    # Permissions run after this model call has consumed its step.
    return ExecutionBudgetPolicy('repair' if repair else 'first_execution', reserve,
        original_execution_count, view.remaining_steps - 1 > reserve)
