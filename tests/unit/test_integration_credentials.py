import json
import os
import subprocess
from pathlib import Path

import pytest

from reproagent.integration import credentials
from reproagent.integration.credentials import (
    CredentialError,
    default_repro_root,
    discover_source,
    load_binding,
    load_child_environment,
)


def _model(tmp_path: Path, **overrides) -> Path:
    data = {"base_url": "https://api.deepseek.com", "model": "deepseek-chat",
            "api_key_env": "DEEPSEEK_API_KEY"}
    data.update(overrides)
    if data.get('api_key_env') is None:
        data.pop('api_key_env')
    path = tmp_path / 'model.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


def _binding(tmp_path: Path, *, model_overrides=None, **overrides) -> dict:
    values = {
        'tool_python': str(tmp_path / 'python'),
        'model_config': str(_model(tmp_path, **(model_overrides or {}))),
        'repro_root': str(tmp_path / 'repro-root'),
        'env_file': None,
    }
    values.update(overrides)
    return load_binding(**values)


def _write_env(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def test_no_model_config_does_not_guess_deepseek(tmp_path):
    binding = load_binding(tool_python=tmp_path / 'python', model_config=None,
                           repro_root=tmp_path / 'root', env_file=None)

    assert binding['model_config_status'] == 'missing'
    assert binding['api_key_env'] is None
    source = discover_source(binding, environ={'DEEPSEEK_API_KEY': 'canary'},
                             platform_name='nt')
    assert source['kind'] == 'missing'
    assert source['reason'] == 'model_config_required'


def test_binding_uses_product_default_key_name(tmp_path):
    binding = _binding(tmp_path, model_overrides={'api_key_env': None})

    assert binding['model_config_status'] == 'ready'
    assert binding['api_key_env'] == 'REPROAGENT_API_KEY'
    assert binding['base_url'] == 'https://api.deepseek.com'
    assert binding['model'] == 'deepseek-chat'


def test_wheel_location_is_not_repro_root(tmp_path):
    installed = tmp_path / 'site-packages' / 'reproagent'
    installed.mkdir(parents=True)
    module_file = installed / '__init__.py'
    module_file.write_text('', encoding='utf-8')

    assert default_repro_root(module_file) is None


def test_source_checkout_is_repro_root(tmp_path):
    root = tmp_path / 'repo'
    package = root / 'src' / 'reproagent'
    package.mkdir(parents=True)
    (package / 'cli.py').write_text('', encoding='utf-8')
    module_file = package / '__init__.py'
    module_file.write_text('', encoding='utf-8')
    (root / 'pyproject.toml').write_text('[project]\nname = "reproagent-local"\n',
                                         encoding='utf-8')

    assert default_repro_root(module_file) == root


def test_explicit_missing_model_config_is_a_parameter_error(tmp_path):
    with pytest.raises(ValueError):
        load_binding(tool_python=tmp_path / 'python',
                     model_config=tmp_path / 'missing.json', repro_root=None, env_file=None)


def test_malformed_model_config_is_invalid_not_a_guess(tmp_path):
    path = tmp_path / 'bad.json'
    path.write_text('{not json', encoding='utf-8')

    binding = load_binding(tool_python=tmp_path / 'python', model_config=path,
                           repro_root=None, env_file=None)

    assert binding['model_config_status'] == 'invalid'
    assert binding['api_key_env'] is None


def test_environment_wins_and_is_shared_with_runtime(tmp_path):
    env_file = _write_env(tmp_path / 'proj.env', 'DEEPSEEK_API_KEY=from-file\n')
    binding = _binding(tmp_path, env_file=str(env_file))

    source = discover_source(binding, environ={'DEEPSEEK_API_KEY': 'from-env'},
                             platform_name='nt')
    child = load_child_environment(binding, source, environ={'DEEPSEEK_API_KEY': 'from-env'},
                                   platform_name='nt')

    assert source['kind'] == 'environment'
    assert child['DEEPSEEK_API_KEY'] == 'from-env'
    assert 'from-env' not in json.dumps(source)


def test_empty_environment_value_falls_through_to_dotenv(tmp_path):
    env_file = _write_env(tmp_path / 'proj.env', 'DEEPSEEK_API_KEY=from-file\n')
    binding = _binding(tmp_path, env_file=str(env_file))

    source = discover_source(binding, environ={'DEEPSEEK_API_KEY': ''}, platform_name='nt')

    assert source['kind'] == 'dotenv'
    assert source['path'] == str(env_file)


def test_explicit_dotenv_wins_over_repro_root_dotenv(tmp_path):
    root = tmp_path / 'repro-root'
    root.mkdir()
    _write_env(root / '.env', 'DEEPSEEK_API_KEY=from-root\n')
    explicit = _write_env(tmp_path / 'proj.env', 'DEEPSEEK_API_KEY=from-project\n')
    binding = _binding(tmp_path, env_file=str(explicit))

    source = discover_source(binding, environ={}, platform_name='posix')

    assert source['kind'] == 'dotenv'
    assert source['path'] == str(explicit)


def test_launch_cwd_change_does_not_change_the_bound_source(tmp_path, monkeypatch):
    explicit = _write_env(tmp_path / 'proj.env', 'DEEPSEEK_API_KEY=bound-value\n')
    binding = _binding(tmp_path, env_file=str(explicit))
    other = tmp_path / 'other'
    other.mkdir()
    _write_env(other / '.env', 'DEEPSEEK_API_KEY=other-value\n')
    monkeypatch.chdir(other)

    source = discover_source(binding, environ={}, platform_name='posix')

    assert source['path'] == str(explicit)


@pytest.mark.parametrize('base_url, expected', [
    ('https://api.deepseek.com', 'dpapi-file'),
    ('https://api.deepseek.com/v1', 'dpapi-file'),
    ('http://api.deepseek.com', 'missing'),
    ('https://api.deepseek.com.evil', 'missing'),
    ('https://api.openai.com', 'missing'),
])
def test_dpapi_only_for_the_exact_deepseek_provider(tmp_path, monkeypatch, base_url, expected):
    root = tmp_path / 'repro-root'
    key = root / '.local' / 'deepseek.key'
    key.parent.mkdir(parents=True)
    key.write_text('00ff', encoding='utf-8')
    binding = _binding(tmp_path, model_overrides={'base_url': base_url},
                       repro_root=str(root))
    monkeypatch.setattr(credentials, 'decrypt_dpapi_file',
                        lambda path: pytest.fail('discovery must not decrypt'))

    source = discover_source(binding, environ={}, platform_name='nt')

    assert source['kind'] == expected


def test_dpapi_is_not_used_for_another_variable_or_platform(tmp_path):
    root = tmp_path / 'repro-root'
    key = root / '.local' / 'deepseek.key'
    key.parent.mkdir(parents=True)
    key.write_text('00ff', encoding='utf-8')
    binding = _binding(tmp_path, repro_root=str(root),
                       model_overrides={'api_key_env': 'OTHER_KEY'})

    assert discover_source(binding, environ={}, platform_name='nt')['kind'] == 'missing'
    assert discover_source(_binding(tmp_path, repro_root=str(root)),
                           environ={}, platform_name='posix')['kind'] == 'missing'


def test_high_priority_broken_source_does_not_fall_back(tmp_path):
    root = tmp_path / 'repro-root'
    root.mkdir()
    _write_env(root / '.env', 'DEEPSEEK_API_KEY=fallback\n')
    broken = _write_env(tmp_path / 'broken.env',
                        'DEEPSEEK_API_KEY=first\nDEEPSEEK_API_KEY=second\n')
    binding = _binding(tmp_path, repro_root=str(root), env_file=str(broken))

    source = discover_source(binding, environ={}, platform_name='posix')

    assert source['kind'] == 'invalid_credential_file'
    with pytest.raises(CredentialError) as error:
        load_child_environment(binding, source, environ={}, platform_name='posix')
    assert error.value.code == 'invalid_credential_file'


def test_dotenv_parses_exact_key_quotes_and_export_without_executing(tmp_path):
    env_file = _write_env(tmp_path / 'proj.env', (
        '# comment\n'
        'OTHER_KEY=other\n'
        'export DEEPSEEK_API_KEY = "fake $HOME ; $(touch pwned)"\n'
    ))
    binding = _binding(tmp_path, env_file=str(env_file))

    source = discover_source(binding, environ={}, platform_name='posix')
    child = load_child_environment(binding, source, environ={}, platform_name='posix')

    assert source['kind'] == 'dotenv'
    assert child['DEEPSEEK_API_KEY'] == 'fake $HOME ; $(touch pwned)'
    assert 'OTHER_KEY' not in child
    assert not (tmp_path / 'pwned').exists()


def test_single_quoted_dotenv_value_is_unquoted(tmp_path):
    env_file = _write_env(tmp_path / 'proj.env', "DEEPSEEK_API_KEY='fake value'\n")
    binding = _binding(tmp_path, env_file=str(env_file))

    source = discover_source(binding, environ={}, platform_name='posix')

    assert load_child_environment(binding, source, environ={},
                                  platform_name='posix')['DEEPSEEK_API_KEY'] == 'fake value'


@pytest.mark.parametrize('text, reason', [
    ('DEEPSEEK_API_KEY=one\nDEEPSEEK_API_KEY=two\n', 'duplicate_key'),
    ("DEEPSEEK_API_KEY='unclosed\n", 'unclosed_quote'),
    ('DEEPSEEK_API_KEY="unclosed\n', 'unclosed_quote'),
])
def test_invalid_dotenv_is_controlled_and_does_not_leak(tmp_path, text, reason):
    env_file = _write_env(tmp_path / 'proj.env', text)
    binding = _binding(tmp_path, env_file=str(env_file))

    source = discover_source(binding, environ={}, platform_name='posix')

    assert source['kind'] == 'invalid_credential_file'
    assert source['reason'] == reason
    assert 'one' not in json.dumps(source)
    assert 'two' not in json.dumps(source)
    with pytest.raises(CredentialError) as error:
        load_child_environment(binding, source, environ={}, platform_name='posix')
    assert 'one' not in str(error.value)
    assert 'two' not in str(error.value)


def test_load_child_environment_does_not_mutate_parent(tmp_path):
    binding = _binding(tmp_path)
    parent = {'DEEPSEEK_API_KEY': 'fake-in-env', 'PATH': 'keep-me'}

    child = load_child_environment(binding, {'kind': 'environment',
        'name': 'DEEPSEEK_API_KEY', 'path': None},
        environ=parent, platform_name='posix')

    assert child['DEEPSEEK_API_KEY'] == 'fake-in-env'
    assert child['PATH'] == 'keep-me'
    assert parent == {'DEEPSEEK_API_KEY': 'fake-in-env', 'PATH': 'keep-me'}
    assert child is not parent


def test_missing_selected_variable_is_a_load_failure(tmp_path):
    binding = _binding(tmp_path)

    with pytest.raises(CredentialError) as error:
        load_child_environment(binding, {'kind': 'environment',
            'name': 'DEEPSEEK_API_KEY', 'path': None}, environ={}, platform_name='posix')

    assert error.value.code == 'credential_load_failed'


@pytest.mark.skipif(os.name != 'nt', reason='DPAPI is Windows-only')
def test_dpapi_file_round_trip(tmp_path):
    value = 'fake-dpapi-canary-123'
    script = ("ConvertTo-SecureString -String '" + value +
              "' -AsPlainText -Force | ConvertFrom-SecureString")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
                            capture_output=True, text=True, check=True)
    path = tmp_path / 'deepseek.key'
    path.write_text(result.stdout.strip(), encoding='utf-8')

    assert credentials.decrypt_dpapi_file(path) == value


@pytest.mark.skipif(os.name != 'nt', reason='DPAPI is Windows-only')
def test_dpapi_unsupported_format_is_controlled(tmp_path):
    path = tmp_path / 'deepseek.key'
    path.write_text('this-is-not-hex', encoding='utf-8')

    with pytest.raises(CredentialError) as error:
        credentials.decrypt_dpapi_file(path)

    assert error.value.code == 'credential_load_failed'
    assert 'this-is-not-hex' not in str(error.value)
