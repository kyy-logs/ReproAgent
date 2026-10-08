"""The product's exploration strategy, run as SDK phases.

Two things meet here and neither replaces the other.  The strategy is the product's: the
contract ``ReproAgent.analyze`` derives, the rules ``exploration_prompt`` states, the
candidate area the task was configured with, and the publication rules of the product
workspace.  The runtime is the SDK's: one agent per task with its own toolkit and message
history, and the phase result that ends a phase.  This adapter is the seam -- it binds the
contract of the phase about to run to the candidate service the phase's domain tools
publish through, and it closes the runtime once the task is over.

The Controller builds one of these per task, after the snapshot is frozen, and closes it
once; nothing a phase started outlives the task that ran it.
"""
from __future__ import annotations

from .dependency import require_agentscope
from .evidence import EvidenceLedger
from .model_factory import AgentScopeModelFactory
from .runtime import AgentScopeRuntime
from .snapshot_backend import SnapshotBackend
from .tools import build_toolkit
from ...core.agent import ReproAgent, exploration_prompt
from ...core.candidate_service import CandidateService
from ...core.phase import PhaseGate


class AgentScopeExplorer(ReproAgent):
    """One task's reproduction strategy, explored by the SDK phase runtime.

    The strategy half is inherited: ``analyze`` turns the issue into the contract, and the
    exploration prompt is the product's own.  The runtime half is one
    :class:`~reproagent.adapters.agentscope.runtime.AgentScopeRuntime` for the task, created
    on the first phase so it is built from that phase's contract, and closed with the task.

    Args:
        gateway: the structured-request boundary the contract analysis uses.
        context: budget and cancellation for every call the task makes.
        candidate_parent: the configured test area; candidates are installed only here.
        project: the frozen snapshot's view.  Every candidate is published against
            ``project.snapshot``; this adapter never freezes or guesses one.
        workspace: the product workspace that owns publication and its rules.
        model_factory: the SDK model factory every model of this task comes from; the
            runtime installs its own guard on the model it creates.
    """

    def __init__(self, gateway, context, candidate_parent='tests', *, project, workspace, model_factory):
        require_agentscope()
        super().__init__(gateway, context, candidate_parent)
        self.project, self.workspace, self.model_factory = project, workspace, model_factory
        self.store = workspace.store
        self.gate = PhaseGate()
        self.candidates = CandidateService(project, workspace, context)
        self.backend = SnapshotBackend(project, self.store, context)
        self.ledger = EvidenceLedger(project, self.store)
        self.runtime = None

    async def explore(self, phase):
        """Run the phase *phase* describes under the contract it carries.

        The contract is bound before the phase starts, so the candidate the phase publishes
        is the one this contract version and this frozen snapshot imply -- the phase cannot
        choose either.
        """
        self.candidates.bind_contract(phase.contract)
        if self.runtime is None:
            self.runtime = self._runtime(phase)
        return await self.runtime.explore(phase)

    async def aclose(self):
        """Close the task's SDK session and its models; nothing this explorer opened outlives it.

        The factory keeps one HTTP client per purpose it built, so closing it -- not only the
        runtime that used it -- is what releases the sockets a long multi-task process would
        otherwise accumulate.
        """
        runtime, self.runtime = self.runtime, None
        if runtime is not None:
            await runtime.aclose()
        factory, self.model_factory = self.model_factory, None
        if factory is not None:
            await factory.aclose()

    def _runtime(self, phase):
        """The one SDK runtime of this task, under the first phase's product prompt.

        The prompt is product policy and the test area of this task, both of which stay the
        same for every phase; the contract that changes between phases reaches the phase as
        its own message, never as a rewritten system prompt.
        """
        model = self.model_factory.create(self.context, 'exploration')
        toolkit = build_toolkit(self.backend, self.ledger, self.candidates, self.gate, self.context)
        return AgentScopeRuntime(model, toolkit, self.gate, self.context,
                                 system_prompt=exploration_prompt(phase, self.candidate_parent),
                                 store=self.store, secrets=getattr(self.gateway, 'secrets', ()))


def agentscope_explorer_factory(config, store, *, transport=None):
    """The product explorer over the SDK infrastructure, built per task.

    Returns:
        `callable`: the factory the Controller calls with ``(gateway, context,
        candidate_parent, *, project, workspace)``.  The SDK model factory is created per
        task, so a test can install its transport before the task's first request.
    """
    require_agentscope()

    def factory(gateway, context, candidate_parent, *, project, workspace):
        return AgentScopeExplorer(gateway, context, candidate_parent, project=project, workspace=workspace,
                                  model_factory=AgentScopeModelFactory(config, store, transport=transport))
    return factory


__all__ = ["AgentScopeExplorer", "agentscope_explorer_factory"]
