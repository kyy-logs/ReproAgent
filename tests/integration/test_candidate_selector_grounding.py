"""Selector prerequisites are part of the oracle, not just the final assertion.

The provider is scripted: these tests prove delivery, rejection feedback and real
test discrimination. They do not prove a real model will recognize every selector.
"""
import asyncio
import json
import sys

import httpx
import pytest

from reproagent.app import create_controller
from reproagent.core.models import FixValidationRequest, ModelConfig, PythonPytestConfig, TaskRequest
from tests.assertion_support import approved_assertions
from tests.integration.test_agentscope_runtime import tool_reply


OLD_DIAGNOSTIC = 'Operation refused: use legacy mode'
NEW_DIAGNOSTIC = 'Retry with recovery_option=True'
BAD = '''from example.parser import parse
OLD_HEAD = "Operation refused: use legacy mode"
def selected(text):
    return [line for line in text.splitlines() if OLD_HEAD in line]
def test_guidance():
    lines = selected(parse([]))
    assert lines
    assert "recovery_option" in " ".join(lines)
'''
GOOD = '''from example.parser import parse
def test_guidance():
    diagnostic = parse([])
    assert "recovery_option" in diagnostic
'''


def test_wording_selector_rejection_reaches_next_exploration_and_corrected_candidate_passes_fixed(
        tmp_path, projects, facts):
    repo = projects.plain(tmp_path / 'repo')
    source = ('def parse(values):\n'
              '    if values: return [values[0]]\n'
              f'    return {OLD_DIAGNOSTIC!r}\n')
    (repo / 'example/parser.py').write_text(source, encoding='utf-8')
    (repo / 'issue.md').write_text(
        'For empty input, parse returns a diagnostic. It must mention recovery_option. '
        f'The reported old diagnostic is {OLD_DIAGNOSTIC!r}; its wording need not be preserved.\n',
        encoding='utf-8')
    fixed = projects.fixed(tmp_path / 'fixed-hidden')
    (fixed / 'example/parser.py').write_text(
        source.replace(repr(OLD_DIAGNOSTIC), repr(NEW_DIAGNOSTIC)), encoding='utf-8')
    request = TaskRequest(repo, tmp_path / 'out', repo / 'issue.md',
        language=PythonPytestConfig(python=sys.executable, target_modules=('example.parser',)))
    context = facts.context(limits=request.limits)
    exploration, reviews = [], []

    async def handle(http_request):
        payload = json.loads(http_request.content)
        # The independent validation source/diagnostic must never teach the model.
        assert str(fixed) not in json.dumps(payload)
        assert NEW_DIAGNOSTIC not in json.dumps(payload)
        if payload.get('tools'):
            exploration.append(payload)
            if len(exploration) == 2:
                history = json.dumps(payload['messages'])
                assert 'CANDIDATE_ASSERTIONS_UNCONFIRMED' in history
                assert '旧文案筛选' in json.dumps(payload['messages'], ensure_ascii=False)
            return tool_reply('write_candidate', {'files': [{'path': 'tests/test_repro.py',
                'role': 'test', 'content': BAD if len(exploration) == 1 else GOOD}],
                'hypothesis': '检查目标诊断中的恢复选项'},
                call_id=f'publish-{len(exploration)}')
        data = json.loads(payload['messages'][-1]['content'])
        if 'available_refs' not in data:
            answer = {'trigger': 'empty input', 'expected': 'diagnostic mentions recovery_option',
                'reported_actual': OLD_DIAGNOSTIC, 'source_indices': [0],
                'missing_information': [], 'observable_checks': [], 'assumptions': []}
        else:
            reviews.append(payload)
            candidate_text = data['candidate'][0]['content']
            answer = {'classification': 'REPRODUCED', 'reason': '目标诊断缺少恢复选项',
                'evidence_refs': data['available_refs'], 'expected_assertion': True,
                'target_triggered': True, 'failure_matches_issue': True,
                'assertion_reviews': approved_assertions(data)}
            if candidate_text == BAD:
                assert len(data['assertion_sites']) == 2
                assert 'def selected' in candidate_text and 'OLD_HEAD' in candidate_text
                answer['assertion_reviews'][0].update(supported=False, evidence_refs=[],
                    reason='旧文案筛选是无依据的前置条件，即使正确诊断也会被排除')
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': json.dumps(answer, ensure_ascii=False)}}]})

    controller = create_controller(request, ModelConfig(base_url='https://offline.example/v1',
        model='offline', output_limit_field='max_tokens'), transport=httpx.MockTransport(handle))
    result = asyncio.run(controller.run(request, context, FixValidationRequest(fixed, sys.executable)))
    assert result.status.value == 'DONE', result
    assert result.evidence_level.value == 'DIFFERENTIAL_VALIDATED'
    assert result.fix_validation_status == 'passed'
    assert len(exploration) == 2 and len(reviews) == 3
    verdicts = [json.loads(path.read_text(encoding='utf-8'))
                for path in (request.output_dir / 'verdicts').glob('*.json')]
    assert sorted(row['classification'] for row in verdicts) == [
        'INVALID_CANDIDATE', 'REPRODUCED', 'REPRODUCED']
    for payload in [*exploration, *reviews]:
        system = '\n'.join(message['content'] for message in payload['messages']
                           if message['role'] == 'system' and isinstance(message['content'], str))
        assert 'acquisition and selection' in system
        assert 'correct diagnostic' in system
        assert 'explicitly required' in system


@pytest.mark.parametrize('diagnostic,old_passes,new_passes', [
    (OLD_DIAGNOSTIC, False, False),
    (NEW_DIAGNOSTIC, False, True),
    ('No recovery information', False, False),
])
def test_selector_counterexample_without_hidden_fix_or_transcript_search(diagnostic, old_passes, new_passes):
    """Controlled diagnostics show exactly what the old prerequisite excluded."""
    import ast
    for candidate, expected in [(BAD, old_passes), (GOOD, new_passes)]:
        # Substitute only the import in this tiny test fixture, never product code.
        tree = ast.parse(candidate)
        tree.body.pop(0)
        namespace = {'parse': lambda values: diagnostic}
        exec(compile(tree, '<candidate-selector-counterexample>', 'exec'), namespace)
        try:
            namespace['test_guidance']()
        except AssertionError:
            passed = False
        else:
            passed = True
        assert passed == expected
