import json
from pathlib import Path

import pytest


def tool_root(tmp_path):
    """A checkout-shaped fixture, so a test never rewrites this worktree's own source."""
    root = tmp_path / 'tool'
    (root / 'src/reproagent/prompts').mkdir(parents=True)
    (root / 'src/reproagent/resources').mkdir(parents=True)
    (root / 'evals/swt_bench').mkdir(parents=True)
    (root / 'pyproject.toml').write_text('[project]\nname = "reproagent"\n', encoding='utf-8')
    (root / 'src/reproagent/app.py').write_text('VERSION = 1\n', encoding='utf-8')
    (root / 'src/reproagent/prompts/explore.md').write_text('explore the repository\n', encoding='utf-8')
    (root / 'src/reproagent/resources/report.md.template').write_text('report\n', encoding='utf-8')
    (root / 'evals/swt_bench/run.py').write_text('def run_batch(): pass\n', encoding='utf-8')
    return root


def test_capture_covers_code_prompts_resources_evals_and_pyproject(tmp_path):
    import reproagent
    from reproagent.core.serialization import canonical_hash
    from evals.swt_bench.provenance import capture_tool_source
    receipt = capture_tool_source(tool_root(tmp_path))
    assert set(receipt['files']) == {'pyproject.toml', 'src/reproagent/app.py', 'src/reproagent/prompts/explore.md',
        'src/reproagent/resources/report.md.template', 'evals/swt_bench/run.py'}
    assert receipt['source_hash'] == canonical_hash(receipt['files'])
    assert receipt['imported_from'] == Path(reproagent.__file__).resolve().as_posix()
    # A fixture outside any repository is recorded as uncommitted, not as a commit.
    assert receipt['git_commit'] == '' and receipt['git_dirty_paths'] == []


def test_changed_source_cannot_be_compared_with_the_recorded_round(tmp_path):
    from evals.swt_bench.provenance import ToolSourceChanged, assert_tool_source, capture_tool_source
    root = tool_root(tmp_path)
    receipt = capture_tool_source(root)
    assert_tool_source(root, receipt)
    (root / 'src/reproagent/prompts/explore.md').write_text('rewritten prompt\n', encoding='utf-8')
    with pytest.raises(ToolSourceChanged): assert_tool_source(root, receipt)
    (root / 'src/reproagent/prompts/explore.md').write_text('explore the repository\n', encoding='utf-8')
    assert_tool_source(root, receipt)
    (root / 'src/reproagent/prompts/added.md').write_text('new prompt\n', encoding='utf-8')
    with pytest.raises(ToolSourceChanged): assert_tool_source(root, receipt)


def test_missing_fingerprint_is_not_recorded_rather_than_verified(tmp_path):
    from evals.swt_bench.provenance import ToolSourceChanged, assert_tool_source
    root = tool_root(tmp_path)
    for receipt in ({}, {'source_hash': 'a' * 64}, None):
        with pytest.raises(ToolSourceChanged): assert_tool_source(root, receipt)


def test_secret_files_are_neither_read_nor_fingerprinted(tmp_path):
    from evals.swt_bench.provenance import assert_tool_source, capture_tool_source
    root = tool_root(tmp_path)
    receipt = capture_tool_source(root)
    (root / 'src/reproagent/prompts/.env').write_text('REPROAGENT_API_KEY=synthetic-secret\n', encoding='utf-8')
    (root / 'src/reproagent/resources/credentials.json').write_text('synthetic-secret\n', encoding='utf-8')
    (root / 'src/reproagent/api.key').write_text('synthetic-secret\n', encoding='utf-8')
    assert_tool_source(root, receipt)
    assert 'synthetic-secret' not in json.dumps(receipt)


def test_untracked_caches_do_not_invalidate_a_recorded_round(tmp_path):
    from evals.swt_bench.provenance import assert_tool_source, capture_tool_source
    root = tool_root(tmp_path)
    receipt = capture_tool_source(root)
    cache = root / 'src/reproagent/__pycache__'
    cache.mkdir()
    (cache / 'app.cpython-312.pyc').write_bytes(b'bytecode')
    assert_tool_source(root, receipt)
    assert not any('__pycache__' in name or name.endswith('.pyc') for name in receipt['files'])


def test_this_checkout_is_fingerprinted_including_prompts_and_evals():
    import evals.swt_bench.provenance as module
    from evals.swt_bench.provenance import capture_tool_source
    files = capture_tool_source(Path(module.__file__).resolve().parents[2])['files']
    assert 'src/reproagent/prompts/explore.md' in files
    assert 'src/reproagent/resources/report.md.template' in files
    assert 'evals/swt_bench/provenance.py' in files and 'pyproject.toml' in files
    assert not any(name.startswith(('.venv/', 'repro-results/', 'outputs/')) for name in files)


def test_round_summary_reports_not_recorded_for_older_rounds():
    from evals.swt_bench.results import summarize_round
    assert summarize_round({'outcomes': {}})['source_comparison_status'] == 'not_recorded'
    assert summarize_round({'outcomes': {}, 'source_comparison_status': 'changed'})['source_comparison_status'] == 'changed'
