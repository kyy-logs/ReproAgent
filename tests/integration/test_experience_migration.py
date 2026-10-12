import json
from pathlib import Path

import pytest

from reproagent import experience as exp
from tests.unit.test_experience_store import card, library


def test_new_default_and_old_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv(exp.EXPERIENCE_ENV, raising=False)
    monkeypatch.setattr(exp, 'DEFAULT_EXPERIENCE_PATH', tmp_path / 'experiences')
    monkeypatch.setattr(exp, 'LEGACY_EXPERIENCE_PATH', tmp_path / 'experiences.json', raising=False)
    assert exp.default_experience_path() == tmp_path / 'experiences'
    library(tmp_path / 'experiences.json', card())
    assert exp.default_experience_path() == tmp_path / 'experiences.json'
    (tmp_path / 'experiences').mkdir()
    assert exp.default_experience_path() == tmp_path / 'experiences'


def test_migration_preserves_source_and_ids(tmp_path):
    from reproagent.experience_storage import migrate_experience_library
    source, target = tmp_path / 'old.json', tmp_path / 'new'
    library(source, card())
    before = source.read_bytes()
    result = migrate_experience_library(source, target)
    assert result['code'] == 'migrated' and result['count'] == 1
    assert source.read_bytes() == before
    assert exp.load_experience_snapshot(target).cards == (card(),)
    assert all((target / category / 'experiences.json').exists() for category in exp.CATEGORIES)
    assert migrate_experience_library(source, target)['code'] == 'target_exists'


def test_failed_migration_does_not_publish_partial_target(tmp_path, monkeypatch):
    import reproagent.experience_storage as storage
    source, target = tmp_path / 'old.json', tmp_path / 'new'
    library(source, card())
    def fail(*args): raise OSError('write failure')
    monkeypatch.setattr(storage, 'atomic_write', fail)
    assert storage.migrate_experience_library(source, target)['code'] == 'store_error'
    assert not target.exists() and exp.load_experience_snapshot(source).cards == (card(),)


def test_read_only_legacy_load_never_upgrades(tmp_path):
    path = tmp_path / 'old.json'
    library(path, card())
    before = path.read_bytes()
    assert exp.load_experience_snapshot(path).cards
    assert path.read_bytes() == before
    assert exp.append_experience(path, card('second'), None) == 'written'
    assert json.loads(path.read_text())['schema_version'] == 2


def test_migration_cli_offline(tmp_path, capsys):
    from reproagent.cli import main
    source, target = tmp_path / 'old.json', tmp_path / 'new'
    library(source, card())
    assert main(['experience', 'migrate', str(source), str(target)]) == 0
    assert json.loads(capsys.readouterr().out)['code'] == 'migrated'


def test_empty_and_exact_legacy_card_migrate_without_expanding_records(tmp_path):
    from reproagent.experience_storage import migrate_experience_library
    from reproagent.core.serialization import canonical_bytes
    source, target = tmp_path / 'empty.json', tmp_path / 'empty-dir'
    library(source)
    assert migrate_experience_library(source, target)['count'] == 0
    large = exp._card_record(card(detail=''))
    large['detail'] = 'x' * (2048 - len(canonical_bytes(large)))
    source = tmp_path / 'large.json'
    source.write_bytes(canonical_bytes({'schema_version':1,'items':[large]}))
    target = tmp_path / 'large-dir'
    assert migrate_experience_library(source, target)['code'] == 'migrated'
    stored = json.loads((target / 'model/experiences.json').read_text())['items'][0]
    assert canonical_bytes(stored) == canonical_bytes(large)


def test_source_change_during_migration_cannot_publish(tmp_path, monkeypatch):
    import reproagent.experience_storage as storage
    source, target = tmp_path / 'old.json', tmp_path / 'new'
    library(source, card())
    original = storage.load_directory_snapshot
    def altered(root, **kwargs):
        result = original(root, **kwargs)
        library(source, card('external change'))
        return result
    monkeypatch.setattr(storage, 'load_directory_snapshot', altered)
    assert storage.migrate_experience_library(source, target)['code'] == 'source_changed'
    assert not target.exists()


def test_migration_preserves_controlled_trigger_metadata(tmp_path):
    from dataclasses import replace
    from reproagent.experience_storage import migrate_experience_library
    from reproagent.experience_triggers import ExperienceTrigger
    trigger = ExperienceTrigger('tool_error', 'write_candidate', 'GROUNDED_CONTRACT_REQUIRED')
    sample = replace(card(), triggers=(trigger,))
    sample = replace(sample, id=exp._identifier(sample.category, sample.tags, sample.summary, sample.triggers))
    source, target = tmp_path / 'old.json', tmp_path / 'new'
    library(source, sample)
    assert migrate_experience_library(source, target)['code'] == 'migrated'
    assert exp.load_experience_snapshot(target).cards == (sample,)
