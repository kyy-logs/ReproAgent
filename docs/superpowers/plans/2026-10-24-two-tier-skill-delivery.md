# Two-Tier Reproduction Skill Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the full reproduction Skill on the first exploration request, but make a compact decision card visible on every later exploration request, so fixture/path guidance is present when the model writes a candidate.

**Architecture:** `SKILL.md` gains a marked `decision-card` section. `reproduction_skill_checklist()` extracts it from the same packaged resource. `ExplorationMiddleware.on_model_call` injects the full Skill once and the checklist on later calls, as per-request `SystemMsg` values that are not written into conversation memory.

**Tech Stack:** Python 3.12, AgentScope 2.0.9, pytest, setuptools package data.

**Spec:** Evidence and failure analysis:
`C:/Users/Dell/.codex/worktrees/task-state-context/verification-2026-10-23/skill-ab/REPEATED-AB-RESULTS.md`
and `.../PREFLIGHT2-RERUN.md`.

## Global Constraints

- Work only in `C:/Users/Dell/.codex/worktrees/skill-decision-card`; do not modify `E:/ReproAgent` or prepared environments.
- Do not commit, push, merge, or open a PR. Leave all changes in the branch worktree for review.
- Do not read, print, or copy credentials. The existing runner may read `.local/deepseek.key` internally.
- No real model calls until Task 5; Tasks 1–4 are offline only.
- Keep failed samples and first runs; never replace a failure or count an unexecuted run as success.
- Use the shared interpreter `E:/ReproAgent/.venv/Scripts/python.exe` with `PYTHONPATH=<worktree>/src`.
- The decision card must remain short (target ≤ 250 tokens); do not move the full method text into every request.

---

### Task 1: Add the decision card and loader

**Files:**
- Modify: `src/reproagent/resources/skills/reproduce-bug/SKILL.md`
- Modify: `src/reproagent/core/agent.py:48-55`
- Test: `tests/unit/test_reproduction_skill.py`

**Interfaces:**
- Consumes: packaged `resources/skills/reproduce-bug/SKILL.md`
- Produces:
  - `reproduction_skill_checklist() -> str` in `reproagent.core.agent`
  - Markers `<!-- decision-card -->` and `<!-- /decision-card -->`
  - Raises `ValueError('bundled reproduction skill has no decision card')` or
    `ValueError('bundled reproduction skill has an empty decision card')`

- [ ] **Step 1: Add the decision-card section to SKILL.md**

Insert immediately after the introductory paragraph (after the line
“This skill guides exploration. The task's registered tools, source protections,
budgets and completion checks remain authoritative. It grants no additional
capabilities.”), before `## Establish the Testable Difference`:

```markdown
<!-- decision-card -->
Before `write_candidate`:

- Preserve the issue's trigger: input shape, configuration values, initialization and cache conditions.
- Verify the fixture/helper you call: required plugin registration, test root, constructor signature, and accepted path type.
- `pathlib.Path` / `WindowsPath` cannot feed a helper that calls `.makedirs()`; use the accepted type. For legacy `py.path` helpers use `path(str(tmp_path))`; for string APIs use `str(path)`.
- Build the smallest setup that produces the target output; drop unrelated root, theme and plugin dependencies.
- If execution stops before the target, fix the earliest setup, import or initialization blocker before changing assertions.
<!-- /decision-card -->
```

- [ ] **Step 2: Write the failing unit tests**

Append to `tests/unit/test_reproduction_skill.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run:

```bash
cd C:/Users/Dell/.codex/worktrees/skill-decision-card
PYTHONPATH=src "E:/ReproAgent/.venv/Scripts/python.exe" -m pytest \
  tests/unit/test_reproduction_skill.py -q
```

Expected: FAIL with `AttributeError: module 'reproagent.core.agent' has no attribute 'reproduction_skill_checklist'`.

- [ ] **Step 4: Implement the loader**

In `src/reproagent/core/agent.py`, add above `reproduction_skill()`:

```python
DECISION_CARD_START = '<!-- decision-card -->'
DECISION_CARD_END = '<!-- /decision-card -->'


def reproduction_skill_checklist():
    """Load the compact rules that stay visible on every exploration request."""
    source = files('reproagent').joinpath(
        'resources/skills/reproduce-bug/SKILL.md').read_text(encoding='utf-8')
    start = source.find(DECISION_CARD_START)
    end = source.find(DECISION_CARD_END, start + len(DECISION_CARD_START)) if start >= 0 else -1
    if start < 0 or end < 0:
        raise ValueError('bundled reproduction skill has no decision card')
    body = source[start + len(DECISION_CARD_START):end].strip()
    if not body:
        raise ValueError('bundled reproduction skill has an empty decision card')
    return body
```

- [ ] **Step 5: Run the tests to verify they pass**

Run the same pytest command. Expected: `8 passed` (the file previously had 5
tests; this task adds 3), no failures.

---

### Task 2: Inject the checklist on every later exploration request

**Files:**
- Modify: `src/reproagent/adapters/agentscope/middleware.py:222-262`
- Test: `tests/integration/test_reproduction_skill_delivery.py`

**Interfaces:**
- Consumes: `reproduction_skill_checklist()` from Task 1
- Produces: a per-call `SystemMsg(name='reproduction-checklist')` on every
  active exploration request after the first; never persisted to memory

- [ ] **Step 1: Extend the integration test with the expected behavior**

In `tests/integration/test_reproduction_skill_delivery.py`, import the loader:

```python
from reproagent.core.agent import reproduction_skill_checklist
```

Inside `test_skill_reaches_only_the_first_request_without_extra_tools_or_calls`,
after the existing `systems` assertions, add:

```python
    card = reproduction_skill_checklist()
    assert card
    assert systems[0].count(card) == 1
    assert systems[1].count(card) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd C:/Users/Dell/.codex/worktrees/skill-decision-card
PYTHONPATH=src "E:/ReproAgent/.venv/Scripts/python.exe" -m pytest \
  tests/integration/test_reproduction_skill_delivery.py -q
```

Expected: FAIL because the second request has no checklist.

- [ ] **Step 3: Implement the per-request injection**

At the start of the Skill block in `middleware.py` (around line 222), replace:

```python
        if not self._skill_injected:
```

with:

```python
        inject_full_skill = not self._skill_injected
        if inject_full_skill:
```

After the existing `exploration-progress` insertion (after the block that builds
`SystemMsg(name='exploration-progress', ...)`), and before
`await self._check_progress_capacity(...)`, insert:

```python
            if not inject_full_skill:
                from ...core.agent import reproduction_skill_checklist
                input_kwargs = {**input_kwargs, 'messages': [*input_kwargs['messages'],
                    SystemMsg(name='reproduction-checklist',
                              content=reproduction_skill_checklist())]}
```

The final block should read:

```python
            input_kwargs = {**input_kwargs, 'messages': [*input_kwargs['messages'],
                SystemMsg(name='exploration-progress', content=progress)]}
            if not inject_full_skill:
                from ...core.agent import reproduction_skill_checklist
                input_kwargs = {**input_kwargs, 'messages': [*input_kwargs['messages'],
                    SystemMsg(name='reproduction-checklist',
                              content=reproduction_skill_checklist())]}
            await self._check_progress_capacity(agent, input_kwargs['messages'], input_kwargs['tools'])
```

- [ ] **Step 4: Run the integration test to verify it passes**

Run the same pytest command. Expected: PASS, and the test still asserts one full
Skill body only in `systems[0]`.

- [ ] **Step 5: Run both related test files together**

```bash
PYTHONPATH=src "E:/ReproAgent/.venv/Scripts/python.exe" -m pytest \
  tests/unit/test_reproduction_skill.py \
  tests/integration/test_reproduction_skill_delivery.py -q
```

Expected: all pass.

---

### Task 3: Cover the packaged loader

**Files:**
- Modify: `tests/integration/test_reproduction_skill_package.py`

**Interfaces:**
- Consumes: `reproduction_skill_checklist()` from the built wheel
- Produces: proof that the same `SKILL.md` resource carries the card outside a checkout

- [ ] **Step 1: Extend the wheel test**

Replace the resource-reading block and subprocess script with:

```python
    resource = 'reproagent/resources/skills/reproduce-bug/SKILL.md'
    with ZipFile(wheel) as archive:
        source = archive.read(resource).decode('utf-8').replace('\r\n', '\n')
    body = source.partition('\n---\n')[2].strip()
    start = source.index('<!-- decision-card -->') + len('<!-- decision-card -->')
    end = source.index('<!-- /decision-card -->')
    expected_card = source[start:end].strip()
    script = """import json, sys
sys.path.insert(0, sys.argv[1])
from reproagent.core.agent import reproduction_skill, reproduction_skill_checklist
import reproagent
print(json.dumps({'body': reproduction_skill(), 'card': reproduction_skill_checklist(),
                  'module': reproagent.__file__}))
"""
    data = json.loads(command([sys.executable, '-I', '-c', script, wheel], cwd=tmp_path).stdout)
    assert data['body'] == body and body
    assert data['card'] == expected_card and expected_card
    assert str(wheel) in data['module']
```

- [ ] **Step 2: Run the package test**

```bash
PYTHONPATH=src "E:/ReproAgent/.venv/Scripts/python.exe" -m pytest \
  tests/integration/test_reproduction_skill_package.py -q --basetemp="C:/Users/Dell/.codex/worktrees/skill-decision-card/.pytest-tmp-package"
```

Expected: PASS.

---

### Task 4: Full targeted regression

**Files:**
- No new files.

- [ ] **Step 1: Run the three Skill-related test files**

```bash
cd C:/Users/Dell/.codex/worktrees/skill-decision-card
PYTHONPATH=src "E:/ReproAgent/.venv/Scripts/python.exe" -m pytest \
  tests/unit/test_reproduction_skill.py \
  tests/integration/test_reproduction_skill_delivery.py \
  tests/integration/test_reproduction_skill_package.py -q \
  --basetemp="C:/Users/Dell/.codex/worktrees/skill-decision-card/.pytest-tmp-skill"
```

Expected: all pass (the previous three-file baseline was 8; this plan adds 3
unit tests and changes the integration/package assertions, so expect 11).

- [ ] **Step 2: Record the working-tree diff**

```bash
cd C:/Users/Dell/.codex/worktrees/skill-decision-card
git status --porcelain
git diff | sha256sum
```

Expected: modified `SKILL.md`, `agent.py`, `middleware.py`, and the three test files;
no commit, no push.

---

### Task 5: Real A/B — one-shot vs two-tier delivery

**Files:**
- Create (outside the repo): `C:/Users/Dell/.codex/worktrees/task-state-context/verification-2026-10-23/skill-ab/skill-ab7/`
- Reuse: `.../skill-ab/run_sourced_task.ps1`

**Interfaces:**
- Consumes: Task 1–4 implementation
- Produces: 3 runs per arm on `sphinx-doc__sphinx-11445`, with preserved artifacts

- [ ] **Step 1: Confirm the arms**

```text
control = C:/Users/Dell/.codex/worktrees/reproduce-bug-skill/ReproAgent/src
          (88f196e + latest SKILL.md, one-shot full Skill)
two-tier = C:/Users/Dell/.codex/worktrees/skill-decision-card/src
          (same commit + same SKILL.md + decision card every later request)
```

Both use the same buggy/fixed repositories and the same Python:
`E:/ReproAgent/.local/swt-bench/envs-pinned/sphinx-doc__sphinx-7.1/Scripts/python.exe`.

- [ ] **Step 2: Create fresh configs**

Copy the existing `skill-ab4/off.json` and `on.json` templates, change only
`output_dir` to `skill-ab7/sphinx11445-{control,two-tier}-r{1,2,3}/run` and
`task_id` to `skill-card-{control,two-tier}-r{rep}`. Do not reuse old output dirs.

- [ ] **Step 3: Run the six interleaved runs**

Use the existing `run_sourced_task.ps1`; it already sets `PATH` so
`shutil.which("rg")` finds `E:/ReproAgent/.local/bin/rg.exe`.

```bash
for rep in 1 2 3; do
  for arm in control two-tier; do
    # point SourcePath at the arm's src, run one config, save logs
    # append the exit code to skill-ab7/exit-codes.txt
  done
done
```

Expected: six complete runs; do not stop on a non-zero exit, because a blocked
or failed run is data.

- [ ] **Step 4: Analyze without widening success**

For each run record:

```text
status, evidence_level, fix_validation_status, stop_reason
attempts, candidate count, total_tokens from model.attempt
duration, ENVIRONMENT_BLOCKED count
whether the first candidate was accepted
whether the Skill full body appeared in exactly one request
whether the checklist appeared in every later exploration request
```

Decision rule:

- **Improvement signal:** two-tier has fewer `ENVIRONMENT_BLOCKED` runs and at
  least as many DONE runs as control across the three pairs.
- **No signal:** same success/block counts, or two-tier is worse.
- Do not treat lower token counts from earlier failures as savings.
- Do not report an official score from this experiment.

- [ ] **Step 5: Write the report**

Create `.../skill-ab7/TWO-TIER-RESULTS.md` with the six-run table, failure
reasons, delivery evidence, and an explicit “no benefit / benefit / inconclusive”
decision. Keep all failed artifacts.

---

## Self-Review

- Spec coverage: card content, loader, per-request injection, packaging, tests,
  and real A/B are each covered by a task.
- No placeholders: every code step contains the actual snippet or command.
- Type consistency: `reproduction_skill_checklist()` is defined in Task 1 and used
  unchanged in Tasks 2, 3, and 5.
- Constraint check: no commit/push steps; all work stays in the branch worktree;
  real calls only in Task 5.
