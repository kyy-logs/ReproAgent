# Verifier Failure Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make failed candidate verification report controlled, evidence-linked gaps and deliver bounded actionable feedback to the next actual exploration request.
**Architecture:** Extend persisted Verdict with optional typed issues; preserve existing classification, semantic response schema and acceptance gates. A pure feedback renderer feeds existing AgentContext.feedback. Controller retains its existing decisions and records their evidence; reports and trace show the same diagnostics.
**Tech Stack:** Python >=3.11, dataclasses, pytest, AgentScope 2.0.9, existing OpenTelemetry facade; no new dependencies.
**Spec:** ../specs/2026-10-14-verifier-failure-feedback-design.md
**Base:** 804ac7b5ef350cbc3dd6de20b3ca0c2d5ddb0faa
**Execution:** User explicitly requested implementation in an isolated worktree. Implement inline now; preserve other sessions' main edits. No paid model run or push.

## Global Constraints

- Use only C:/Users/Dell/.codex/worktrees/verifier-failure-feedback/ReproAgent for product changes; keep main's uncommitted P12 and HTML untouched.
- Keep candidate classes, action/phase result codes, current main semantic schema (seven fields including assertion_reviews), strict citations, hash/source protection, repeat confirmation, optional fixed isolation and export completion gates unchanged.
- Existing original timeout threshold is 2 consecutive attempts per snapshot/environment/role; no new retry policy, permissions or diagnostics model.
- Optional issues defaults to (). New reader supports old records; old strict reader need not support new fields.
- Provider false means unconfirmed, not a proven negative. No semantic claims from invalid answers; no promotion via protocol correction.
- Feedback <= min(8192, tool_response_bytes) UTF-8 bytes, redact before encoding, never slice JSON, explicitly mark omissions. Too-small minimal envelope stops via NEEDS_INFORMATION.
- Complete saved Verdict is immutable; bounded model projection never replaces it. Cancel/budget/cleanup priority remains unchanged.
- Do not add exploration phase restrictions, experience schema/retrieval changes, trace viewer layout changes, new tools, dependencies or model calls.
- Use external basetemp. Reuse E main dependencies in worktree .venv without changing main. Verify worktree import path.
- Do not use Astra. Only independent final review may use an agent; implementation stays inline.

## Review Focus

1. Invalid semantic answers containing false must remain protocol failures with no trusted semantic evidence (Task 2).
2. Secret replacement expanding JSON or containing quotes/newlines must preserve validity and byte limits (Task 3).
3. Replay and fixed evidence must not contaminate original decisions or erase first SINGLE_OBSERVATION (Tasks 4/5).
4. Stale or mutated references must never reappear as verified diagnostic evidence (Task 2).
5. Feedback can fit 8 KiB yet full phase input cannot fit; runtime must refuse without dropping core Issue/contract (Task 5).

## File structure and interfaces

- core/verification_issues.py (new): closed vocabulary, code -> check/origin/recovery and bounded measurement validation; no imports from models.
- core/models.py: VerificationIssue(code, check, origin, message, evidence_refs=(), measurements={}, recovery='inspect_evidence'); Verdict.issues appended default (). Validate types/controlled values at construction and decoded reconstruction.
- core/verification_feedback.py (new): render_verification_feedback(verdict, *, result_code, byte_limit, decision='continue', instruction='', secrets=(), extras=None) -> str. Uses existing BudgetStopped for capacity refusal.
- core/verifier.py: build issues at known branches, derive provider gaps only from schema/citation-valid answer; record diagnostic codes. No acceptance change.
- core/controller.py: candidate_feedback(...) wrapper saves/links decision metadata after Verdict persistence, replaces candidate-specific feedback calls only.
- exporter.py/reporting.py: include issues in bound run summaries and existing report evidence section; old records fall back explicitly.
- prompts/explore.md: describe issues as advisory, current evidence remains authoritative.
- Unit/integration tests named below; docs/reviews/2026-10-14-verifier-failure-feedback-acceptance.md records real results and limitations.

## Task 1: Typed diagnostics and old-record compatibility

**Files:** core/verification_issues.py, core/models.py, tests/unit/test_verification_issues.py (new).
**Produces:** VerificationIssue and validated Verdict.issues tuple; vocabulary consumed by Tasks 2/3.

- [x] Write failing tests: new issue fields; nested encode/decode; old Verdict lacking issues; reject unknown codes/check-origin pairs/recovery, malformed measurements, non-finite floats and bool-as-count. Verify no new model schema fields.
- [x] Run pytest tests/unit/test_verification_issues.py; expected new field assertion fails before implementation.
- [x] Implement controlled mappings from spec, bounded keys/types per code and immutable outer records. Use existing serializer recursion; no migration dependency.
- [x] Run new tests plus tests/unit/test_serialization.py (if present) and tests/unit/test_execution_diagnostics.py; expect exit 0.
- [x] Commit types and tests, record RED/GREEN commands and BASE in ledger.

## Task 2: Verifier branch coverage and claim boundaries

**Files:** core/verifier.py; tests/unit/test_verification_issues.py; tests/unit/test_verifier.py; tests/unit/test_verifier_issue_branches.py (new).
**Consumes:** VerificationIssue and vocabulary.
**Produces:** all returned non-REPRODUCED Verdicts have issues and actual unmet checks; success has none.

- [x] Write failing tests using real Runner fixture where needed: no execution, binding/probe fault, framework error, unavailable import, environment blocked, no executed tests, original pass, missing expectation, changed candidate, review over-cap/redaction over-cap, unavailable evidence, provider false/null/non-success classification, invalid response with false, bad citations and correction-only overflow.
- [x] Run new branch tests; expected empty issues/false-as-unconfirmed assertions fail.
- [x] Wire issue helper into actual returns. Validate issue evidence with resolve before inclusion; an unavailable/changed reference must not be attached. Preserve short circuit and gateway request count. Populate actual failed check(s), retain execution diagnostics and negative-format guard.
- [x] Track protocol failure kind deterministically (schema/unknown citation/missing two-sided citation); do not derive codes by searching exception prose. Retain max 3 existing attempts; final invalid output is MODEL_PROTOCOL_ERROR, with citation-specific issue when known.
- [x] Run branch tests + test_verifier.py + test_review_context.py + test_review_regressions.py; expected existing gates and new diagnostics all pass.
- [x] Commit and ledger; no real provider calls.

## Task 3: Bounded feedback projection

**Files:** core/verification_feedback.py, tests/unit/test_verification_feedback.py (new).
**Consumes:** validated Verdict and issues.
**Produces:** render_verification_feedback interface above; complete JSON or BudgetStopped('NEEDS_INFORMATION').

- [x] Write failing tests for full roundtrip, metadata/diagnostic preservation, Chinese byte boundary, huge reason/references/measurements, extras, secret expansion, minimum envelope failure and input non-mutation.
- [x] Run tests; expected module/feature missing.
- [x] Render existing verdict nesting for execution/replay callers; keep original top-level codes. Preserve main issue/checks/binding/decision/instruction. Shrink optional measurements and refs, excerpt text with omitted markers, omit trailing issues last; do not slice JSON. Minimum retains main issue and all mandatory identity/checks. Reject arbitrary extras keys/oversized mandatory data instead of losing them silently.
- [x] Run feedback tests + issue serialization; expect exit 0, every emitted JSON <= cap.
- [x] Commit and ledger.

## Task 4: Controller decision and feedback delivery

**Files:** core/controller.py, tests/unit/test_controller_verification_feedback.py (new), test_controller_execution_recovery.py.
**Consumes:** actual Verdict; render_verification_feedback.
**Produces:** saved Verdict followed by verification.feedback event and next AgentContext feedback, existing control outcomes unchanged.

- [x] Write failing tests using existing scripted Controller: semantic rejection then corrected candidate; original pass; one timeout then success; two timeouts stop; protocol failure not retried automatically; narrow duplicate guard; replay rejection keeps both IDs and first observation; tiny feedback cap stops; event linkage and saved Verdict remain complete.
- [x] Run new tests; expected new structured issues/decision event absent.
- [x] Add candidate_feedback wrapper, replace candidate-verdict branches only. Keep timeout counters, environment blocking, next_action=narrow_candidate, replay/fixed behavior and root result codes. New issue recovery informs instruction, never grants tool permissions.
- [x] Record bounded verification.feedback with diagnostic_codes, candidate/contract/run identities, decision, output bytes and omissions; reference persisted verdict paths, no long model reason in OTel metadata. Observation sink faults must not change result.
- [x] Run new tests + existing controller/execution recovery/regression tests; expect exit 0.
- [x] Commit and ledger.

## Task 5: Actual SDK request, trace and report

**Files:** exporter.py, reporting.py, prompts/explore.md, tests/integration/test_verification_feedback_delivery.py (new), tests/unit/test_reporting.py, tests/integration/test_execution_diagnostic_reporting.py.
**Consumes:** Controller candidate feedback and saved typed issues.
**Produces:** actual next wire request and report/trace details retain code/check/source/recovery/binding and omissions.

- [x] Write failing request test: produce a real Verifier refusal and Controller feedback, feed it through real AgentScopeRuntime + MockTransport, capture next serialized request and assert exact structured payload. Test phase cap refusal preserves original Issue/contract and sends no request. Bind generated identity to actual refusal, not a handwritten feedback string.
- [x] Write failing report/trace tests for program vs provider labels, malicious Markdown/HTML safety, old Verdict fallback and truncated feedback metadata.
- [x] Run tests; expected missing report/trace fields or missing prompt guidance.
- [x] Add bound verdict issues to run report summaries; display limited codes, checks and recovery in existing evidence section. Use existing escaping and section caps. Safe old fallback does not infer old codes.
- [x] Keep trace existing event detail path; prove code/check source survives exported trace JSON. Add short prompt guidance; no viewer layout rewrite.
- [x] Run SDK/report tests + first browse/progress/observability regressions; expect exit 0.
- [x] Commit; write acceptance doc and mark spec implementation status with actual evidence, no real success-rate claim.

## Task 6: Whole-branch verification and later integration

- [x] Review diff and unchanged acceptance gates. Run full pytest -q in isolated .venv with external basetemp, save log and exact exit/count; no model/key calls.
- [x] One fresh non-Astra reviewer checks integrated base 2e1f967..1bd80a0 for Critical/Important problems. No implementer subagents. Fix any accepted findings through RED/GREEN and relevant regression; preserve uncertainty.
- [x] Record commands, full results, review and any limitations in acceptance/ledger. Verify branch clean and main changes untouched.
- [x] Keep managed worktree until safe integration. User authorized later merge; inspect fresh main before integrating, resolve committed overlap through tests; never overwrite/stash another session's uncommitted edits. Do not push. If another session still edits overlapping main files, deliver clean feature branch for later merge.
