# Experience Trigger Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 用户已要求计划完成后直接执行，不再等待二次确认。

**Goal:** 让可靠写入的过程经验通过实际发生的情境找回，并保持旧库及主复现流程兼容。

**Architecture:** 增加不依赖 SDK 的受控 trigger 定义与证据解析；程序生成可选卡片元数据。冻结视图累计管理选中/展示/详情预算，AgentScope hook 在工具结果之后、下一模型请求之前注入相关摘要。

**Tech Stack:** Python、pytest、AgentScope 2.0.9 公共 middleware/ToolResponse metadata、现有 JSON 文件存储和 OTel 观测。无新依赖。

**Spec:** ../specs/2026-10-12-experience-trigger-retrieval-design.md

## Global Constraints

- 模型输出仍恰好五字段；trigger 为程序所有，未知/无依据条件不生成。
- 类别固定 framework/model/workflow；旧库 schema_version=1、空条件旧 ID 兼容。
- 学习 8192 B、单卡 2048 B、库 1MiB；累计最多三条摘要、共享 2048 B、最多一次成功详情。
- 快照仅准备阶段读取一次；问题召回在首轮前；过程召回根据已发生事件，不根据未来能力。
- 不增加模型调用、探索流程限制或额外权限；thinking 不改；不使用 Astra。
- Windows 文件读写继续使用 workspace_path/原子写/锁；不改原始结果或旧实验。
- 不 push/合并、不解密密钥或发起真实评测；最终留下可审查实现与证据。

## Review Focus

- 有触发条件的卡不能因通用标签在探索前泄漏；只读过工具名不代表出现错误。
- 原始输出缺字段、未知错误码、伪造标签/未知工具、重复 EID 必须保持明确拒绝或无触发条件。
- 流式工具 hook 只能观察一次终态；关闭流不丢资源，不能为了观测重跑工具。
- 累计配额跨阶段保持；容量省略卡不可读；已读详情的字节不能因后续摘要重置。
- 非可信建议、未知 metadata、日志故障和取消不能改变工具权限或主结果。

---

### Task 1: Trigger contract and evidence-backed writing

Status: complete; RED 5 failures, GREEN 81 passed / 1 skipped. See acceptance ledger for later boundary regressions.

**Files:** Create src/reproagent/experience_triggers.py, tests/unit/test_experience_triggers.py. Modify src/reproagent/experience.py, src/reproagent/learning_material.py, src/reproagent/prompts/extract_experience.md. Test existing store/learning/material/capture tests.

**Interfaces:** `ExperienceTrigger(kind: str, tool: str, code: str)` frozen dataclass; `trigger_from_fact(fact: dict) -> ExperienceTrigger | None`; `evidence_triggers(entry: dict) -> tuple[ExperienceTrigger,...]`. `ExperienceCard.triggers: tuple[ExperienceTrigger,...] = ()`; `_identifier(..., triggers=())` preserves legacy identity when empty. Metadata key `reproagent_error_code` is produced by product tools only. Parser validates closed kind/tool/code combinations, never parses free result text.

- [ ] Write tests: legacy card JSON missing triggers loads with identical ID; learned card derives conditions from cited frozen structured error only; uncited error and summary guess create no conditions; unsupported metadata and altered evidence are rejected/no match; different conditions change IDs; limits remain enforced.
- [ ] Run new tests: expected RED for absent trigger contract.
- [ ] Implement pure trigger validators, optional card serialization validation, program derivation after EID validation, normative bounded tags. Capture safe error metadata in tool_error atoms; existing notes retain actual allowed action under tool.
- [ ] Update prompt: applicability is conditional; only supported evidence; five-field schema unchanged. Derived metadata is not verified success.
- [ ] Run unit store/learning/trigger/material/capture tests: expected GREEN.
- [ ] Commit `feat: derive experience triggers from cited task facts` and record evidence in ledger.

### Task 2: Cumulative view and event matching

Status: complete; RED 7 failures, GREEN 13 passed.

**Files:** Modify src/reproagent/experience.py; create tests/unit/test_experience_trigger_view.py; existing tests/unit/test_experience_view.py.

**Interfaces:** `ExperienceView.match_event(trigger) -> summaries` yields only unshown exact matches within remaining total count/bytes; no library reads. `admit_summaries(ids)` marks actual display; `restrict_summaries(ids)` keeps first-phase compatibility and excludes dropped suggestions. `read` remains one successful lookup of actually admitted ID. View keeps proposed and admitted cards separately and reserves consumed detail bytes.

- [ ] RED tests: trigger card withheld at startup; exact event returns related card; different tool/code returns none; no substring matching; stable ties; repeated event and phase do not repeat admitted summary; total three cards and 2048 bytes across initial/dynamic/detail; omitted cards cannot be read.
- [ ] Implement compatible initial selection, additive exact event selection, admission and cumulative byte accounting. Skip over large cards without dropping others; no eviction of already shown/read advice.
- [ ] GREEN existing view plus new trigger view tests.
- [ ] Commit `feat: retrieve pitfall experience on matching runtime events` and ledger.

### Task 3: SDK event capture and bounded next-round injection

Status: complete; SDK regression 66 passed; compact facts, trace and learning/capture checks 48 passed. Additional file: src/reproagent/observability.py (two controlled trace attribute keys).

**Files:** Modify src/reproagent/adapters/agentscope/tools.py, middleware.py, runtime.py; create tests/integration/test_agentscope_experience_triggers.py. Existing runtime/domain/experience tests.

**Interfaces:** Tool refusal chunks carry `metadata={"reproagent_error_code": CODE}` for the two citation checks only, preserving ERROR state/text. Middleware accepts optional experience callback; successful permission results never match; reserved/denied use final code. on_acting observes only final ToolResponse ERROR metadata and closes generator. Runtime queues validated triggers (deduplicated, max 16) and injects one bounded advisory UserMsg on next on_model_call, with actual admission only after byte capacity and SDK local count_tokens threshold checks. The input callback is async for this local estimate; protocol errors use same queue before existing correction. Empty trigger serialization uses `_card_record` to preserve legacy sizes.

- [ ] RED real SDK + mock HTTP tests: first request no process advice; actual citation refusal triggers next wire request with summary and intact tool result; wrong error/success does not; repeated failures inject once; trace off works; one detail lookup remains possible; no extra requests or steps.
- [ ] Implement metadata at source using narrow handled citation refusal; add exact event callback to existing hooks without changing handler call count, permissions or gate.
- [ ] Before next model dispatch validate cancellation/gate/budget; match queued facts, limit new message plus relevant prior input by tool_response_bytes; admit only retained summaries and save safe `experience.retrieved`/`experience.injected`/`experience.omitted` events and mark. Diagnostic exceptions are isolated.
- [ ] GREEN SDK tests plus runtime/domain experience regressions. Tests cover small caps, reserved publishing, protocol correction, phase end, shared detail bytes, and callback faults.
- [ ] Commit `feat: inject contextual experience through AgentScope hooks` and ledger.

### Task 4: Documentation and whole-branch acceptance

**Files:** Modify docs/experience.md, prompts/explore.md; create docs/reviews/2026-10-12-experience-trigger-retrieval-acceptance.md. Integration learning lifecycle tests as needed.

- [ ] Document write/read timing, evidence-backed conditions, precise supported event list, legacy fallback, cumulative limits and A/B actual-injection gates. Prompt explains later advisory messages, no extra authority.
- [ ] Offline integration from validated error evidence -> learned new card -> frozen library -> SDK corresponding error -> actual next wire injection; verify only five learning keys, empty library equal tool surface, and no manual tag/card manipulation.
- [ ] Run full pytest with E:/ReproAgent/.local/experience-triggers/full as basetemp and Node trace viewer tests. Expected all pass; no external provider or Astra.
- [ ] Fresh-context whole-branch review, fix important findings RED->GREEN, then required regressions; capture review limitations and deviations.
- [ ] Commit acceptance/plan progress and preserve branch/worktree. Report plan path, implementation result, tests and outstanding real A/B.
