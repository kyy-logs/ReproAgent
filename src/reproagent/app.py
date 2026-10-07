import os
from .adapters.models.provider import ChatCompletionGateway
from .core.budget import BudgetedGateway
from .core.controller import Controller
from .core.verifier import Verifier
from .exporter import Exporter
from .runner import Runner
from .store import TaskStore
from .workspace import Workspace


def create_controller(request, model, gateway=None, *, model_backend='native', agent_backend='native', explorer_factory=None):
    if model_backend not in ('native', 'agentscope') or agent_backend not in ('native', 'agentscope'):
        raise ValueError('unknown model or agent backend')
    if request.language_id != 'python_pytest' or request.runtime_id != 'local':
        raise ValueError('only python_pytest/local are supported')
    if request.limits.model_cost_limit is not None:
        raise ValueError('hard model cost limits are unsupported by this adapter')
    info = {'model_backend':model_backend, 'agent_backend':'custom' if explorer_factory else agent_backend, 'model':model.model,
            'model_injected':gateway is not None}
    if 'agentscope' in (model_backend, agent_backend):
        from .adapters.agentscope.dependency import require_agentscope
        info['agentscope_version'] = require_agentscope()
    store = TaskStore(request.output_dir)
    injected = gateway is not None
    if gateway is None:
        if model_backend == 'agentscope':
            from .adapters.agentscope.gateway import AgentScopeModelGateway
            from .adapters.agentscope.model_factory import AgentScopeModelFactory
            gateway = AgentScopeModelGateway(AgentScopeModelFactory(model, store))
        else:
            gateway = ChatCompletionGateway(model)
    if explorer_factory is None:
        # The Controller drives exploration phases, so the only product strategy it can
        # build is the SDK one; an injected gateway is the caller's to pair with an
        # explorer of its own.  The legacy backend flags still select the model boundary
        # above and are mapped to the single infrastructure in Task 9 of the migration.
        if injected:
            raise ValueError('a Controller drives an exploration strategy: pass explorer_factory with an injected gateway')
        from .adapters.agentscope.explorer import agentscope_explorer_factory
        explorer_factory = agentscope_explorer_factory(model, store)
    workspace = Workspace(request.output_dir, store)
    runner = Runner(workspace, store)
    secrets = (os.environ.get(model.api_key_env, ''),)
    model_gateway = BudgetedGateway(gateway, store, secrets)
    verifier = Verifier(store, runner.adapter, model_gateway, secrets)
    return Controller(store, workspace, runner, model_gateway, verifier, Exporter(secrets), secrets, explorer_factory, info)
