from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

JsonValue = Any


class TaskState(StrEnum):
    PREPARING = "PREPARING"
    ANALYZING = "ANALYZING"
    GENERATING = "GENERATING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    REPLAYING = "REPLAYING"
    EXPORTING = "EXPORTING"
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    EXHAUSTED = "EXHAUSTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CandidateClass(StrEnum):
    ENVIRONMENT_BLOCKED = "ENVIRONMENT_BLOCKED"
    INVALID_CANDIDATE = "INVALID_CANDIDATE"
    NOT_REPRODUCED = "NOT_REPRODUCED"
    REPRODUCED = "REPRODUCED"


class EvidenceLevel(StrEnum):
    NONE = "NONE"
    SINGLE_OBSERVATION = "SINGLE_OBSERVATION"
    REPEATED_OBSERVATION = "REPEATED_OBSERVATION"
    DIFFERENTIAL_VALIDATED = "DIFFERENTIAL_VALIDATED"


# Closed vocabulary for the fixed-version check. No value claims more than was observed:
# "passed" means the same candidate passed on the fixed version, "failed" that it did not,
# "blocked" that the fixed version could not be prepared, and "not_provided" that no
# differential check was requested or reached.
FIX_VALIDATION_STATUSES = frozenset({"not_provided", "passed", "failed", "blocked"})

# The component identity a run records for itself, as field names shared by the task
# report and the evaluation summary. These describe what the product ran on, not what a
# model reported about itself: `infrastructure` is the SDK the strategy runs on, `strategy`
# is the product's reproduction strategy and `strategy_version` its version, and
# `agentscope_version` is the SDK version actually present. The names are consumed by the
# evaluation summary, so they must stay stable.
INFRASTRUCTURE = "agentscope"
STRATEGY = "reproagent"
STRATEGY_VERSION = "1"
IDENTITY_FIELDS = ("infrastructure", "agentscope_version", "strategy", "strategy_version",
                   "model_backend", "agent_backend")
# What a record that never carried one of these fields says, so a reader can tell "this run
# did not record it" from an actual component name standing in for it.
NOT_RECORDED = "not_recorded"


def installed_agentscope_version():
    """The SDK version present in this environment, or ``''`` when it is not installed."""
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("agentscope")
    except PackageNotFoundError:
        return ""


def component_identity(**recorded):
    """The product's component identity, under whatever a run actually recorded.

    Every key the run recorded wins, so a run that selected another backend, or a record
    written before these fields existed, keeps saying what it used instead of being
    relabelled by a later build.
    """
    identity = {"infrastructure": INFRASTRUCTURE, "agentscope_version": installed_agentscope_version(),
                "strategy": STRATEGY, "strategy_version": STRATEGY_VERSION,
                "model_backend": INFRASTRUCTURE, "agent_backend": INFRASTRUCTURE}
    identity.update({key: value for key, value in recorded.items() if value is not None})
    return identity


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    agent_steps: int = 20
    command_timeout_seconds: float = 60
    task_timeout_seconds: float = 900
    cleanup_timeout_seconds: float = 10
    finalize_timeout_seconds: float = 5
    model_cost_limit: float | None = None
    log_bytes: int = 33554432
    tool_response_bytes: int = 32768

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
                raise ValueError(f"limits.{name} must be a positive finite number")
        for name in ("agent_steps", "log_bytes", "tool_response_bytes"):
            if not isinstance(getattr(self, name), int):
                raise ValueError(f"limits.{name} must be an integer")


@dataclass(frozen=True, slots=True)
class PythonPytestConfig:
    python: str = ""
    pytest_args: tuple[str, ...] = ("-q",)
    baseline_tests: tuple[str, ...] = ()
    target_modules: tuple[str, ...] = ()
    source_roots: tuple[str, ...] = ()
    candidate_parent: str = "tests"
    mutable_paths: tuple[str, ...] = (".pytest_cache", "__pycache__")


@dataclass(frozen=True, slots=True)
class ModelConfig:
    base_url: str = ""
    model: str = ""
    api_key_env: str = "REPROAGENT_API_KEY"
    max_output_tokens: int = 4096
    output_limit_field: str = "max_completion_tokens"
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    thinking_mode: str | None = None
    #: The provider's sampling temperature, left to the provider when it is not set.  A run
    #: that does not fix it cannot be compared with another: the same configuration over the
    #: same cases produced different outcomes each time while the default applied.
    temperature: float | None = None

    def __post_init__(self):
        if not isinstance(self.max_output_tokens, int) or isinstance(self.max_output_tokens, bool) or self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be a positive integer")
        if self.output_limit_field not in ("max_completion_tokens", "max_tokens"):
            raise ValueError("unsupported output_limit_field")
        if self.thinking_mode not in (None, "enabled", "disabled"):
            raise ValueError("thinking_mode must be one of None, enabled, disabled")
        if self.temperature is not None and (isinstance(self.temperature, bool)
                                            or not isinstance(self.temperature, (int, float))
                                            or not 0.0 <= self.temperature <= 2.0):
            raise ValueError("temperature must be a number between 0 and 2, or None")


@dataclass(frozen=True, slots=True)
class TaskRequest:
    repo: Path
    output_dir: Path
    issue_file: Path
    task_id: str = ""
    language_id: str = "python_pytest"
    runtime_id: str = "local"
    limits: BudgetLimits = field(default_factory=BudgetLimits)
    language: PythonPytestConfig = field(default_factory=PythonPytestConfig)
    experience_file: Path | None = None
    learn_experience: bool = True


@dataclass(frozen=True, slots=True)
class FixValidationRequest:
    repo: Path
    python: str = ""


@dataclass(frozen=True, slots=True)
class IssueDescription:
    text: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class SourceRef:
    path: str
    content_hash: str
    start_line: int = 1
    end_line: int = 1
    kind: str = "issue"
    version: int = 1


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    path: str
    content_hash: str
    start_line: int = 1
    end_line: int = 1


@dataclass(frozen=True, slots=True)
class IssueContract:
    contract_id: str
    version: int = 1
    description_hash: str = ""
    trigger: str = ""
    expected: str = ""
    reported_actual: str = ""
    observable_checks: tuple[str, ...] = ()
    sources: tuple[SourceRef, ...] = ()
    assumptions: tuple[str, ...] = ()
    missing_information: tuple[str, ...] = ()
    revision_reason: str = ""


@dataclass(frozen=True, slots=True)
class FileEntry:
    path: str
    content_hash: str
    size: int
    role: str = "test"


@dataclass(frozen=True, slots=True)
class DraftFile:
    path: str
    content: bytes
    role: str = "test"


@dataclass(frozen=True, slots=True)
class CodeSnapshot:
    snapshot_id: str
    root: Path
    manifest_hash: str
    files: tuple[FileEntry, ...]
    excluded: tuple[str, ...] = ()
    git_state: str = ""


@dataclass(frozen=True, slots=True)
class CandidateDraft:
    files: tuple[DraftFile, ...]
    snapshot_id: str
    contract_id: str
    contract_version: int
    hypothesis: str
    candidate_id: str = ""
    parent_id: str = ""
    language_id: str = "python_pytest"
    selectors: tuple[str, ...] = ()
    run_options: tuple[str, ...] = ()
    expectation_sources: tuple[SourceRef, ...] = ()
    fixture_refs: tuple[SourceRef, ...] = ()
    preconditions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: str
    snapshot_id: str
    contract_id: str
    contract_version: int
    files: tuple[FileEntry, ...]
    storage_root: Path
    manifest_hash: str
    hypothesis: str = ""
    parent_id: str = ""
    language_id: str = "python_pytest"
    selectors: tuple[str, ...] = ()
    run_options: tuple[str, ...] = ()
    expectation_sources: tuple[SourceRef, ...] = ()
    fixture_refs: tuple[SourceRef, ...] = ()
    preconditions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectView:
    snapshot: CodeSnapshot
    candidates: tuple[Candidate, ...] = ()


@dataclass(frozen=True, slots=True)
class RunWorkspace:
    run_id: str
    root: Path
    temp_root: Path
    snapshot_id: str
    candidate_id: str = ""


@dataclass(frozen=True, slots=True)
class ProtectionCheck:
    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    candidate_ok: bool = True

    @property
    def ok(self):
        return not (self.modified or self.deleted or self.added) and self.candidate_ok


@dataclass(frozen=True, slots=True)
class ExecutionSpec:
    spec_id: str
    argv: tuple[str, ...]
    cwd: Path
    run_id: str = ""
    snapshot_id: str = ""
    candidate_id: str = ""
    manifest_hash: str = ""
    contract_id: str = ""
    contract_version: int = 1
    execution_role: str = "original"
    env_names: tuple[str, ...] = ()
    env_overrides: dict[str, str] = field(default_factory=dict)
    probe_path: Path | None = None
    target_modules: tuple[str, ...] = ()
    source_roots: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LanguageInspection:
    config: PythonPytestConfig
    probes: tuple[ExecutionSpec, ...] = ()
    missing_information: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProbeArtifacts:
    path: Path


@dataclass(frozen=True, slots=True)
class RawExecution:
    spec_id: str
    exit_code: int | None
    duration: float
    stop_reason: str
    stdout_ref: EvidenceRef
    stderr_ref: EvidenceRef
    cleanup_ok: bool
    probe_artifacts: ProbeArtifacts | None = None
    log_truncated: bool = False


@dataclass(frozen=True, slots=True)
class ProbeResults:
    results: tuple[RawExecution, ...] = ()


@dataclass(frozen=True, slots=True)
class EnvironmentSnapshot:
    environment_id: str
    python: str
    tool_version: str = ""
    source_roots: tuple[str, ...] = ()
    target_modules: tuple[str, ...] = ()
    candidate_parent: str = "tests"
    pytest_args: tuple[str, ...] = ("-q",)
    preconditions: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ("file_copy", "process_tree_cleanup")
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TestObservation:
    collected: bool = False
    executed: bool = False
    probe_complete: bool = False
    tests: tuple[dict[str, Any], ...] = ()
    target_origins: dict[str, str] = field(default_factory=dict)
    failure_refs: tuple[EvidenceRef, ...] = ()
    framework_details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FrameworkChecks:
    blocked: tuple[str, ...] = ()
    invalid: tuple[str, ...] = ()
    suspicious: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    run_id: str
    spec_id: str
    snapshot_id: str
    environment_id: str
    candidate_id: str
    manifest_hash: str
    contract_id: str
    contract_version: int
    raw: RawExecution
    observation: TestObservation
    protection: ProtectionCheck
    execution_role: str = "original"


@dataclass(frozen=True, slots=True)
class ModelRequest:
    messages: tuple[dict[str, str], ...]
    response_kind: str = "action"
    max_output_tokens: int | None = None

    def __post_init__(self):
        if self.max_output_tokens is None:
            return
        if isinstance(self.max_output_tokens, bool) or not isinstance(self.max_output_tokens, int) or self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be None or a positive integer")


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    request_id: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    cost_kind: str = "unknown"
    cost_value: float | None = None
    duration: float = 0
    finish_reason: str = "stop"


@dataclass(frozen=True, slots=True)
class CallContext:
    budget: Any
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(frozen=True, slots=True)
class RunContext(CallContext):
    run_id: str = ""
    private_env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    sources: tuple[SourceRef, ...] = ()
    texts: tuple[str, ...] = ()
    previous_contract: IssueContract | None = None


@dataclass(frozen=True, slots=True)
class ExperienceSummary:
    id: str
    summary: str


@dataclass(frozen=True, slots=True)
class AgentContext:
    contract: IssueContract
    project: ProjectView
    history: tuple[dict[str, Any], ...] = ()
    feedback: str = ""
    issue: IssueDescription | None = None
    experience_summaries: tuple[ExperienceSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class Verdict:
    classification: CandidateClass | None
    reason: str
    evidence_refs: tuple[EvidenceRef, ...] = ()
    unmet_checks: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    next_action: str = "explore"
    candidate_id: str = ""
    manifest_hash: str = ""
    contract_id: str = ""
    contract_version: int = 1
    run_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskResult:
    task_id: str
    status: TaskState
    stop_reason: str = ""
    evidence_level: EvidenceLevel = EvidenceLevel.NONE
    accepted_candidate_id: str = ""
    export_state: str = "pending"
    uncertainties: tuple[str, ...] = ()
    duration: float = 0
    # Whether an explicitly supplied fixed version was checked against the same candidate.
    # A repeated observation of the original failure and a differential success are separate
    # claims, so this stays its own field next to the evidence level. Records written before
    # it existed load with the default.
    fix_validation_status: str = "not_provided"

    def __post_init__(self):
        if self.fix_validation_status not in FIX_VALIDATION_STATUSES:
            raise ValueError(f"unsupported fix_validation_status: {self.fix_validation_status}")


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    package_kind: str
    root: Path
    manifest_hash: str
    files: tuple[FileEntry, ...]
    candidate_id: str = ""
    event_cutoff: int = -1


@dataclass(frozen=True, slots=True)
class TaskEvent:
    seq: int
    kind: str
    refs: tuple[str, ...]
    payload: dict[str, Any]
    timestamp: float


Record = Any
