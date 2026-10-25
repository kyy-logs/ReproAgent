import json
import os
import sys
from pathlib import Path

import pytest

from reproagent.integration.manifest import apply_plan
from reproagent.integration.setup import build_plan, check_setup


def _environ(home: Path, *, key='DEEPSEEK_API_KEY', value='fake-canary') -> dict:
    env = {'HOME': str(home), 'USERPROFILE': str(home),
           'PATH': os.environ.get('PATH', '')}
    if key:
        env[key] = value
    return env


def _options(tmp_path: Path, **overrides) -> dict:
    root = tmp_path / 'repro-root'
    root.mkdir(exist_ok=True)
    model = tmp_path / 'model.json'
    model.write_text(json.dumps({"base_url": "https://api.deepseek.com",
                                 "model": "fake-model",
                                 "api_key_env": "DEEPSEEK_API_KEY"}),
                     encoding='utf-8')
    options = {
        'host': 'all', 'codex_layout': 'standard', 'check': False,
        'dry_run': False, 'uninstall': False, 'force': False,
        'tool_python': sys.executable, 'model_config': str(model),
        'repro_root': str(root), 'env_file': None,
    }
    options.update(overrides)
    return options


def _apply(options, home, platform_name='posix'):
    plan = build_plan(options, environ=_environ(home), platform_name=platform_name)
    apply_plan(plan, manifest_path=plan['_manifest_path'],
               allowed_roots=plan['_allowed_roots'])
    return plan


def test_default_plan_installs_two_hosts_but_one_codex_root(tmp_path):
    home = tmp_path / 'home'

    plan = build_plan(_options(tmp_path), environ=_environ(home), platform_name='posix')

    assert {operation['host'] for operation in plan['operations']} == {'claude-code', 'codex'}
    codex_roots = {operation['root'] for operation in plan['operations']
                   if operation['host'] == 'codex'}
    assert codex_roots == {str(home / '.agents' / 'skills' / 'reproagent')}
    assert {operation['relative_path'] for operation in plan['operations']
            if operation['host'] == 'codex'} == {
        'SKILL.md', 'agents/openai.yaml', 'scripts/reproagent.sh'}
    assert not home.exists()


def test_check_uses_installed_binding_without_repeating_install_options(tmp_path):
    home = tmp_path / 'home'
    options = _options(tmp_path)
    _apply(options, home)
    snapshot = {path: path.read_bytes() for path in home.rglob('*') if path.is_file()}
    checked = check_setup({**options, 'model_config': None, 'repro_root': None,
                           'env_file': None, 'tool_python': None},
                          environ=_environ(home), platform_name='posix')
    assert checked['ready'] is True
    for host in ('claude-code', 'codex'):
        assert checked['hosts'][host]['entry_status'] == 'installed'
        assert checked['hosts'][host]['model_config_status'] == 'ready'
    assert all(path.read_bytes() == data for path, data in snapshot.items())


def test_check_separates_template_update_from_user_modification(tmp_path, monkeypatch):
    import reproagent.integration.setup as setup_module
    home = tmp_path / 'home'
    options = _options(tmp_path)
    _apply(options, home)
    render = setup_module.render_files

    def updated(*args, **kwargs):
        files = render(*args, **kwargs)
        files['SKILL.md'] += b'\nnew template\n'
        return files

    monkeypatch.setattr(setup_module, 'render_files', updated)
    checked = check_setup(options, environ=_environ(home), platform_name='posix')
    assert checked['hosts']['codex']['entry_status'] == 'installed'
    skill = next(item for item in checked['hosts']['codex']['files'] if item['path'] == 'SKILL.md')
    assert skill['template_current'] is False


def test_check_reports_missing_installed_model_without_calling_files_modified(tmp_path):
    home = tmp_path / 'home'
    options = _options(tmp_path)
    _apply(options, home)
    Path(options['model_config']).unlink()
    checked = check_setup({**options, 'model_config': None, 'repro_root': None},
                          environ=_environ(home), platform_name='posix')
    assert checked['ready'] is False
    assert checked['hosts']['codex']['entry_status'] == 'installed'
    assert checked['hosts']['codex']['model_config_status'] == 'missing'


def test_installed_hosts_keep_independent_configuration_bindings(tmp_path):
    home = tmp_path / 'home'
    first = _options(tmp_path, host='claude-code')
    _apply(first, home)
    second_model = tmp_path / 'second-model.json'
    second_model.write_text(json.dumps({'base_url': 'https://example.invalid',
                                       'model': 'second', 'api_key_env': 'SECOND_KEY'}))
    _apply({**first, 'host': 'codex', 'model_config': str(second_model)}, home)
    checked = check_setup({**first, 'host': 'all', 'model_config': None,
                           'tool_python': None, 'repro_root': None},
                          environ=_environ(home), platform_name='posix')
    assert checked['hosts']['claude-code']['ready'] is True
    assert checked['hosts']['codex']['ready'] is False
    assert checked['hosts']['codex']['credential_source']['name'] == 'SECOND_KEY'
    assert checked['credential_source']['kind'] == 'mixed'


def test_manifest_records_only_nonsecret_binding_paths(tmp_path):
    home = tmp_path / 'home'
    options = _options(tmp_path)
    _apply(options, home)
    text = (home / '.reproagent/integrations.json').read_text()
    for root in json.loads(text)['roots']:
        assert set(root['binding']) == {'tool_python', 'model_config', 'repro_root', 'env_file'}
        assert root['binding']['model_config'] == options['model_config']
    assert 'fake-canary' not in text
    assert 'base_url' not in text


def test_legacy_selection_writes_only_the_legacy_codex_root(tmp_path):
    home = tmp_path / 'home'
    options = _options(tmp_path, host='codex', codex_layout='legacy')

    plan = build_plan(options, environ=_environ(home), platform_name='posix')

    assert {operation['root'] for operation in plan['operations']} == {
        str(home / '.codex' / 'skills' / 'reproagent')}
    assert all(operation['layout'] == 'legacy' for operation in plan['operations'])


def test_legacy_layout_is_rejected_for_claude_only(tmp_path):
    home = tmp_path / 'home'
    options = _options(tmp_path, host='claude-code', codex_layout='legacy')

    with pytest.raises(ValueError):
        build_plan(options, environ=_environ(home), platform_name='posix')


@pytest.mark.parametrize('changes', [
    {'check': True, 'uninstall': True},
    {'check': True, 'dry_run': True},
    {'force': True, 'uninstall': True},
    {'force': True, 'check': True},
    {'uninstall': True, 'codex_layout': 'legacy'},
])
def test_invalid_option_combinations_are_rejected(tmp_path, changes):
    home = tmp_path / 'home'
    options = _options(tmp_path, **changes)

    with pytest.raises(ValueError):
        build_plan(options, environ=_environ(home), platform_name='posix')


def test_check_reports_each_readiness_dimension_then_becomes_ready(tmp_path):
    home = tmp_path / 'home'
    options = _options(tmp_path)
    environment = _environ(home)

    before = check_setup(options, environ=environment, platform_name='posix')
    assert before['hosts']['claude-code']['entry_status'] == 'missing'
    assert before['hosts']['claude-code']['launcher_importable'] is True
    assert before['hosts']['claude-code']['model_config_status'] == 'ready'
    assert before['hosts']['claude-code']['run_configured'] is True
    assert before['hosts']['claude-code']['ready'] is False
    assert before['ready'] is False
    assert not home.exists()

    _apply(options, home)
    after = check_setup(options, environ=environment, platform_name='posix')
    assert after['hosts']['claude-code']['entry_status'] == 'installed'
    assert after['hosts']['codex']['entry_status'] == 'installed'
    assert after['hosts']['claude-code']['ready'] is True
    assert after['hosts']['codex']['ready'] is True
    assert after['ready'] is True


def test_check_reports_modified_and_conflict_entries(tmp_path):
    home = tmp_path / 'home'
    _apply(_options(tmp_path, host='claude-code'), home, platform_name='posix')
    (home / '.claude' / 'skills' / 'reproagent' / 'SKILL.md').write_bytes(b'user-edit')
    codex_root = home / '.agents' / 'skills' / 'reproagent'
    codex_root.mkdir(parents=True)
    (codex_root / 'SKILL.md').write_bytes(b'foreign')
    options = _options(tmp_path)

    checked = check_setup(options, environ=_environ(home), platform_name='posix')

    assert checked['hosts']['claude-code']['entry_status'] == 'modified'
    assert checked['hosts']['claude-code']['ready'] is False
    assert checked['hosts']['codex']['entry_status'] == 'conflict'
    assert checked['hosts']['codex']['ready'] is False


def test_missing_model_config_is_reported_not_guessed(tmp_path):
    home = tmp_path / 'home'
    empty_root = tmp_path / 'empty-root'
    empty_root.mkdir()
    options = _options(tmp_path, model_config=None, repro_root=str(empty_root))

    plan = build_plan(options, environ=_environ(home, key=None), platform_name='posix')
    checked = check_setup(options, environ=_environ(home, key=None), platform_name='posix')

    assert 'model_config_missing' in plan['warnings']
    assert checked['hosts']['codex']['model_config_status'] == 'missing'
    assert checked['hosts']['codex']['run_configured'] is False
    assert checked['ready'] is False


def test_posix_check_requires_an_executable_script(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    options = _options(tmp_path)
    _apply(options, home, platform_name='posix')
    wrapper = home / '.claude' / 'skills' / 'reproagent' / 'scripts' / 'reproagent.sh'
    import reproagent.integration.setup as setup_module
    original = setup_module.os.access

    def access(path, mode):
        if Path(path) == wrapper and mode == os.X_OK:
            return False
        return original(path, mode)

    monkeypatch.setattr(setup_module.os, 'access', access)
    checked = check_setup(options, environ=_environ(home), platform_name='posix')

    assert checked['hosts']['claude-code']['permission_ready'] is False
    assert checked['hosts']['claude-code']['ready'] is False


def test_check_output_does_not_include_the_credential_value(tmp_path):
    home = tmp_path / 'home'
    options = _options(tmp_path)

    checked = check_setup(options, environ=_environ(home, value='fake-canary-987'),
                          platform_name='posix')

    assert 'fake-canary-987' not in json.dumps(checked)
    assert checked['hosts']['codex']['credential_source']['kind'] == 'environment'


def test_dry_run_plan_contains_no_file_bytes(tmp_path):
    home = tmp_path / 'home'

    plan = build_plan(_options(tmp_path), environ=_environ(home), platform_name='posix')

    assert all(isinstance(operation.get('content'), (bytes, bytearray))
               for operation in plan['operations'])
    assert not home.exists()


def test_uninstall_plan_reads_both_registered_codex_layouts(tmp_path):
    home = tmp_path / 'home'
    _apply(_options(tmp_path, host='codex', codex_layout='standard'), home)
    _apply(_options(tmp_path, host='codex', codex_layout='legacy'), home)

    plan = build_plan(_options(tmp_path, host='codex', uninstall=True),
                      environ=_environ(home), platform_name='posix')

    roots = {operation['root'] for operation in plan['operations']}
    assert roots == {str(home / '.agents' / 'skills' / 'reproagent'),
                     str(home / '.codex' / 'skills' / 'reproagent')}
    assert all(operation['op'] == 'remove' for operation in plan['operations'])
