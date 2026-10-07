from typing import Protocol, runtime_checkable

from .models import (
    AgentAction, AgentContext, CallContext, Candidate, EnvironmentSnapshot,
    EvidenceContext, ExecutionSpec, FrameworkChecks, IssueContract, IssueDescription,
    LanguageInspection, ModelRequest, ModelResponse, ProbeArtifacts, ProbeResults,
    ProjectView, PythonPytestConfig, RawExecution, RunContext, RunWorkspace, TestObservation,
)
from .phase import PhaseResult


class ModelGateway(Protocol):
    async def complete(self, request: ModelRequest, context: CallContext) -> ModelResponse: ...


class LanguageAdapter(Protocol):
    def inspect(self, project: ProjectView, config: PythonPytestConfig) -> LanguageInspection: ...
    def describe_environment(self, inspection: LanguageInspection, probes: ProbeResults) -> EnvironmentSnapshot: ...
    def build_execution(self, candidate: Candidate, run: RunWorkspace, environment: EnvironmentSnapshot) -> ExecutionSpec: ...
    def normalize(self, raw: RawExecution, artifacts: ProbeArtifacts) -> TestObservation: ...
    def check_framework(self, candidate: Candidate, observation: TestObservation) -> FrameworkChecks: ...


class ExecutionBackend(Protocol):
    async def execute(self, spec: ExecutionSpec, context: RunContext) -> RawExecution: ...


class Explorer(Protocol):
    async def analyze(self, description: IssueDescription, evidence: EvidenceContext) -> IssueContract: ...
    async def next_action(self, context: AgentContext) -> AgentAction: ...


@runtime_checkable
class ExplorationRuntime(Protocol):
    """One task's exploration phases, run one at a time and closed once.

    ``explore`` runs the phase *context* describes and returns what the phase's gate holds;
    the runtime keeps the task's own message history across calls.  It raises rather than
    inventing a result when the task is cancelled or out of budget, and ``aclose`` releases
    the task's SDK session, so no work it started outlives the task.
    """

    async def explore(self, context: AgentContext) -> PhaseResult: ...
    async def aclose(self) -> None: ...
