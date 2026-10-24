from importlib.resources import files

import pytest

from reproagent.core import agent
from reproagent.core.models import AgentContext, IssueContract, ProjectView


RESOURCE = 'resources/skills/reproduce-bug/SKILL.md'


@pytest.mark.parametrize('experience_enabled', [False, True])
def test_skill_is_not_in_the_persistent_system_prompt(experience_enabled):
    context = AgentContext(IssueContract('contract-1'), ProjectView(None))
    text = agent.exploration_prompt(context, candidate_parent='checks', experience_enabled=experience_enabled)
    source = files('reproagent').joinpath(RESOURCE).read_text(encoding='utf-8')
    body = source.partition('\n---\n')[2].strip()
    assert body and body == agent.reproduction_skill()
    assert body not in text and 'name: reproduce-bug' not in text
    assert 'checks/test_repro.py' in text
    assert ('list_experiences' in text) == experience_enabled


@pytest.mark.parametrize('source', ['body without metadata', '---\nname: reproduce-bug\n---\n'])
def test_broken_bundled_skill_does_not_silently_disable_guidance(tmp_path, monkeypatch, source):
    path = tmp_path / RESOURCE
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding='utf-8')
    monkeypatch.setattr(agent, 'files', lambda package: tmp_path)
    with pytest.raises(ValueError, match='reproduction skill'):
        agent.reproduction_skill()


def test_skill_changes_invalidate_evaluation_source_receipt(tmp_path):
    from evals.swt_bench.provenance import capture_tool_source, assert_tool_source, ToolSourceChanged
    path = tmp_path / 'src/reproagent' / RESOURCE
    path.parent.mkdir(parents=True)
    path.write_text('skill version 1', encoding='utf-8')
    receipt = capture_tool_source(tmp_path)
    assert 'src/reproagent/' + RESOURCE in receipt['files']
    path.write_text('skill version 2', encoding='utf-8')
    with pytest.raises(ToolSourceChanged):
        assert_tool_source(tmp_path, receipt)


def test_decision_card_is_bundled_and_non_empty():
    body = agent.reproduction_skill()
    card = agent.reproduction_skill_checklist()
    assert card
    assert card in body
    assert 'pathlib.Path' in card
    assert 'makedirs' in card
    assert 'pytest' in card or 'plugin' in card


def test_missing_decision_card_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / RESOURCE
    path.parent.mkdir(parents=True)
    path.write_text('---\nname: reproduce-bug\n---\n# body without a card\n', encoding='utf-8')
    monkeypatch.setattr(agent, 'files', lambda package: tmp_path)
    with pytest.raises(ValueError, match='decision card'):
        agent.reproduction_skill_checklist()


def test_empty_decision_card_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / RESOURCE
    path.parent.mkdir(parents=True)
    path.write_text(
        '---\nname: reproduce-bug\n---\n<!-- decision-card -->\n<!-- /decision-card -->\n',
        encoding='utf-8')
    monkeypatch.setattr(agent, 'files', lambda package: tmp_path)
    with pytest.raises(ValueError, match='empty decision card'):
        agent.reproduction_skill_checklist()
