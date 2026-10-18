# Task State and Material Offload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 每轮传递可信的精简任务状态，并让探索工具的大输出保存后可按需读取。

**Architecture:** TaskStore 的现有记录驱动 TaskProgress 派生状态；MaterialStore 负责有界、脱敏的材料登记和读取；ContextBuilder 在探索模型调用前组装一次性 UserMsg。Controller/Verifier 保持验收权。

**Tech Stack:** Python 3.11+、AgentScope 2.0.9、现有 JSON/atomic_write/httpx/pytest；无新依赖。

**Spec:** docs/superpowers/specs/2026-10-18-task-state-context-design.md

## Global Constraints
- 基于 origin/main 0c17d53；不改 E:/ReproAgent 的其他会话文件。
- 材料每项 256 KiB，每任务 4 MiB/128 项；上下文状态 4096 UTF-8 字节；read_artifact offset>=1、limit=1..200，返回受 tool_response_bytes 限制。
- 无额外模型调用；不使用 Astra；不改变成功标准、thinking、权限状态码、源码引用与 fixed 信息隔离。
- 每轮视图仅加入本次 input，不累计进 SDK 历史；全部路径兼容 Windows 长路径。

## Review Focus
- 多字节超长行和很小的返回预算：JSON/UTF-8 保持有效，next_offset 不能无限重复。由 Task 1/3 测试。
- 磁盘写入或脱敏失败：没有虚假可读入口，旧状态可用，原有工具结果保留。由 Task 1/3 测试。
- 跨契约和 fixed-run 内容泄漏：旧引用不能成为当前成功，fixed 内容不能被材料工具读出。由 Task 2 测试。
- 外部文本伪造规则与路径：数据不能升为 system，ID 不能变成任意路径。由 Task 1/4 测试。
- 工具结果入历史但未入下一次 HTTP：测试真实 SDK 序列化后的模型请求，不只测试 hook。由 Task 4 测试。

### Task 1: Bounded material store
**Files:** Create src/reproagent/materials.py; Test tests/unit/test_materials.py.
**Interfaces:** Consumes TaskStore.root and atomic_write/safe_child. Produces MaterialStore(store, secrets=()), capture(text, *, kind, description, upstream_complete=None)->dict; read(artifact_id, offset=1, limit=100, max_bytes=32768, column=0)->dict; catalog()->list[dict].
- [x] Write tests for saved/retrieved original text, redaction, UTF-8 cap, count/total caps, digest tampering, unknown/path IDs, paging and invalid bounds.
- [x] Run `.venv/Scripts/python.exe -m pytest -q tests/unit/test_materials.py` and confirm missing module/API RED.
- [x] Implement bounded content-addressed material bodies plus immutable metadata, digest verification and controlled errors. Return only registered IDs; no filesystem paths accepted by read.
- [x] Run same tests, expected all PASS; commit `feat: add bounded retrievable task materials`.

### Task 2: Derived task state
**Files:** Create src/reproagent/task_progress.py; Modify src/reproagent/store.py; Test tests/unit/test_task_progress.py.
**Interfaces:** Consumes MaterialStore. Produces TaskProgress(store).record(kind,obj,ref), event(kind,payload), phase(phase_context), snapshot(budget=None)->dict, view(max_bytes=4096)->dict; lazy TaskStore.materials and TaskStore.progress.
- [x] Test TaskResult/IssueContract writes update STATE atomically, new contract clears current evidence, original-only run materials, tool refusal blocker, fresh budget, corrupt state and incomplete evidence cannot claim completion.
- [x] Run task tests RED, then implement incremental bounded projection; no fact inference from model prose; fixed-derived material excluded.
- [x] Run `.venv/Scripts/python.exe -m pytest -q tests/unit/test_task_progress.py tests/unit/test_store.py tests/unit/test_materials.py`, expected PASS; commit `feat: persist derived exploration task state`.

### Task 3: Offload integration and reader
**Files:** Modify src/reproagent/adapters/agentscope/tools.py, runtime.py, middleware.py, observability.py, src/reproagent/prompts/explore.md; Create tests/integration/test_task_material_tools.py.
**Interfaces:** Consumes TaskStore.materials, adds read_artifact to TOOL_NAMES and reader budget policy. Source tool middleware saves oversized SDK output and appends bounded artifact notice while preserving original evidence accounting. read_artifact returns MaterialStore.read data, no source citation elevation.
- [x] Write real SDK tool tests for oversized Read round-trip, preserved shown-line refs, tiny capacity and storage failure fallback, unknown ID, reserved reader refusal.
- [x] Run new tests RED; implement reader/tool descriptions, bounded offload notice, redaction before persistence, controlled permissions and unchanged domain results.
- [x] Run `.venv/Scripts/python.exe -m pytest -q tests/integration/test_task_material_tools.py tests/integration/test_agentscope_snapshot_tools.py tests/integration/test_agentscope_domain_tools.py`, expected PASS; commit `feat: offload oversized tool results with safe readback`.

### Task 4: Per-call context builder and acceptance
**Files:** Create src/reproagent/adapters/agentscope/context_builder.py; Modify runtime.py, middleware.py; Test tests/integration/test_task_state_delivery.py; Update docs/observability.md; Create docs/reviews/2026-10-18-task-state-context-acceptance.md.
**Interfaces:** Consumes TaskProgress.phase/snapshot/view. Produces ExplorationContextBuilder(store, context, secrets=()).build(messages, phase_context)->list[Msg]; fresh one-shot UserMsg, context.prepared event.
- [x] Test actual HTTP requests through SDK/MockTransport for state refresh after tool error, offloaded body readback, exactly one task-state per call, data roles, contract version invalidation, no extra calls and full tool-result pairing.
- [x] Run new tests RED; implement context builder after existing advice and before final capacity check; mandatory failure remains explicit.
- [x] Run related SDK/controller/trace tests, then full pytest coverage (final execution split into four isolated groups; commands and totals in acceptance record), expected PASS. Keep logs git-ignored, nested-test temporary directories outside the repository, and report every failure.
- [x] Review whole branch in a fresh reviewer context (no Astra), fix important findings with regression tests, record acceptance; commit `feat: deliver current task state on every exploration call`.

## Execution record
执行方式：当前会话 inline；用户已明确要求计划写完直接执行。每项 RED/GREEN、偏差和最终验收记录在本计划工作目录及验收文档。计划自检：接口一致、五项边界已分配测试，无待定字段。

补充验收边界：目录 ID=catalog 有界分页；元数据完整结构校验；重复登记完整性不受剩余额度影响；元数据失败清理正文并计算孤立文件额度；真实协议事件更新阻塞。独立审查已完成并修复阻断问题；最终分组全量验收 1176 passed / 6 skipped，四组 exit 0，详见验收记录。
