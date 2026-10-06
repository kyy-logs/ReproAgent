import asyncio
import importlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from reproagent.core.models import EvidenceLevel, TaskResult, TaskState
from reproagent.core.serialization import bytes_hash
from tests.unit.test_verifier import prepare


def export_setup(tmp_path, projects, facts, secrets=()):
    if secrets:
        original_projects = projects
        class LoggingProjects:
            def plain(self, root):
                repo = original_projects.plain(root)
                path = repo / 'example/parser.py'
                path.write_text(path.read_text().replace('    return', "    print('private-token')\n    return"))
                return repo
        projects = LoggingProjects()
    contract, candidate, execution, verifier, _, runner, snapshot, request = prepare(tmp_path, projects, facts)
    runner.store.save_record('contracts', contract.contract_id, contract)
    result = TaskResult('task', TaskState.EXPORTING, evidence_level=EvidenceLevel.REPEATED_OBSERVATION, accepted_candidate_id=candidate.candidate_id)
    exporter = importlib.import_module('reproagent.exporter').Exporter(secrets)
    return exporter, result, runner.store, candidate, execution, snapshot, request


def test_exported_candidate_replays_from_declared_install_paths(tmp_path, projects, facts):
    exporter, result, store, candidate, execution, snapshot, request = export_setup(tmp_path, projects, facts)
    manifest = exporter.export(result, store)
    fresh = projects.plain(tmp_path / 'fresh')
    out = tmp_path / 'replay'
    replay = subprocess.run([sys.executable, str(manifest.root / 'replay.py'), '--repo', str(fresh), '--python', sys.executable, '--output', str(out), '--install'], capture_output=True, timeout=30)
    assert replay.returncode == 1, replay.stderr.decode()
    assert (fresh / 'tests/test_repro.py').read_bytes() == (candidate.storage_root / 'tests/test_repro.py').read_bytes()
    from reproagent.adapters.languages.python_pytest.collector import read_probe
    assert read_probe(out / 'probe.jsonl', 'export-replay').probe_complete
    assert str(store.root) not in (manifest.root / 'report.json').read_text()


def test_export_failure_never_publishes_success_package(tmp_path, projects, facts):
    exporter, result, store, candidate, *_ = export_setup(tmp_path, projects, facts)
    (candidate.storage_root / candidate.files[0].path).write_text('changed')
    with pytest.raises(ValueError): exporter.export(result, store)
    assert not (store.root / 'artifacts/reproduction').exists()


def test_manifest_does_not_hash_itself_or_final_task_event(tmp_path, projects, facts):
    exporter, result, store, *_ = export_setup(tmp_path, projects, facts)
    manifest = exporter.export(result, store)
    assert 'manifest.json' not in [f.path for f in manifest.files]
    assert manifest.event_cutoff == len(store.read_events()[0]) - 1
    store.append_event('export.completed', (), {'manifest_hash':manifest.manifest_hash})
    assert bytes_hash((manifest.root / 'manifest.json').read_bytes())
    assert all(bytes_hash((manifest.root / entry.path).read_bytes()) == entry.content_hash for entry in manifest.files)


def test_redacted_logs_keep_explicit_source_to_export_reference_mapping(tmp_path, projects, facts):
    exporter, result, store, candidate, execution, *_ = export_setup(tmp_path, projects, facts, ('private-token',))
    source = store.root / execution.raw.stdout_ref.path
    manifest = exporter.export(result, store)
    report = json.loads((manifest.root / 'report.json').read_text())
    mapping = next(m for m in report['log_mapping'] if m['source_path'] == execution.raw.stdout_ref.path)
    assert mapping['source_hash'] != mapping['export_hash']
    assert 'private-token' not in (manifest.root / mapping['export_path']).read_text()
    assert mapping['source_hash'] == bytes_hash(source.read_bytes())


def test_exported_markdown_is_hashed_redacted_and_links_to_existing_package_files(tmp_path, projects, facts):
    import re
    from urllib.parse import unquote

    exporter, result, store, *_ = export_setup(tmp_path, projects, facts, ('private-token',))
    manifest = exporter.export(result, store)
    text = (manifest.root / 'report.md').read_text(encoding='utf-8')
    assert 'private-token' not in text and str(store.root) not in text
    assert '[REDACTED]' in text
    readable = text.replace('\\[', '[').replace('\\]', ']')
    assert 'IndexError' in readable and 'parse([]) returns []' in readable
    assert 'report.md' in [entry.path for entry in manifest.files]
    for target in re.findall(r'\]\(([^)]+)\)', text):
        path = (manifest.root / unquote(target)).resolve()
        assert path.is_relative_to(manifest.root.resolve()) and path.is_file(), target
    assert all(bytes_hash((manifest.root / entry.path).read_bytes()) == entry.content_hash for entry in manifest.files)


def test_diagnostic_markdown_keeps_stop_reason_and_does_not_link_missing_replay(tmp_path, projects, facts):
    exporter, result, store, *_ = export_setup(tmp_path, projects, facts)
    result = replace(result, status=TaskState.EXHAUSTED, stop_reason='time limit', uncertainties=('need more evidence',))
    manifest = exporter.export(result, store)
    text = (manifest.root / 'report.md').read_text(encoding='utf-8')
    assert '未确认复现' in text and 'time limit' in text and 'need more evidence' in text
    assert '(./replay.py)' not in text


@pytest.mark.parametrize('diagnostic', [False, True])
def test_original_issue_survives_contract_using_only_repository_sources(tmp_path, projects, facts, diagnostic):
    exporter, result, store, *_ = export_setup(tmp_path, projects, facts)
    (store.root / 'input').mkdir()
    (store.root / 'input/issue.md').write_text('Original user expectation: return an empty list.', encoding='utf-8')
    if diagnostic:
        result = replace(result, status=TaskState.EXHAUSTED)
    manifest = exporter.export(result, store)
    text = (manifest.root / 'report.md').read_text(encoding='utf-8')
    assert 'Original user expectation' in text
    report = json.loads((manifest.root / 'report.json').read_text(encoding='utf-8'))
    original = next(item for item in report['source_mapping'] if item['source_path'] == 'input/issue.md')
    assert (manifest.root / original['export_path']).read_text(encoding='utf-8').startswith('Original user expectation')


def test_secret_in_candidate_install_path_is_rejected_without_publishing(tmp_path, projects, facts):
    # The generated default file is tests/test_repro.py; changing its install
    # metadata by replacing "test_repro" would make the independent replay fail.
    exporter, result, store, *_ = export_setup(tmp_path, projects, facts)
    exporter.secrets = ('test_repro',)
    with pytest.raises(ValueError, match='installation metadata'):
        exporter.export(result, store)
    assert not (store.root / 'artifacts/reproduction').exists()


@pytest.mark.parametrize('secret', ['top\\secret"value', '\\token', '"token'])
def test_json_escaped_secret_is_redacted_before_markdown_formatting(tmp_path, projects, facts, monkeypatch, secret):
    exporter, result, store, *_ = export_setup(tmp_path, projects, facts)
    exporter.secrets = (secret,)
    contract = store.load_record('contracts', 'contract-1')
    monkeypatch.setattr(store, 'load_contract', lambda *args: replace(contract, expected='Expected: ' + secret))
    manifest = exporter.export(result, store)
    report = json.loads((manifest.root / 'report.json').read_text(encoding='utf-8'))
    text = (manifest.root / 'report.md').read_text(encoding='utf-8')
    assert secret not in report['contract']['expected']
    assert 'top' not in text and '[REDACTED]' in report['contract']['expected']
