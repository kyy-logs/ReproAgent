# Refusal experience trigger acceptance

> **历史归档（2026-10-24）**：保存当时的设计、运行结果或验收依据，不能据此判断当前默认行为、最新测试数量或总体复现率。
> 当前入口见[文档导航](../../README.md)，旧路径对照见[归档索引](../README.md)。

Status: implementation, independent review and final full suite passed. Base7948abe, branch codex/experience-rejection-triggers. No paid provider calls, thinking change or Astra.

- Baseline22 passed. Grounded RED6 failed/15 passed -> GREEN42 passed(12.15s); learning/capture/material28 passed(41.79s).
- Controller RED3 failed/23 passed (after correcting helper argument) -> GREEN71 passed(66.29s), includes existing Controller/SDK regressions.
- Real SDK grounded rejection -> capture controlled metadata -> cite frozen hashed EID with five learning fields -> write card -> new frozen task -> corresponding error -> next wire summary. No publication on error; ordinary ValueError prose cannot create code.
- Real Controller1024B overflow -> one-shot next phase fact -> frozen action.rejected/run_candidate -> cited card -> new SDK phase after publication -> next wire summary. First request absent, two requests/two steps, library unchanged. Following duplicate result does not replay old overflow; feedback text alone cannot trigger.

Rulings: controller_error is distinct from tool_error and uses appended optional default phase field (cost: port compatibility must be tested). Preserve validators, budgets and six-tool surface, without fabricating old-card conditions (cost: old cards need relearning). Generic callback/log failures remain optional via existing isolation. Actual success-rate improvement and full backward reading by old binaries are not acceptance claims.

Independent GPT-6.1-sol fresh read-only whole-branch review (7948abe..c58a165): no Critical, Important or Minor findings; fresh49 tests passed (19trigger/22SDK/8quota), plus combined missing prerequisites and nine provenance cases checked. Root additional Verifier/large-review/SDK-runtime/material regression40 passed(70.18s). No source/index/branch mutations by reviewer; no re-review required.

Declined-to-judge rulings: actual success-rate improvement remains deferred to authorized frozen A/B (cost: no measured efficacy claim); live provider behavior is not verified with mock HTTP (cost: provider-specific behavior remains unknown); older binaries reading new-trigger cards are outside one-way compatibility (cost: update readers before sharing such cards).

Logs: E:/ReproAgent/.local/experience-rejections/. Branch not pushed/merged.

Final full pytest at runtime/source commit c58a165: 839 passed / 5 skipped, 664.00s, exit0. Basetemp outside all Git/config inheritance: C:/Users/Dell/AppData/Local/Temp/reproagent-experience-triggers-verification/rejections-full. Logs full.log/full.exit in the log directory above. No source changed during acceptance; subsequent changes only record documentation. Trace viewer was not changed in this patch. Execution ledger and review diff preserved as execution-ledger.md and review.diff in the same log directory. No deferred Minor findings. Branch retained, no push or merge authorized in this task.
