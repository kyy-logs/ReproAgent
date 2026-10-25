# Experience Draft Decision and Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every post-failure candidate explicitly choose `lesson_draft` or `lesson_status="none"`, optionally recover omitted drafts with a bounded post-task fallback, and keep rejected-candidate drafts reviewable.

**Architecture:** Extend the existing `draft_review` experiment only. `write_candidate` gains an optional decision field; the draft session records `received/none/omitted/invalid_decision`. A per-call reminder appears after a recoverable failure. A default-off fallback uses one bounded learning request to synthesize a draft from the existing material, then the existing review call decides whether to write a card. Legacy mode is unchanged.

**Tech Stack:** Python >=3.11, AgentScope 2.0.9, existing TaskStore, dataclasses, pytest/httpx MockTransport.

**Spec:** `docs/superpowers/specs/2026-10-25-experience-draft-decision-design.md`

## Global Constraints

- Only `experience_mode="draft_review"` changes behavior; `legacy` stays byte-compatible.
- Candidate publication, Controller flow, Verifier decision, source evidence rules, fixed isolation, and hard step/time limits are unchanged.
- A bad draft, `none` decision, storage failure, or fallback failure must never block a candidate or change the main result.
- `lesson_draft` remains four fields: situation/observation/lesson/source_ids; 1 KiB max; 1-3 distinct real source IDs.
- Source IDs for inline drafts remain visible `learning_source_id` values, never paths, hashes or SDK call IDs.
- Fallback draft source IDs are valid EIDs from the current frozen material; fallback is default off.
- Fallback uses at most one extra logical learning request; review remains one logical request; each has the existing 30-second learning budget.
- The only events are controlled codes and bounded metadata; never draft prose or model free text.
- No commit, push, merge, or product changes outside this worktree without explicit authorization.
- Real model calls require separate explicit authorization; offline tests use MockTransport only.

---

### Task 1: Explicit lesson decision

**Files:**
- Modify: `src/reproagent/experience_drafts.py`
- Modify: `src/reproagent/adapters/agentscope/tools.py`
- Modify: `src/reproagent/adapters/agentscope/middleware.py`
- Test: `tests/unit/test_experience_drafts.py`
- Test: `tests/integration/test_agentscope_experience_drafts.py`

**Interfaces:**
- `ExperienceDraftSession.offer(raw: object, *, status: str | None = None, call_id: str, arguments_hash: str) -> str`
- Codes: `received`, `none`, `omitted`, `invalid_decision`, `invalid`, `invalid_sources`, `store_error`.
- `_WriteCandidateWithLessonParams` gains `lesson_status: Literal["draft", "none"] | None = None`.

- [ ] **Step 1: Write the failing session-decision tests**

Add to `tests/unit/test_experience_drafts.py`:

```python
def test_explicit_none_is_recorded_without_replacing_a_valid_draft(tmp_path):
    store = TaskStore(tmp_path / "task")
    session = ExperienceDraftSession(store)
    assert session.offer(registered_draft(session), call_id="call-1", arguments_hash="h1") == "received"
    assert session.offer(None, status="none", call_id="call-2", arguments_hash="h2") == "none"
    assert session.latest.call_id == "call-1"
    events, _ = store.read_events()
    assert [event.payload["code"] for event in events if event.kind == "experience.draft"] == ["received", "none"]


def test_missing_decision_and_conflicting_decisions_are_controlled(tmp_path):
    store = TaskStore(tmp_path / "task")
    session = ExperienceDraftSession(store)
    assert session.offer(None, status=None, call_id="c1", arguments_hash="h1") == "omitted"
    assert session.offer(registered_draft(session), status="none", call_id="c2", arguments_hash="h2") == "invalid_decision"
    assert session.offer(None, status="draft", call_id="c3", arguments_hash="h3") == "invalid_decision"
    assert session.latest is None
```

- [ ] **Step 2: Run RED**

Run:

```bash
cd E:/ReproAgent
PYTHONPATH="src;." E:/ReproAgent/.venv/Scripts/python.exe -m pytest \
  tests/unit/test_experience_drafts.py -q \
  --basetemp=C:/Users/Dell/AppData/Local/Temp/reproagent-draft-decision/task1-red
```

Expected: FAIL because `offer()` has no `status` parameter.

- [ ] **Step 3: Implement decision handling**

In `experience_drafts.py`:

```python
DRAFT_CODES = ("received", "none", "omitted", "invalid_decision", "invalid",
               "invalid_sources", "store_error")
```

Change `offer` to:

```python
    def offer(self, raw: object, *, status: str | None = None,
              call_id: str, arguments_hash: str) -> str:
        if status not in (None, "draft", "none"):
            self.saw_invalid = True
            self.last_invalid_code = "invalid_decision"
            return self._record("invalid_decision", call_id=call_id,
                                arguments_hash=arguments_hash, decision=status)
        if status == "none":
            if raw is not None:
                self.saw_invalid = True
                self.last_invalid_code = "invalid_decision"
                return self._record("invalid_decision", call_id=call_id,
                                    arguments_hash=arguments_hash, decision="none")
            self.saw_none = True
            return self._record("none", call_id=call_id, arguments_hash=arguments_hash)
        if raw is None:
            if status == "draft":
                self.saw_invalid = True
                self.last_invalid_code = "invalid_decision"
                return self._record("invalid_decision", call_id=call_id,
                                    arguments_hash=arguments_hash, decision="draft")
            return self._record("omitted", call_id=call_id, arguments_hash=arguments_hash)
        # existing parse + source validation + received path follows, unchanged
```

Keep the existing `none`/`omitted` events free of draft prose.

- [ ] **Step 4: Add the opt-in schema field**

In `tools.py`:

```python
class _WriteCandidateWithLessonParams(ParamsBase):
    model_config = ConfigDict(extra="forbid")
    files: list[_CandidateFileParams] = Field(min_length=1, description="The complete candidate files, at least one test module.")
    hypothesis: str = Field(min_length=1, description="What the candidate tests and which contract fact it reproduces.")
    lesson_status: Literal["draft", "none"] | None = Field(
        default=None,
        description='Use "draft" with lesson_draft, or "none" when no reusable pitfall exists.')
    lesson_draft: Any | None = Field(default=None, description="Optional task-scoped lesson draft.")
```

The legacy `_WriteCandidateParams` is not changed.

- [ ] **Step 5: Forward the decision from the real tool response**

In `middleware.py`, replace the draft offer block with:

```python
                    if (name == "write_candidate" and self.draft_session is not None
                            and bool((item.metadata or {}).get(DRAFT_ELIGIBLE_METADATA))):
                        try:
                            arguments = parse_json(call.input)
                            self.draft_session.offer(
                                arguments.get("lesson_draft"),
                                status=arguments.get("lesson_status"),
                                call_id=call.id,
                                arguments_hash=canonical_hash(arguments))
                        except Exception:
                            pass
```

- [ ] **Step 6: Run GREEN**

Run the RED command. Expected: all tests pass.

- [ ] **Step 7: Run integration and legacy boundary**

```bash
PYTHONPATH="src;." E:/ReproAgent/.venv/Scripts/python.exe -m pytest \
  tests/integration/test_agentscope_experience_drafts.py \
  tests/integration/test_agentscope_domain_tools.py -q \
  --basetemp=C:/Users/Dell/AppData/Local/Temp/reproagent-draft-decision/task1-green
```

Expected: PASS; legacy schema has no `lesson_status` or `lesson_draft`.

---

### Task 2: Decision-point reminder

**Files:**
- Modify: `src/reproagent/adapters/agentscope/middleware.py`
- Test: `tests/integration/test_agentscope_experience_hint.py`
- Test: `tests/unit/test_experience_runtime_hint.py`

**Interfaces:**
- Reminder message name: `lesson-decision`.
- Text constant: `LESSON_DECISION_TEXT = "上次操作未通过。下一次 write_candidate 必须提供 lesson_draft，或声明 lesson_status=\\"none\\"。"`
- One reminder per task; not persisted to SDK history.

- [ ] **Step 1: Write the failing wire test**

Add to `tests/integration/test_agentscope_experience_hint.py`:

```python
from types import SimpleNamespace
from reproagent.adapters.agentscope.middleware import ExplorationMiddleware
from reproagent.experience_drafts import ExperienceDraftSession

def test_decision_reminder_follows_a_continue_feedback_once(tmp_path):
    env = runtime_environment(tmp_path, projects, facts, answers=[])
    middleware = ExplorationMiddleware(
        env.context, env.gate, env.store,
        draft_session=ExperienceDraftSession(env.store))
    middleware.phase_context = SimpleNamespace(feedback="")
    assert middleware._lesson_decision_message() is None
    middleware.phase_context = SimpleNamespace(feedback='{"decision":"continue"}')
    message = middleware._lesson_decision_message()
    assert message is not None and message.name == "lesson-decision"
    assert "上次操作未通过" in message.content
    assert middleware._lesson_decision_message() is None
```

- [ ] **Step 2: Run RED** with the same pytest command; expected FAIL because no reminder exists.

- [ ] **Step 3: Implement the reminder**

Add to `ExplorationMiddleware.__init__`:

```python
        self._lesson_reminded = False
```

Add a method:

```python
    def _lesson_decision_message(self):
        if self._lesson_reminded or self.draft_session is None:
            return None
        try:
            feedback = parse_json(self.phase_context.feedback) if self.phase_context and self.phase_context.feedback else {}
        except (ValueError, TypeError):
            feedback = {}
        if feedback.get("decision") != "continue":
            return None
        self._lesson_reminded = True
        return SystemMsg(name="lesson-decision", content=LESSON_DECISION_TEXT)
```

In `on_model_call`, after the progress message and before the optional hint, append the reminder if not `None`; include it in the capacity check. If capacity fails, remove only the reminder, restore the original request, and retry; do not stop the task.

- [ ] **Step 4: Run GREEN and the hint regression**

```bash
PYTHONPATH="src;." E:/ReproAgent/.venv/Scripts/python.exe -m pytest \
  tests/integration/test_agentscope_experience_hint.py \
  tests/unit/test_experience_runtime_hint.py -q \
  --basetemp=C:/Users/Dell/AppData/Local/Temp/reproagent-draft-decision/task2
```

Expected: PASS.

---

### Task 3: Rejected-candidate draft remains reviewable

**Files:**
- Test: `tests/integration/test_experience_draft_review_lifecycle.py`
- Test: `tests/unit/test_experience_drafts.py`

**Interfaces:**
- No new public function; this task proves the existing separation and adds a regression.

- [ ] **Step 1: Write the failing regression**

Add a lifecycle test that:

1. runs a task whose candidate is rejected (`INVALID_CANDIDATE` or `REPLAY_UNCONFIRMED`);
2. stores a valid draft;
3. calls `ExperienceService.learn` with the rejected result and a review gateway that returns `accept/SUPPORTED`;
4. asserts the card is written and the review payload contains `task_status` of the rejected result;
5. asserts the review reason cannot claim the candidate succeeded (the fake gateway only checks the payload; production prompt already forbids it).

Use `prepare_review_input` with a rejected `TaskState.FAILED` or `INVALID` result; do not use a DONE result.

- [ ] **Step 2: Run the regression**

Run the lifecycle test file. This task is a regression gate, not a new production behavior: if it already passes, record it as regression coverage and do not invent a production change. If it fails, the failure is the RED evidence and the smallest separation fix may be implemented.

- [ ] **Step 3: Add the regression fixture only**

Ensure the test uses a draft source that was recorded before the draft and a review gateway that returns a complete seven-field card.

- [ ] **Step 4: Run GREEN**

```bash
PYTHONPATH="src;." E:/ReproAgent/.venv/Scripts/python.exe -m pytest \
  tests/integration/test_experience_draft_review_lifecycle.py -q \
  --basetemp=C:/Users/Dell/AppData/Local/Temp/reproagent-draft-decision/task3
```

Expected: PASS.

---

### Task 4: Optional post-task fallback

**Files:**
- Modify: `src/reproagent/core/models.py`
- Create: `src/reproagent/prompts/extract_fallback_draft.md`
- Modify: `src/reproagent/experience_review.py`
- Modify: `src/reproagent/experience.py`
- Test: `tests/unit/test_experience_review.py`
- Test: `tests/integration/test_experience_draft_review_lifecycle.py`

**Interfaces:**
- `TaskRequest.experience_draft_fallback: bool = False` (validated boolean).
- `ExperienceDraftSession.saw_none: bool` records that the model explicitly chose `lesson_status="none"`.
- `extract_fallback_draft(material, gateway, context, store, *, secrets=()) -> LessonDraft | None` in `experience_review.py`.
- `prepare_fallback_review_input(material, draft) -> LearningInput | None` attaches `draft` and `draft_evidence_ids` to the material; source_ids must be EIDs from the current material, not paths.
- Fallback is used only when `draft_session.latest is None`, no explicit `none`, and the flag is true.

- [ ] **Step 1: Write failing fallback tests**

Add:

```python
def test_fallback_draft_uses_material_eids_and_rejects_paths(tmp_path):
    # material with E1/E2; extractor returns {"lesson_draft": {... "source_ids": ["E1"]}}
    # parse must accept E1 and reject "src/parser.py"
```

and:

```python
def test_fallback_is_skipped_when_explicit_none_or_disabled(tmp_path):
    # draft_session.saw_explicit_none=True or request.experience_draft_fallback=False
    # -> no extra gateway call
```

- [ ] **Step 2: Run RED**

Run `tests/unit/test_experience_review.py`; expected FAIL for missing functions/field.

- [ ] **Step 3: Implement the fallback**

Add `experience_draft_fallback: bool = False` to `TaskRequest`, with `__post_init__` rejecting non-bool. In `experience_review.py`:

```python
def fallback_draft_schema():
    return {"type": "object", "properties": {"lesson_draft": {
        "type": "object", "properties": {
            "situation": {"type": "string"}, "observation": {"type": "string"},
            "lesson": {"type": "string"},
            "source_ids": {"type": "array", "minItems": 1, "maxItems": 3,
                           "uniqueItems": True, "items": {"type": "string"}}},
        "required": ["situation", "observation", "lesson", "source_ids"],
        "additionalProperties": False}}, "required": ["lesson_draft"],
        "additionalProperties": False}
```

`extract_fallback_draft` sends one `response_kind="learning"` request with the prompt and `material.payload`; parse the JSON; call `parse_lesson_draft`; require every source ID in `material.evidence_map` and at least one cited entry whose kind is not `agent_hypothesis`. No retry/format correction.

`prepare_fallback_review_input` deep-copies `material.payload`, adds the draft and `draft_evidence_ids`, sets `response_schema=review_schema()`, and returns `None` if it exceeds `INPUT_BYTES`.

In `ExperienceService._learn` draft branch, when no latest draft and no explicit `none` and the flag is true:
1. build legacy material with `prepare_learning_input`;
2. call `extract_fallback_draft`;
3. if a draft is returned, call `prepare_fallback_review_input`;
4. then call the existing `review_experience` and append as usual.

Track `fallback_used` in `LearningResult` (defaults False) and record `experience.review` with `fallback_used`.

- [ ] **Step 4: Run GREEN**

Run the two fallback test files. Expected: PASS, and no gateway call when the flag is off or `none` was explicit.

---

### Task 5: Integration and real-case verification

**Files:**
- Test: `tests/integration/test_experience_draft_review_lifecycle.py`
- Docs: `docs/experience.md`, `examples/task.experience.json`
- Evidence: `repro-results/experience-draft-decision-20261025/VERIFICATION.md`

- [ ] **Step 1: Run the offline matrix**

```bash
PYTHONPATH="src;." E:/ReproAgent/.venv/Scripts/python.exe -m pytest \
  tests/unit/test_experience_drafts.py \
  tests/unit/test_experience_review.py \
  tests/unit/test_experience_runtime_hint.py \
  tests/unit/test_learning_review_material.py \
  tests/unit/test_learning_capture.py \
  tests/integration/test_agentscope_experience_drafts.py \
  tests/integration/test_agentscope_experience_hint.py \
  tests/integration/test_experience_draft_review_lifecycle.py \
  -q --basetemp=C:/Users/Dell/AppData/Local/Temp/reproagent-draft-decision/task5
```

Expected: PASS; preserve any first failure.

- [ ] **Step 2: Real 5227, only with explicit authorization**

Use the existing safe launcher and isolated library. Configuration:

```json
{"experience_mode":"draft_review","learn_experience":true,
 "experience_draft_fallback":false,
 "limits":{"agent_steps":20,"task_timeout_seconds":600,"command_timeout_seconds":60}}
```

Run once, then inspect:

- `experience.draft` code (`received/none/omitted/invalid_decision`);
- `experience.review` decision and HTTP attempts;
- `experiences.json` and `index.json`;
- main status/evidence/fixed status, tokens and duration;
- fixed content not in trace.

- [ ] **Step 3: Report, do not overclaim**

Report main reproduction and experience chain separately. A written card is not proof of better reproduction; no official SWT score or A/B. Do not modify product code after the real run.

---

## Self-Review

- Spec coverage: explicit decision (Task 1), reminder (Task 2), rejected-candidate adoption (Task 3), fallback (Task 4), real verification (Task 5).
- No placeholders: each task contains concrete APIs, tests and commands.
- Type consistency: `LessonDraft`, `CapturedDraft`, `offer(status=...)`, `LessonDraft` source IDs, `experience_draft_fallback` are used consistently.
- Commit steps are intentionally absent because the user requires no commit/push/merge.
