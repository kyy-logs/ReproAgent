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
