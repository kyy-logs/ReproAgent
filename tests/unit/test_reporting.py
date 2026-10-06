from copy import deepcopy

from reproagent.reporting import render_report


def report_data(level='REPEATED_OBSERVATION'):
    return {
        'package_kind': 'reproduction', 'verified': True, 'task_id': 'task-1',
        'status': 'EXPORTING', 'evidence_level': level, 'event_cutoff': 12,
        'candidate_id': 'candidate-1', 'candidate_files': [
            {'path': 'tests/中文 case (#1).py', 'role': 'test'},
            {'path': 'tests/data.json', 'role': 'data'},
        ],
        'contract': {'trigger': 'mixed separators', 'expected': 'raises Error',
                     'reported_actual': 'returns True', 'assumptions': [], 'missing_information': []},
        'source_mapping': [{'source_path': 'input/issue.md', 'export_path': 'sources/source-0000.txt', 'start_line': 1, 'end_line': 1}],
        'verdict_mapping': [{'export_path': 'verdicts/verdict-0000.json'}],
        'accepted_run_ids': ['original-1', 'original-2'],
        'runs': [
            {'run_id': 'old-attempt', 'candidate_id': 'old', 'role': 'original', 'exit_code': 1, 'cleanup_ok': True},
            {'run_id': 'original-1', 'candidate_id': 'candidate-1', 'role': 'original', 'exit_code': 1, 'cleanup_ok': True},
            {'run_id': 'original-2', 'candidate_id': 'candidate-1', 'role': 'original', 'exit_code': 1, 'cleanup_ok': True},
        ],
        'log_mapping': [{'source_path': 'runs/original-1/stdout.log', 'export_path': 'evidence/log-0000.txt'}],
        'python': 'target-python', 'pytest_version': '9.1', 'source_roots': ['src'],
        'target_modules': ['example'], 'limitations': [], 'uncertainties': [], 'stop_reason': '',
    }


def test_readable_report_preserves_original_source_and_links_without_copying_tests():
    data = report_data()
    before = deepcopy(data)
    text = render_report(data, {'sources/source-0000.txt': 'returns an Error object, not raises',
                                'evidence/log-0000.txt': 'AssertionError: got True',
                                'verdicts/verdict-0000.json': 'Expected wording conflicts with input.'})
    assert 'raises Error' in text and 'returns an Error object, not raises' in text
    assert 'AssertionError: got True' in text and 'wording conflicts' in text
    assert 'candidate/tests/%E4%B8%AD%E6%96%87%20case%20%28%231%29.py' in text
    assert '(./evidence/log-0000.txt)' in text
    assert '(./report.json)' in text and '(./manifest.json)' in text
    assert 'old-attempt' not in text
    assert 'EXPORTING' in text and '最终任务状态' in text
    assert '未获得修复版通过的差异验证' in text
    assert '--install' in text and '全新' in text
    assert data == before


def test_fixed_failure_and_uncertainties_are_visible_without_promoting_evidence():
    data = report_data()
    data['runs'].append({'run_id': 'fixed-1', 'candidate_id': 'candidate-1', 'role': 'fixed', 'exit_code': 1, 'cleanup_ok': True})
    data['uncertainties'] = ['fixed version did not pass']
    text = render_report(data)
    assert 'fixed-1' in text and 'fixed version did not pass' in text
    assert '未获得修复版通过的差异验证' in text


def test_differential_report_explains_proof_scope():
    data = report_data('DIFFERENTIAL_VALIDATED')
    data['runs'].append({'run_id': 'fixed-1', 'candidate_id': 'candidate-1', 'role': 'fixed', 'exit_code': 0, 'cleanup_ok': True})
    text = render_report(data)
    assert '修复版对照通过' in text and '根因' in text


def test_diagnostic_with_partial_evidence_never_offers_success_replay():
    data = report_data()
    data.update(package_kind='diagnostic', verified=False, status='EXHAUSTED', stop_reason='time limit', candidate_files=[])
    data.pop('contract')
    text = render_report(data)
    assert '未确认复现' in text and 'time limit' in text
    assert 'REPEATED_OBSERVATION' in text
    assert '--install' not in text and '(./replay.py)' not in text


def test_untrusted_issue_text_cannot_close_fence_or_create_markdown_image():
    data = report_data()
    data['contract']['expected'] = '![remote](https://invalid/image)\n<script>bad</script> | forged'
    source = '```\n# forged conclusion\n```\n![remote](https://invalid/image)'
    text = render_report(data, {'sources/source-0000.txt': source})
    assert '\\!\\[remote\\]' in text and '&lt;script&gt;' in text
    assert '````text\n' + source + '\n````' in text


def test_large_fields_and_previews_are_bounded_and_marked_as_excerpts():
    data = report_data()
    data['contract']['expected'] = 'x' * 100_000
    text = render_report(data, {'sources/source-0000.txt': 'y' * 100_000})
    assert len(text) < 15000
    assert '节选' in text


def test_large_collections_remain_bounded_and_reference_complete_records():
    data = report_data()
    data['contract']['assumptions'] = ['a' * 2000] * 10000
    text = render_report(data)
    assert len(text.encode('utf-8')) <= 65536
    assert '节选' in text and '(./report.json)' in text


def test_rendering_checks_shared_finalization_budget():
    class Deadline(Exception):
        pass

    def check():
        raise Deadline('deadline')

    import pytest
    with pytest.raises(Deadline):
        render_report(report_data(), check=check)


def test_report_uses_packaged_template_and_fixed_section_order():
    from importlib.resources import files
    from string import Template
    template = files('reproagent').joinpath('resources/report.md.template').read_text(encoding='utf-8')
    assert '$conclusion' in template and '$review' in template and '$evidence' in template
    text = render_report(report_data())
    headings = ['## 结论', '## 用户审查入口', '## 问题理解与原始来源',
                '## 执行结果', '## 环境与重跑', '## 限制与不确定项', '## 详细记录']
    assert [text.index(heading) for heading in headings] == sorted(text.index(heading) for heading in headings)
    assert '${' not in text
    custom = Template(template.replace('## 结论', '## 模板控制的结论标题'))
    assert '## 模板控制的结论标题' in render_report(report_data(), template=custom)


def test_original_source_link_survives_large_model_contract_fields():
    data = report_data()
    data['contract'].update(trigger='触' * 2000, expected='期' * 2000)
    text = render_report(data)
    assert '(./sources/source-0000.txt)' in text


def test_replay_command_and_tool_link_survive_large_environment_fields():
    data = report_data()
    data['target_modules'] = ['模' * 2000]
    text = render_report(data)
    assert '--install' in text and '(./replay.py)' in text
