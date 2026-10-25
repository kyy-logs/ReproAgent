# Refusal Experience Triggers Implementation Plan

> **历史归档（2026-10-24）**：保存当时的设计、运行结果或验收依据，不能据此判断当前默认行为、最新测试数量或总体复现率。
> 当前入口见[文档导航](../../README.md)，旧路径对照见[归档索引](../README.md)。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task. 用户要求当前会话先写计划后直接执行，无二次审批。

**Goal:** grounded-contract 与审阅超限经验可从真实拒绝事实写入，并在对应情境进入下一已有模型请求。

**Architecture:** 程序源头产码；工具错误用现有 middleware，验收超限用可选 AgentContext 结构化阶段事实。两条链共享冻结库和有界注入，学习使用引用同源受控事实。

**Tech Stack:** Python/pytest/AgentScope 2.0.9，现有 JSON 与 trace；无新增依赖。

**Spec:** ../specs/2026-10-12-experience-rejection-triggers-design.md

## Global Constraints

- 五字段学习输出；8192B学习/2048B卡/共享3摘要+2048B+一次详情保持不变。
- 不解析自由错误字符串；code 必须来自实际校验或 Controller 程序结果。
- 不扩大校验上限、不放松 grounded contract、不截断关键证据。
- 不新增 provider 调用、不启用 thinking、不使用 Astra、无付费实验、无 push/合并。
- 使用现有 workspace_path/原子写与冻结机制；基线7948abe，复用隔离工作树。

## Review Focus

- 普通 ValueError 不能冒充 grounded 拒绝；已成功发布不能产生错误条件。
- 空 sources、空 expected、missing_information 的固定缺项准确，拒绝无候选副作用。
- 超限反馈只能来自最近实际程序结果，不能从模型/自由反馈文本猜测。
- 完整工具错误提取对新工具开放后，旧引用错误仍需 source_refs/link_state，错误工具组合继续拒绝。
- 跨 phase gate/取消/去重/容量仍有效；过程经验不能首轮关键词提前注入。

### Task 1: Grounded refusal and learning fact

**Files:** src/reproagent/core/candidate_service.py; adapters/agentscope/tools.py; experience_triggers.py; learning_material.py. Tests: tests/unit/test_experience_triggers.py; tests/integration/test_agentscope_experience_triggers.py.

**Interfaces:** GroundedContractRequired(ValueError) owns code and tuple missing. Supported trigger tool_error/write_candidate/GROUNDED_CONTRACT_REQUIRED; tool returns ERROR with reproagent_error_code. Existing hook delivers small factual exploration.tool_error.

- [x] Write RED tests for actual ungrounded write refusal, exact missing/metadata, no stored candidate/phase finish, next-request injection; cited complete and compact facts yield triggers, wrong tool/uncited fact do not.
- [x] Run named tests; expect missing controlled code/trigger assertions to fail.
- [x] Implement typed source refusal and narrow tool conversion; extend closed combos, controlled learning code, and evidence parser without removing citation-specific safeguards.
- [x] Run domain tools, candidate/phase, learning/capture/material and trigger tests; expect GREEN.
- [x] Commit fix: expose grounded publication refusal as a learning trigger.

### Task 2: Controller overflow into next phase

**Files:** core/models.py; core/controller.py; adapters/agentscope/runtime.py; learning_material.py; experience_triggers.py. Tests: tests/unit/test_controller.py; tests/integration/test_agentscope_experience_triggers.py; tests/unit/test_experience_triggers.py.

**Interfaces:** AgentContext.experience_events: tuple[dict[str,Any], ...]=() appended default field. Controller supplies controller_error/tool empty/REVIEW_CONTEXT_TOO_LARGE only after its confirm_candidate returns that code; next phase opens gate then queues fact. Frozen learning notes preserve action=run_candidate for this actual action.rejected result.

- [x] RED tests: Controller real/context-overflow path passes structured fact to next phase; arbitrary feedback and other outcome do not. SDK next phase sends matching summary, no extra call/step; first phase without fact has no advice. Cited action.rejected fact writes same trigger; wrong action/code not accepted.
- [x] Implement optional phase events, latest actual outcome reset and runtime queueing after gate open. Match learning parser to controlled action.rejected source with action retained.
- [x] Run controller/Verifier/large-review/SDK/learning regressions; expect GREEN. Preserve context-too-large duplicate prevention.
- [x] Commit fix: retrieve overflow lessons on controller feedback.

### Task 3: Complete acceptance

**Files:** docs/experience.md; docs/reviews/2026-10-12-experience-rejection-triggers-acceptance.md; plan progress.

- [x] Offline capture -> frozen cited EID -> validated five-field proposal -> stored new card -> new-task corresponding event -> next wire injection, frozen library unchanged, shared quota intact.
- [x] Update supported trigger list, source ownership and old-card relearning notes; preserve distinction NONE vs candidate count and A/B actual injection gate.
- [x] Fresh whole-branch review using GPT-6.1-sol (not Astra), fix Important findings with RED->GREEN tests.
- [x] Run full pytest with external basetemp C:/Users/Dell/AppData/Local/Temp/reproagent-experience-triggers-verification/rejections-full, redirect E:/ReproAgent/.local/experience-rejections/full.log; expect exit0. Do not change source during final run.
- [x] Commit docs/acceptance, preserve branch; report evidence and actual A/B limits.

## Completed verification

Grounded42 passed; learning/capture/material28 passed; Controller/SDK/trigger71 passed; additional Verifier/large-review/runtime/material40 passed; fresh GPT-6.1-sol review49 passed with no findings; final full suite839 passed /5 skipped,664.00s,exit0. All source acceptance at c58a165. No real provider/Astra; runtime budgets and validators unchanged.
