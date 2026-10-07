from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    issue_url: str
    issue_hash: str
    buggy_repo: Path
    buggy_version: str
    fixed_repo: Path
    fixed_version: str
    buggy_python: str
    fixed_python: str
    allowed_description: str
    buggy_preconditions: tuple[str, ...] = ()
    fixed_preconditions: tuple[str, ...] = ()
    forbidden_materials: tuple[str, ...] = ()
    review_status: str = 'pending'
    target_modules: tuple[str, ...] = ()
    source_roots: tuple[str, ...] = ('.',)
    candidate_parent: str = 'tests'
    pytest_args: tuple[str, ...] = ('-q',)
    baseline_tests: tuple[str, ...] = ()
    buggy_source_hash: str = ''
    fixed_source_hash: str = ''


@dataclass(frozen=True)
class EvalResult:
    case_id: str
    status: str = 'not_run'
    evidence_level: str = 'NONE'
    environment_status: str = 'unknown'
    reproduced: bool = False
    export_replayed: bool | None = None
    differential_validated: bool = False
    human_judgement: bool | None = None
    duration: float | None = None
    cost_kind: str = 'unknown'
    cost_value: float | None = None
    model: str = ''
    model_base_url: str = ''
    budget: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    description_hash: str = ''
    buggy_version: str = ''
    fixed_version: str = ''
    human_supplements: int = 0
    model_backend: str = 'native'
    agent_backend: str = 'native'
    stop_reason: str = ''
    http_attempts: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    known_cost_subtotal: float = 0
    unknown_cost_attempts: int = 0
    # Mirrors the task record: not_provided/passed/failed/blocked. Kept last so existing
    # positional construction and older imported rounds are unaffected.
    fix_validation_status: str = 'not_provided'
