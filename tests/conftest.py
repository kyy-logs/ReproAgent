import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class ProjectFactory:
    def plain(self, root):
        root.mkdir(parents=True, exist_ok=True)
        (root / "example").mkdir()
        (root / "example" / "__init__.py").write_text("", encoding="utf-8")
        (root / "example" / "parser.py").write_text("def parse(values):\n    return [values[0]]\n", encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests" / "test_existing.py").write_text("from example.parser import parse\ndef test_nonempty():\n    assert parse([1]) == [1]\n", encoding="utf-8")
        (root / "issue.md").write_text("Empty input must return an empty list; parse([]) raises IndexError.\n", encoding="utf-8")
        return root

    def src_layout(self, root):
        root = self.plain(root)
        (root / "src").mkdir()
        (root / "example").rename(root / "src" / "example")
        return root

    def fixed(self, root):
        root = self.plain(root)
        (root / "example" / "parser.py").write_text("def parse(values):\n    return [values[0]] if values else []\n", encoding="utf-8")
        return root


@pytest.fixture
def projects():
    return ProjectFactory()


@pytest.fixture
def facts():
    from reproagent.core.models import BudgetLimits, RunContext
    def context(**kwargs):
        try:
            from reproagent.core.budget import Budget
            budget = Budget(kwargs.pop("limits", BudgetLimits()))
        except ImportError:
            budget = SimpleNamespace(check=lambda: None, deadline=float("inf"))
        return RunContext(budget=budget, **kwargs)
    return SimpleNamespace(context=context)
