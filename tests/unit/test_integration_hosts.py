import os
from pathlib import Path

import pytest

from reproagent.integration.hosts import allowed_roots, resolve_hosts


def _by_host(items):
    return {item['host']: item for item in items}


def test_default_resolves_two_hosts_one_codex_root(tmp_path):
    home = tmp_path / 'isolated-home'

    roots = resolve_hosts('all', home=home, environ={}, codex_layout='standard')

    assert {item['host'] for item in roots} == {'claude-code', 'codex'}
    assert len(roots) == 2
    assert [item['root'] for item in roots if item['host'] == 'codex'] == [
        home / '.agents' / 'skills' / 'reproagent']
    assert not home.exists()


def test_legacy_selects_only_legacy_root(tmp_path):
    home = tmp_path / 'home'
    codex_home = tmp_path / 'custom-codex'

    roots = resolve_hosts('all', home=home, environ={'CODEX_HOME': str(codex_home)},
                          codex_layout='legacy')

    codex = _by_host(roots)['codex']
    assert codex['root'] == codex_home / 'skills' / 'reproagent'
    assert codex['layout'] == 'legacy'
    assert len([item for item in roots if item['host'] == 'codex']) == 1
    assert home / '.agents' not in [item['root'] for item in roots]


def test_claude_config_dir_is_honoured(tmp_path):
    home = tmp_path / 'home'
    claude_home = tmp_path / 'claude-home'

    roots = _by_host(resolve_hosts('claude-code', home=home,
                                   environ={'CLAUDE_CONFIG_DIR': str(claude_home)}))

    assert roots['claude-code']['root'] == claude_home / 'skills' / 'reproagent'


@pytest.mark.parametrize('value', ['', 'relative/path'])
def test_invalid_host_directory_variable_is_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        resolve_hosts('claude-code', home=tmp_path, environ={'CLAUDE_CONFIG_DIR': value})


def test_unknown_host_or_layout_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        resolve_hosts('other', home=tmp_path, environ={})
    with pytest.raises(ValueError):
        resolve_hosts('codex', home=tmp_path, environ={}, codex_layout='other')


def test_detection_uses_existing_config_directory_without_creating_it(tmp_path):
    home = tmp_path / 'home'
    (home / '.claude').mkdir(parents=True)

    claude = _by_host(resolve_hosts('claude-code', home=home, environ={}))['claude-code']

    assert claude['host_detected'] is True
    assert 'config_dir' in claude['detection_sources']
    assert not (home / '.claude' / 'skills').exists()


def test_executable_on_path_is_a_detection_source(tmp_path):
    home = tmp_path / 'home'
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    executable = bin_dir / ('codex.exe' if os.name == 'nt' else 'codex')
    executable.write_text('fake', encoding='utf-8')
    if os.name != 'nt':
        executable.chmod(0o755)

    codex = _by_host(resolve_hosts('codex', home=home,
                                   environ={'PATH': str(bin_dir)}))['codex']

    assert codex['host_detected'] is True
    assert 'executable' in codex['detection_sources']
    assert not home.exists()


def test_detection_does_not_treat_a_new_install_dir_as_a_host(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()

    for item in resolve_hosts('all', home=home, environ={}):
        assert item['host_detected'] is False
        assert item['detection_sources'] == []


def test_allowed_roots_lists_standard_and_legacy_codex(tmp_path):
    home = tmp_path / 'home'
    codex_home = tmp_path / 'codex-home'

    roots = allowed_roots(home=home, environ={'CODEX_HOME': str(codex_home)})

    assert roots['claude-code'] == (home / '.claude' / 'skills' / 'reproagent',)
    assert roots['codex'] == (home / '.agents' / 'skills' / 'reproagent',
                              codex_home / 'skills' / 'reproagent')


def test_allowed_roots_are_unique_and_absolute(tmp_path):
    home = tmp_path / 'home'

    roots = allowed_roots(home=home, environ={})

    for paths in roots.values():
        assert len(paths) == len(set(paths))
        assert all(path.is_absolute() for path in paths)
