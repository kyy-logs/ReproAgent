# ReproAgent Pitfall Learning Input Implementation Plan

> **For agentic workers:** Use executing-plans to execute inline, task by task, with TDD and one final fresh-context review. The user explicitly requested writing this plan and immediately executing it; do not pause for another handoff. Acceptance must not use Astra.

**Goal:** After a task, give the existing learning model a bounded, citable account of failed attempts and later adjustments, rather than the Verifier's full judgment payload.

**Architecture:** A dedicated `learning_material.py` selects original-only facts from immutable task records. It retains a few representative candidate attempts, exact bounded source/code fragments, controlled errors and measured outcomes. AgentScope exports bounded tool failures and issued original citations after its reply has been retired and before resources are discarded. The existing independent learning request freezes the chosen material once and proposes at most one advisory card.

**Tech Stack:** Python >=3.11, AgentScope 2.0.9, existing JSON TaskStore, pytest, HTTP MockTransport. No new dependencies, database, service, or paid provider calls for acceptance.

**Spec:** The approved in-chat design (2026-10-11): same model configuration, independent learning context/budget, key exploration experience plus final feedback, <=8192-byte user JSON, three fixed categories, no artificial causal claims. Existing source/integrity, storage and learning lifecycle rules in `docs/superpowers/specs/2026-10-06-reproagent-experience-evolution-design.md` still apply; this plan replaces its reuse of the reviewer payload and its all-or-nothing size handling. CLI default experience configuration follows current `docs/experience.md`.

## Global Constraints

- Keep `INPUT_BYTES=8192`, `CARD_BYTES=2048`, library <=1MiB, three categories `framework/model/workflow`, <=5 tags and <=3 citations per card.
- Learning keeps the same ModelConfig, fresh client/context, 30-second budget, one logical request, <=3 HTTP attempts, no tools or format-correction requests.
- Do not change exploration prompts, permissions, main budget, Verifier, original-source protection, main result, exit code or sealed delivery package. Thinking remains unchanged.
- Cancelled, disabled and read-only tasks do not issue learning requests. Source-invalid material is never treated as reliable evidence.
- Read no fixed runs/source, gold patches/reference tests, reports, old experience bodies or trace data as learning evidence. Never recursively summarize task directories.
- Retain exact source fragments with hash and line ranges; partial source/code is explicitly identified as a fragment. Omit an oversized atom whole, record why, and keep eligible other categories.
- Total serialized model JSON (including schema, grouping and omission metadata) <=8192. No silent head/tail cuts; storage uses `workspace_path`, `safe_child` and atomic write.
- Keep `learning/evidence.jsonl` as the only frozen learning capsule. No persistent full-chat duplicate. IDs, hashes and source task IDs remain program-owned.
- Work in attached `experience-episodes` worktree on `codex/experience-episodes`; main checkout is preserved. Test temp directories must be outside any Git checkout.

## Review Focus

1. Several failed candidates or repeated replays: preserve chronological attempt identities and show measured repeats; do not claim the last test repaired production code (Task 1).
2. Oversized candidate/traceback: other factual groups remain eligible, with explicit omissions, not `skipped_no_evidence` for all data (Task 1).
3. SDK tool errors with trace disabled and historical-memory tool enabled: retain current allowed tool failures, exclude memory content and arbitrary assistant claims; observation faults cannot replace business/cleanup results (Task 2).
4. Altered files, non-original references, Unicode line boundaries and incomplete logs: validate before extracting any citable source; reject untrusted sources (Task 1).
5. Task cancelled/read-only, clients closing, or evidence capsule exists: no learning request/write during cancellation or before main sealing; never overwrite a capsule (Task 2).

## File Map

- Create `src/reproagent/learning_material.py`: independent projection, attempt selection, capture normalization, budget packing and explicit build outcome.
- Modify `src/reproagent/experience.py`: compatibility wrapper, typed build outcome handling, capture handoff, group-aware prompt context and diagnostics.
- Modify `src/reproagent/adapters/agentscope/evidence.py`, `explorer.py`: expose issued references and retain allowed facts after SDK retirement.
- Modify `src/reproagent/core/controller.py`: hand retained learning context to the real experience service after explorer closure, without requiring it from injected strategies/services.
- Modify `src/reproagent/prompts/extract_experience.md`, `docs/experience.md`: explain attempts, fragments, observed outcomes and omissions.
- Tests: new `tests/unit/test_learning_material.py`, existing experience learning/lifecycle and SDK experience integration tests.

## Task 1: Independent original-only learning projection

**Interfaces:**
- `LearningBuildResult(material: LearningInput | None, code: str)`.
- `prepare_learning_input(store, result, context, *, secrets=(), exploration=None) -> LearningBuildResult`.
- Preserve `experience.build_learning_input(...) -> LearningInput | None` as a wrapper for callers.
- Build codes: `ready`, `skipped_no_evidence`, `skipped_input_too_large`, `skipped_invalid_evidence`, `skipped_existing_evidence`; budget/cancellation propagate to existing lifecycle handling.
- Model payload: `task_status`, `evidence[{id,kind,text}]`, `attempts` (chronological, with actual IDs and EID references), `category_materials` (three lists of EIDs), bounded `omissions`, `response_schema`.

**Selection:** Use `run.completed` references and `execution_role=original`, grouping by actual candidate ID. Select the latest candidate and closest earlier candidate with different code or execution settings (fallback to the preceding ID for repeated identical attempts), chronological order. Track all original run/repeat counts; read selected candidates/contracts/snapshots with binding and hash checks. Send no verdict reasoning as proved cause: typed classification and known confirmation status are observations only. Retain candidate hypotheses as explicitly untrusted statements.

**Atoms:** The issue is preserved whole and is subject to the 8192-byte input boundary. Add workflow measurements and small per-attempt execution summaries first, including actual failing test/node/exception/observed comparison. Add controlled protocol errors, exact cited original source segments, candidate code fragments for failing test functions (whole AST function when possible; complete lines and explicit context omissions), and framework error summaries. Use <=2048 serialized bytes per optional atom, <=24 entries, <=16 omissions. Repeated body stays in its original artifact; a source fragment always carries a verified original-file hash and exact line range. The packed payload is measured after every proposed addition; a skipped atom does not erase unrelated groups.

- [x] Write RED tests for two distinct attempts, large original failure still producing workflow material, original-only boundaries, altered source rejection, source/code fragments and UTF-8 final cap.
- [x] Run new tests in an outside-checkout unique temporary directory; confirm expected behavior failures.
- [x] Implement the independent builder and compatibility wrapper. No call/import of `build_review_context`.
- [x] Run learning unit tests, source-protection tests and real saved diag-8365 read-only projection (using an isolated copied task fixture for any frozen learning output); verify <=8192 and actual omitted fields.
- [x] Commit the completed projection with its tests and this plan; ledger commands/results.

## Task 2: Preserve current exploration facts and integrate learning

**Interfaces:**
- `capture_exploration(messages, source_refs, *, secrets=()) -> dict`: keys `source_refs`, `tool_errors`, `capture_issues`; no raw chat or assistant text. <=16 source refs, <=8 complete allowed tool-error records, <=2048 bytes each; never export `read_experience` content.
- Readonly `EvidenceLedger.issued_refs -> tuple[EvidenceRef,...]`.
- `AgentScopeExplorer.learning_context`: initialized empty; captured after `runtime.aclose()` while its messages are still available. Factory cleanup always runs even when capture fails.
- `ExperienceService.retain_exploration(context)` and `prepare_learning_input(... exploration=self.exploration)`; Controller uses an optional callable handoff after explorer closes. Existing injected ports remain compatible.

- [ ] Write RED SDK/lifecycle tests with trace disabled: a real Read error survives close, original source refs survive, old memory and arbitrary assistant claims are absent, and learning begins only after main clients close and result seals.
- [ ] Implement capture/handoff without extra model requests or changed tool results. Include all needed tool-call association fields as measured facts; missing links remain explicit.
- [ ] Wire build codes into `LearningResult` and learning events. Diagnostics distinguish oversized from invalid/missing material; selected/omitted information is included in the learning payload and observation event.
- [ ] Update extraction prompt and docs: independent request sees representative pitfall history, fragments are not complete programs, temporal sequence/hypothesis is not proof of causation. No successes are invented for failed/exhausted tasks.
- [ ] Run all experience lifecycle/evaluation/SDK tests; run full pytest and Node viewer tests. Preserve logs and counts.
- [ ] One fresh reviewer (GPT-6.1, not Astra) reviews the branch against this plan. Reproduce important findings RED->GREEN; run affected checks and a green full suite after fixes. No paid-model semantic accuracy claim.
- [ ] Write acceptance record and commit final feature. Keep the worktree for review; no push/merge unless authorized separately.

## Execution Ledger

Use `.superpowers/sdd/2026-10-11-reproagent-pitfall-learning-input/progress.md` for task progress, commands, review findings and rulings. Baseline is main commit `1c7a604`; relevant baseline tests run in the worktree environment before edits. Final receipt contains all incomplete/skipped checks and the limits of offline validation.
