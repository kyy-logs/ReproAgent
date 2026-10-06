import asyncio
import importlib.util
import json
import os
import subprocess
import sys

import pytest

from reproagent.app import create_controller
from reproagent.core.agent import ReproAgent
from reproagent.core.models import ModelConfig
from tests.unit.test_controller import setup


def test_controller_injects_explorer_factory_and_records_actual_backends(tmp_path, projects, facts):
    request, model, _, context = setup(tmp_path, projects, facts)
    created = []
    def factory(gateway, ctx, candidate_parent):
        created.append((gateway, ctx, candidate_parent))
        return ReproAgent(gateway, ctx, candidate_parent)
    controller = create_controller(request, ModelConfig(), gateway=model, explorer_factory=factory)
    result = asyncio.run(controller.run(request, context))
    assert result.status.value == 'DONE' and len(created) == 1
    events = controller.store.read_events()[0]
    backend = next(event for event in events if event.kind == 'backend.selected')
    assert backend.payload['model_backend'] == 'native'
    assert backend.payload['agent_backend'] == 'custom'


@pytest.mark.parametrize('kwargs', [{'agent_backend':'wrong'}, {'model_backend':'wrong'}])
def test_backend_selection_rejects_unknown_values_before_model_calls(tmp_path, projects, facts, kwargs):
    request, model, *_ = setup(tmp_path, projects, facts)
    with pytest.raises(ValueError, match='backend'):
        create_controller(request, ModelConfig(), gateway=model, **kwargs)
    assert not model.messages


def test_native_cli_help_advertises_backend_choices_without_importing_sdk():
    env = dict(os.environ)
    result = subprocess.run([sys.executable, '-c',
        "import sys; from reproagent.cli import main; "
        "\ntry: main(['run','--help'])\nfinally: assert 'agentscope' not in sys.modules"],
        capture_output=True, text=True, env=env, timeout=20)
    assert result.returncode == 0, result.stderr
    assert '--agent-backend' in result.stdout and '--model-backend' in result.stdout


def test_sdk_dependency_error_is_explicit_when_not_installed():
    from reproagent.adapters.agentscope.dependency import require_agentscope
    if importlib.util.find_spec('agentscope') is not None:
        assert require_agentscope() == '2.0.9'
    else:
        with pytest.raises(ValueError, match='agentscope.*install|install.*agentscope'):
            require_agentscope()
