"""The product's single assembly point: one infrastructure, two injection seams.

The product has exactly one infrastructure.  ``create_controller`` builds the SDK model
gateway and the SDK explorer factory over one model configuration and one task store, so
every model call a task makes -- contract analysis, exploration and verdict -- runs
through the same guarded factory.  There is no second runtime to fall back to: when the
SDK is not installed, the assembly fails with an install error instead of building one.

``gateway`` and ``explorer_factory`` stay injection points for tests and for custom domain
strategies.  They select no product backend and are recorded for what they are: an
injected gateway means this build cannot know the model infrastructure the run used, and
an injected explorer factory is a custom strategy, neither of which the product may
relabel as its own AgentScope infrastructure.

``model_backend`` and ``agent_backend`` are the flags the two pre-migration backends used.
Both still assemble this one infrastructure, so an old command keeps running, and both
warn that they are deprecated; an unknown value is refused rather than silently accepted.
"""
import os
import warnings

from .core.budget import BudgetedGateway
from .core.controller import Controller
from .core.models import INFRASTRUCTURE, NOT_RECORDED, component_identity
from .core.verifier import Verifier
from .exporter import Exporter
from .runner import Runner
from .store import TaskStore
from .workspace import Workspace

#: The flag values the product used to offer.  Both name the one infrastructure now.
LEGACY_BACKENDS = ('native', 'agentscope')
DEPRECATION_NOTICE = ('the %s flag is deprecated: ReproAgent runs on the AgentScope '
                      'infrastructure only, and the flag no longer selects a runtime')


def _accept_legacy_backend(value, name):
    """Refuse an unknown backend flag; accept a legacy one as a deprecated no-op."""
    if value is None:
        return
    if value not in LEGACY_BACKENDS:
        raise ValueError('unknown model or agent backend')
    warnings.warn(DEPRECATION_NOTICE % name, DeprecationWarning, stacklevel=3)


def create_controller(request, model, gateway=None, *, model_backend=None, agent_backend=None,
                      explorer_factory=None, transport=None, learning_gateway_factory=None):
    _accept_legacy_backend(model_backend, 'model_backend')
    _accept_legacy_backend(agent_backend, 'agent_backend')
    if request.language_id != 'python_pytest' or request.runtime_id != 'local':
        raise ValueError('only python_pytest/local are supported')
    if request.limits.model_cost_limit is not None:
        raise ValueError('hard model cost limits are unsupported by this adapter')
    store = TaskStore(request.output_dir)
    injected_gateway = gateway is not None
    injected_explorer = explorer_factory is not None
    recorded = {'model': model.model, 'model_injected': injected_gateway,
                'agent_backend': 'custom' if injected_explorer else INFRASTRUCTURE}
    if injected_gateway:
        # The caller owns this boundary, so the run cannot claim the product's own.
        recorded['model_backend'] = NOT_RECORDED
    else:
        from .adapters.agentscope.dependency import require_agentscope
        from .adapters.agentscope.gateway import AgentScopeModelGateway
        from .adapters.agentscope.model_factory import AgentScopeModelFactory
        recorded['agentscope_version'] = require_agentscope()
        recorded['model_backend'] = INFRASTRUCTURE
        gateway = AgentScopeModelGateway(AgentScopeModelFactory(model, store, transport=transport))
    if not injected_explorer:
        # The Controller drives exploration phases, so the only product strategy it can
        # build is the SDK one; an injected gateway is the caller's to pair with an
        # explorer of its own.
        if injected_gateway:
            raise ValueError('a Controller drives an exploration strategy: pass explorer_factory with an injected gateway')
        from .adapters.agentscope.explorer import agentscope_explorer_factory
        explorer_factory = agentscope_explorer_factory(model, store, transport=transport)
    workspace = Workspace(request.output_dir, store)
    runner = Runner(workspace, store)
    secrets = (os.environ.get(model.api_key_env, ''),)
    model_gateway = BudgetedGateway(gateway, store, secrets)
    verifier = Verifier(store, runner.adapter, model_gateway, secrets)
    experience_service = None
    if request.experience_file is not None:
        from .experience import ExperienceService
        if request.learn_experience and learning_gateway_factory is None:
            if injected_gateway:
                raise ValueError("an injected gateway with learning requires learning_gateway_factory")
            def learning_gateway_factory(task_store):
                return BudgetedGateway(AgentScopeModelGateway(AgentScopeModelFactory(model, task_store,
                    transport=transport)), task_store, secrets)
        experience_service = ExperienceService(request, store, learning_gateway_factory, secrets=secrets)
    return Controller(store, workspace, runner, model_gateway, verifier, Exporter(secrets), secrets,
                      explorer_factory, component_identity(**recorded), experience_service=experience_service)
