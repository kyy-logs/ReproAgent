# Pytest-8906 / 5221 Repair Implementation Plan

> **历史归档（2026-10-24）**：保存当时的设计、运行结果或验收依据，不能据此判断当前默认行为、最新测试数量或总体复现率。
> 当前入口见[文档导航](../../README.md)，旧路径对照见[归档索引](../README.md)。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Execute inline in an isolated worktree; no implementer subagents. One final non-Astra review. Checkbox steps track actual completion.

**Goal:** Verify the existing 8906 Windows private-temp repair against the frozen original candidate, and improve 5221-style assertion discrimination guidance and actionable original-pass feedback.

**Architecture:** Preserve Runner's existing short, unique, per-execution Windows temp directory and bounded cleanup. Keep the current acceptance gates; specialize the existing bounded feedback for CANDIDATE_NOT_REPRODUCED and strengthen generation instructions against self-satisfying metadata. No automatic candidate rewrite or additional model call.

**Tech Stack:** Python, pytest, existing AgentScope runtime/MockTransport, current Workspace/Runner; no new dependencies.

**Spec:** User-approved 8906/5221 scope in this conversation and the repair contracts below. Initial main baseline c7c6a09. Product implementation is isolated from concurrent evaluation-attachment changes.

## Correction to the initial 8906 plan

The initial plan inspected adapter.build_execution and workspace.fresh_run but missed Runner._execute replacing temp_root on Windows. Commits eafc536 and 194ab3e already create an owned short ra-* directory and perform bounded cleanup; legacy pathlib/pytest can remove an extended prefix. The proposed adapter-only prefix change is withdrawn. Preserve the initial plan under the ignored task audit directory for accountability.

The 002 failure paths (270/276 characters) belong to older execution code. Current behavior must be verified rather than described as still unfixed. Do not change adapter.py, paths.py or the existing private-temp policy unless a new controlled reproduction actually shows a defect; that would require revising this plan first.

## Global Constraints

- Work only in the task's managed worktree and codex/pytest-temp-and-assertions branch. Do not stage/stash/overwrite another agent's evals, tests or existing main documents.
- Preserve all 002 records. Frozen replay uses unchanged original candidate file bytes, the original source snapshot and its recorded Python 3.9 venv; fresh output and IDs. Validate source/candidate hashes before execution. No fixed/gold data.
- The short Windows temp location is an already-shipped private directory, not a new shared scratch space. Preserve uniqueness, bounded cleanup, source protection and interpreter identity.
- No changes to step/time/cost limits, tail reserve, fixed exploration phases, tool permissions, semantic schema, experience library, complete-state criteria or official grading.
- No paid model generation. Offline replay and MockTransport prove execution and message delivery, not that a real model adopts the guidance or that either case now earns DONE.
- The existing explore prompt already warns against broad keyword checks. Add concrete neutral-name/docstring/input guidance rather than claim this is a missing general rule.
- Do not inspect exact scope display format from gold or turn it into a universal required string. Original-pass feedback states observed passing execution and a hypothesis to inspect, not a proven reason for passing.

## Files

- Modify src/reproagent/prompts/explore.md: neutral metadata and discriminating assertions, with an early minimal-candidate strategy while preserving genuine blockers and the current budget.
- Modify src/reproagent/core/verification_feedback.py: select a short controlled instruction for CANDIDATE_NOT_REPRODUCED; use the same selected instruction in normal and capacity-fallback paths, with existing redaction.
- Add tests/integration/test_assertion_discrimination_feedback.py: real Controller + Runner + SDK MockTransport checks the next actual request, no new semantic call on an original passing run; tiny-capacity and unrelated-diagnostic boundaries use the existing renderer tests.
- Modify tests/unit/test_verification_feedback.py only if required to pin specialized feedback fallback/redaction without duplicating implementation.
- Add docs/reviews/2026-10-15-pytest-temp-assertion-acceptance.md: exact offline results, corrected plan, replay identities, limitations and review.

## Review Focus

1. Existing 8906 repair: distinguish the historical long path from current private-temp execution; replay confirms reaching makepyfile/runpytest without changing candidate bytes.
2. Original pass: current run remains NOT_REPRODUCED and no semantic model is called merely to diagnose it; advice is not a new finding.
3. Low capacity: specialized action is still complete/redacted and bound, or an explicit capacity refusal; custom instructions and unrelated diagnostics keep their existing semantics.
4. Candidate self-satisfaction: a neutral metadata instruction reaches the actual wire; scripted tests do not prove real-model compliance or a new benchmark success.
5. Budget: no extra exploration/model call and no relaxation of the original gates; an already-exhausted task cannot be promised a new retry.

## Task 1: Baseline and frozen 8906 replay

- [x] Run existing tests/integration/test_runner_private_temp.py, tests/unit/test_private_temp_confirmation.py and tests/unit/test_verification_feedback.py in the isolated venv with an external unique basetemp.
- [x] Validate and load the 002 original snapshot/candidate, request and environment with current serializers; reconstruct in a new ignored output, preserving file hashes and recording new identity bindings.
- [x] Execute through current Runner with the recorded Python 3.9 venv and normal probe/protection/limits; record actual node outcomes, temp root, cleanup and failure stage. No semantic model or fixed version is needed.
- [x] If the old temporary-file failure is gone, document the existing repair as verified and leave runtime/adapter unchanged. Subsequent assertion failures are separate observations, not a declaration of full reproduction.

## Task 2: 5221 generation and passing-run feedback

- [x] Write an actual-wire test: the mock publishes a candidate that passes on original, then asks for information. Assert the program creates CANDIDATE_NOT_REPRODUCED, the next request carries bound complete JSON plus input-contamination advice, and no verdict-model request was made. Assert generation instructions recommend neutral fixture names/docstrings/input and an early minimal candidate when the supported contract permits it.
- [x] Run the new test RED and identify the missing advice, not an infrastructure error.
- [x] Add a controlled instruction constant and a private selection expression in the renderer, chosen from the primary issue code. Preserve explicit custom instruction precedence and use the same selection for a redacted capacity fallback. Do not parse reason prose or add a code/schema.
- [x] Add concise generic generation guidance: avoid putting the expected word in fixture/test names, docstrings, paths or echoed input when testing generated output metadata; separate legitimate inputs from assertions about generated properties. Publish a justified minimal candidate before expanding broad exploration, without clearing genuine missing_information.
- [x] GREEN the real-wire test plus feedback capacity/redaction and existing Controller/SDK regressions. Test a non-CANDIDATE_NOT_REPRODUCED code to ensure it does not receive an unsupported diagnosis.
- [x] Commit only task-owned source/tests/docs. Record RED/GREEN and source commit.

## Task 3: Whole-branch acceptance

- [x] Run the complete offline suite once after both scopes are stable; save exact command, commit, exit and passed/skipped counts. Do not add concurrent main edits to this run.
- [x] One fresh non-Astra reviewer examines c7c6a09..HEAD and these five review-focus boundaries. Fix accepted Important/Critical findings in one RED/GREEN pass, without a second review dispatch.
- [x] Write acceptance and update checkboxes with actual results. Do not claim latest real 5221 agent success, original 8906 bug validation or official score without the corresponding new evaluation.
- [x] Keep the isolated branch for later integration; merge/push only under applicable user authorization and after checking fresh main status. Preserve ignored audit assets before any managed-worktree archive.
