import asyncio
import importlib
import importlib.util
import os
import subprocess
import sys
import warnings

import pytest

from reproagent.app import LEGACY_BACKENDS, create_controller
from reproagent.core.models import INFRASTRUCTURE, NOT_RECORDED, ModelConfig
from tests.unit.test_controller import setup

MODEL = dict(base_url='https://offline.example/v1', model='offline')


def test_controller_injects_explorer_factory_and_records_actual_components(tmp_path, projects, facts):
    """The factory is handed the gateway, the call context, the test area and the task."""
    from tests.unit.test_controller import PhasePlan, publish
    request, model, _, context = setup(tmp_path, projects, facts)
    created = []
    plan = PhasePlan([publish()])
    def factory(gateway, ctx, candidate_parent, *, project, workspace):
        created.append((gateway, ctx, candidate_parent, project, workspace))
        return plan(gateway, ctx, candidate_parent, project=project, workspace=workspace)
    controller = create_controller(request, ModelConfig(), gateway=model, explorer_factory=factory)
    result = asyncio.run(controller.run(request, context))
    assert result.status.value == 'DONE' and len(created) == 1
    gateway, ctx, candidate_parent, project, workspace = created[0]
    assert gateway is controller.gateway and ctx is context and candidate_parent == 'tests'
    assert project.snapshot.snapshot_id and workspace is controller.workspace
    events = controller.store.read_events()[0]
    backend = next(event for event in events if event.kind == 'backend.selected')
    assert backend.payload['infrastructure'] == INFRASTRUCTURE
    # The caller built both boundaries here, so the run says exactly that instead of
    # borrowing the product's own infrastructure name for a model it did not create.
    assert backend.payload['model_backend'] == NOT_RECORDED
    assert backend.payload['agent_backend'] == 'custom'
    assert backend.payload['model_injected'] is True


@pytest.mark.parametrize('kwargs', [{'agent_backend':'wrong'}, {'model_backend':'wrong'}])
def test_backend_selection_rejects_unknown_values_before_model_calls(tmp_path, projects, facts, kwargs):
    request, model, *_ = setup(tmp_path, projects, facts)
    with pytest.raises(ValueError, match='backend'):
        create_controller(request, ModelConfig(), gateway=model, **kwargs)
    assert not model.messages


@pytest.mark.parametrize('kwargs', [{}, {'model_backend': 'native'}, {'model_backend': 'agentscope'},
                                    {'agent_backend': 'native'}, {'agent_backend': 'agentscope'},
                                    {'model_backend': 'native', 'agent_backend': 'agentscope'}])
def test_every_default_and_legacy_flag_builds_the_one_infrastructure(tmp_path, projects, facts, kwargs):
    """The default and both old flags assemble the same SDK gateway, and the old ones warn."""
    from reproagent.adapters.agentscope.gateway import AgentScopeModelGateway
    request, model, *_ = setup(tmp_path, projects, facts)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        controller = create_controller(request, ModelConfig(**MODEL), **kwargs)
    assert isinstance(controller.gateway.gateway, AgentScopeModelGateway)
    assert controller.backend_info['model_backend'] == INFRASTRUCTURE
    assert controller.backend_info['agent_backend'] == INFRASTRUCTURE
    assert controller.backend_info['infrastructure'] == INFRASTRUCTURE
    assert controller.backend_info['agentscope_version'] == '2.0.9'
    assert [str(warning.category.__name__) for warning in caught] == ['DeprecationWarning'] * len(kwargs)


def test_a_missing_sdk_is_an_install_error_with_no_native_fallback(tmp_path, projects, facts, monkeypatch):
    from importlib.metadata import PackageNotFoundError
    dependency = importlib.import_module('reproagent.adapters.agentscope.dependency')
    def missing(name): raise PackageNotFoundError(name)
    monkeypatch.setattr(dependency, 'version', missing)
    request, model, *_ = setup(tmp_path, projects, facts)
    with pytest.raises(ValueError, match='agentscope'):
        create_controller(request, ModelConfig(**MODEL))
    # The native model execution path does not exist any more, so none can run instead.
    assert importlib.util.find_spec('reproagent.adapters.models.provider') is None


def test_cli_help_advertises_the_deprecated_flags_without_importing_sdk():
    env = dict(os.environ)
    result = subprocess.run([sys.executable, '-c',
        "import sys; from reproagent.cli import main; "
        "\ntry: main(['run','--help'])\nfinally: assert 'agentscope' not in sys.modules"],
        capture_output=True, text=True, env=env, timeout=20)
    assert result.returncode == 0, result.stderr
    assert '--agent-backend' in result.stdout and '--model-backend' in result.stdout
    assert LEGACY_BACKENDS == ('native', 'agentscope')


def test_sdk_dependency_error_is_explicit_when_not_installed():
    from reproagent.adapters.agentscope.dependency import require_agentscope
    if importlib.util.find_spec('agentscope') is not None:
        assert require_agentscope() == '2.0.9'
    else:
        with pytest.raises(ValueError, match='agentscope.*install|install.*agentscope'):
            require_agentscope()
