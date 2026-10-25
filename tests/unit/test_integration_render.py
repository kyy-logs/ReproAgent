import base64
import json
from pathlib import Path

import pytest

from reproagent.integration.render import decode_binding, encode_binding, render_files


def _binding(tmp_path: Path) -> dict:
    return {
        'tool_python': str(tmp_path / "tool python" / "python's.exe"),
        'model_config': str(tmp_path / 'model config.json'),
        'repro_root': str(tmp_path / 'repo$root;中文'),
        'env_file': str(tmp_path / '.env'),
        'skill_root': str(tmp_path / "skill dir's"),
    }


def test_codex_policy_is_separate_yaml(tmp_path):
    files = render_files('codex', platform_name='nt', binding=_binding(tmp_path))

    assert 'agents/openai.yaml' in files
    policy = files['agents/openai.yaml'].decode('utf-8')
    assert 'allow_implicit_invocation: false' in policy
    assert 'allow_implicit_invocation' not in files['SKILL.md'].decode('utf-8')


def test_only_the_current_platform_script_is_rendered(tmp_path):
    windows = render_files('claude-code', platform_name='nt', binding=_binding(tmp_path))
    unix = render_files('claude-code', platform_name='posix', binding=_binding(tmp_path))

    assert 'scripts/reproagent.ps1' in windows
    assert 'scripts/reproagent.sh' not in windows
    assert 'scripts/reproagent.sh' in unix
    assert 'scripts/reproagent.ps1' not in unix


def test_global_skill_has_no_checkout_relative_links_or_fabricated_variables(tmp_path):
    for host in ('claude-code', 'codex'):
        files = render_files(host, platform_name='posix', binding=_binding(tmp_path))
        skill = files['SKILL.md'].decode('utf-8')
        assert '../../../docs' not in skill
        assert '../../../examples' not in skill
        assert '${CLAUDE_SKILL_DIR}' not in skill
        assert '${CODEX_SKILL_DIR}' not in skill


def test_skill_points_at_the_installed_script_directory(tmp_path):
    binding = {**_binding(tmp_path), 'skill_root': str(tmp_path / 'skill-root')}

    files = render_files('codex', platform_name='posix', binding=binding)

    skill = files['SKILL.md'].decode('utf-8')
    assert binding['skill_root'] in skill


def test_binding_round_trip_is_byte_exact(tmp_path):
    binding = _binding(tmp_path)

    encoded = encode_binding(binding)

    assert decode_binding(encoded) == {key: binding[key] for key in
                                       ('tool_python', 'model_config', 'repro_root', 'env_file')}


@pytest.mark.parametrize('extra', [
    {'api_key': 'canary-secret'},
    {'messages': 'model output'},
])
def test_binding_refuses_non_path_or_secret_fields(tmp_path, extra):
    binding = {**_binding(tmp_path), **extra}

    with pytest.raises(ValueError):
        encode_binding(binding)


def test_binding_accepts_missing_optional_paths(tmp_path):
    python = str(tmp_path / 'python')
    assert decode_binding(encode_binding({'tool_python': python,
        'model_config': None, 'repro_root': None, 'env_file': None})) == {
            'tool_python': python, 'model_config': None,
            'repro_root': None, 'env_file': None}


def test_wrapper_embeds_only_the_four_binding_paths(tmp_path):
    binding = _binding(tmp_path)

    files = render_files('claude-code', platform_name='posix', binding=binding)
    wrapper = files['scripts/reproagent.sh'].decode('utf-8')

    encoded = wrapper.split('REPROAGENT_BINDING_B64=', 1)[1].splitlines()[0].strip().strip("'\"")
    assert json.loads(base64.b64decode(encoded)) == {
        'tool_python': binding['tool_python'],
        'model_config': binding['model_config'],
        'repro_root': binding['repro_root'],
        'env_file': binding['env_file'],
    }
    assert 'canary-secret' not in wrapper


def test_powershell_wrapper_quotes_tool_python_safely(tmp_path):
    binding = _binding(tmp_path)

    wrapper = render_files('claude-code', platform_name='nt',
                           binding=binding)['scripts/reproagent.ps1'].decode('utf-8')

    assert "python''s.exe" in wrapper
    assert '$env:REPROAGENT_TOOL_PYTHON' in wrapper


def test_posix_wrapper_uses_shlex_quoting_and_exec(tmp_path):
    binding = _binding(tmp_path)

    wrapper = render_files('claude-code', platform_name='posix',
                           binding=binding)['scripts/reproagent.sh'].decode('utf-8')

    assert 'exec ' in wrapper
    assert 'eval ' not in wrapper
    assert 'source ' not in wrapper
    assert "python'\"'\"'s.exe" in wrapper


@pytest.mark.parametrize('host', ['claude-code', 'codex'])
def test_windows_skill_commands_quote_paths_as_literals(tmp_path, host):
    root = tmp_path / "skill's $x $(literal) `name"
    skill = render_files(host, platform_name='nt',
                         binding={**_binding(tmp_path), 'skill_root': str(root)})['SKILL.md'].decode()
    path = str(root / 'scripts/reproagent.ps1')
    quoted = "'" + path.replace("'", "''") + "'"
    commands = [line for line in skill.splitlines() if line.startswith('powershell.exe ')]
    assert len(commands) == 3
    assert all('-File ' + quoted in line for line in commands)


@pytest.mark.parametrize('platform,flag', [('nt', '-ModelConfig'), ('posix', '--model-config')])
def test_codex_optional_flags_match_the_generated_wrapper(tmp_path, platform, flag):
    skill = render_files('codex', platform_name=platform, binding=_binding(tmp_path))['SKILL.md'].decode()
    optional = skill.split('## Workflow', 1)[0].split('Optional run arguments:', 1)[1]
    assert flag in optional
    if platform == 'nt':
        assert '--model-config' not in optional
        assert all(name in optional for name in ('-FixedRepo', '-FixedPython', '-ToolPython'))
