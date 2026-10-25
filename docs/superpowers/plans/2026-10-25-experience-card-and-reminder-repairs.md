# Experience Card and Reminder Repairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本计划交给另一代理执行；本文档的生成不授权当前会话开始实现。

**Goal:** 减少经验卡超长输出，明确字段拒绝原因，并让一次性提醒的准备、丢弃与交付准确关联实际探索调用。

**Architecture:** 复用当前审查请求、卡片校验和探索 middleware。严格保留卡片长度限制；添加字段级诊断和提醒状态事件，不自动改写模型的经验内容，不增加纠错请求。

**Tech Stack:** Python >=3.11、AgentScope 2.0.9、现有 dataclasses/TaskStore、pytest/httpx MockTransport；无新依赖。

**Spec:** 本会话确认的“两块修复”要求，以及 [经验草稿审查设计](../specs/2026-10-25-experience-draft-review-design.md)。与本次细节相关的最终约束在下节；保留其他既有约束。

## Global Constraints

- 当前工作区 `E:/ReproAgent`，分支 `feat/ai-tool-integration`，HEAD `a89871d` 加大量未提交改动。先保存 status 与目标文件哈希，不能 checkout/reset 到裸 HEAD。
- 当前 prompt 已有 name 1–64 / description 1–160；middleware 已有 prepared/delivered/not_delivered 和容量丢弃后重新准备的补丁。先验证已有实现，不重复覆盖。
- 本轮只写计划；执行时保留其他代理修改，默认不提交、push、merge。
- `name` 最多 64 字符，`description` 最多 160 字符；按 Python 字符串长度计算，不按 UTF-8 字节或 token 计算。
- 单卡 4096 字节、学习材料 8192 字节、来源资格、fixed 隔离、主任务验收及预算上限均不变。
- 不新增纠错模型请求，不自动截断、压缩或改写模型返回的经验卡；不得通过提高上限让 165 字符的旧输出入库。
- 提醒是临时输入，不持久化进 SDK 历史，不产生额外模型或工具调用；最多确认交付一次。
- 不使用 Astra，不读取真实密钥或用户经验库。真实模型验证须有该次具体授权；此前“仅离线”的选择持续有效。

## 已核对的事实

- 证据：`repro-results/experience-draft-decision-20261025/REAL-5227.md` 及 `5227-authorized/run/observability/trace.json`。
- 该次主任务 DONE / DIFFERENTIAL_VALIDATED / fixed passed，合法草稿来源为 L62/L87/L109。
- 实际审查回答为 revise/SCOPE_NARROWED，name 长度 27、description 长度 165；随后程序返回 invalid_fields，未写卡。
- 该次真实请求的审查 prompt 尚无当前新增的 64/160 字符文字约束；schema 的 anyOf 分支已经包含 maxLength，不能误说 schema 完全没有限制。
- 39 份 wire_request 中只有 4 份正文完整；其余截断/省略。因此“采集中未找到提醒”不等于“证明所有请求都未交付”。
- 消息名 lesson-decision 不保证保留在 HTTP JSON 中；验收须匹配实际提醒正文和事件。
- 当前 delivered 事件在模型 guard 已扣步后仍使用 steps_used + 1，存在关联序号偏一。现有测试文件已补部分相关断言，但该轮测试被用户叫停，不能记为通过。

## Review Focus

1. 160/161 字符、中文及脱敏扩长：校验实际待写内容，给出准确长度，不回显模型正文。由 Task 1 覆盖。
2. 异常类型、空白和总卡片超限：不能一律改成长度错误或改变既有拒绝码。由 Task 1 覆盖。
3. 容量丢弃后下轮恢复：不能准备一次就永久失去提醒，也不能重复交付。由 Task 2 覆盖。
4. HTTP 重试、预算停止、无模型响应：不能错记 delivered 或错关联调用序号。由 Task 2 覆盖。
5. trace 正文截断或日志写失败：事件仍可诊断；无证据时保留未知，日志故障不影响候选。由 Task 2/3 覆盖。

## Task 1: 生成约束与字段长度诊断

**Files**
- Modify: `src/reproagent/prompts/review_experience.md`。
- Modify: `src/reproagent/experience.py` 的 LearningResult、LearningValidationError、validate_experience、ExperienceService._learn。
- Verify existing: `src/reproagent/core/protocol.py:learning_schema`、`src/reproagent/experience_review.py:review_schema`。
- Test: `tests/unit/test_learning_validation_errors.py`、`tests/unit/test_experience_review.py`、`tests/integration/test_experience_draft_review_lifecycle.py`。

**Interfaces**
- `LearningValidationError(..., field_errors=())` 增加有界诊断；新增受控码 `invalid_field_length`。
- `LearningResult.field_errors: tuple[dict, ...] = ()` 追加默认字段，不改变现有位置参数顺序。
- field_errors 最多两项，每项精确为 `{field, actual_length, limit}`；field 只能是 name/description，limit 只能是对应的 64/160。不得携带字段值或任意 provider 文本。

- [ ] **1. 先补边界与生命周期测试并见红。** name=64、description=160 合法；65/161/165 拒绝；同时超限输出两项诊断。覆盖中文、类型错误、空白以及脱敏后变长。类型/空白仍走 invalid_fields，总卡片超限仍走 invalid_card_size。
- [ ] **2. 用真实已录响应做离线反例。** 将其脱敏后的经验 JSON 复制到临时测试材料，使用临时合法证据映射；165 字符必须拒绝，错误精确指出 description/165/160。不得修改历史记录、库或把手工缩短后写卡冒充真实成功。
- [ ] **3. 最小实现字段校验和诊断透传。** 对已通过类型检查、按现有策略脱敏后的 name/description 检查长度。长度异常须在通用 ValueError 折叠之前透传，不能再次退化成 invalid_fields。两条 LearningResult 构造路径和 experience.learning 事件均携带 field_errors。
- [ ] **4. 完善 prompt 的软目标。** 保留硬上限；建议 name 不超过 32 字符、description 80–120 字符。软目标不是新的拒绝条件；短但充分的 description 仍可接受。提醒模型保留触发条件与限制，不贴着硬上限生成。
- [ ] **5. 校验 schema 和实际请求。** name/description 限制位于 experience.anyOf 的对象分支，不要错误地从 experience.properties 取值。通过真实 SDK + MockTransport 检查送出的审查请求，不只断言本地提示文件包含一句话。
- [ ] **6. 验证一次审查、零格式纠正。** 超限响应只调用一次逻辑 learning；不写卡、不更新 index、不改变已落盘 TaskResult。提供另一份正常的模拟响应，验证合法卡仍可写入并从目录读取。

Run:
```powershell
python -m pytest tests/unit/test_learning_validation_errors.py tests/unit/test_experience_review.py tests/integration/test_experience_draft_review_lifecycle.py -q --basetemp=<仓库外唯一目录>
```
Expected: 所有测试通过，长度反例有具体诊断；不以模型调用次数增加换取通过。

## Task 2: 提醒状态及调用关联

**Files**
- Modify: `src/reproagent/adapters/agentscope/middleware.py` 的 on_model_call、_lesson_decision_message、_deliver_lesson_decision、_record_lesson_decision。
- Test: `tests/integration/test_agentscope_experience_hint.py`；必要时复用 `tests/integration/test_agentscope_runtime.py` 的真实 SDK 测试工具。

**Interfaces**
- 保留 `_lesson_reminded` 表示“已确认交付”，不能表示“准备过”。
- 准备时冻结本轮 `call_index = budget.steps_used + 1`，在 prepared/丢弃/交付中复用，不能在扣步后重新加一。
- 事件 `experience.lesson_decision` 保留现有 code 词汇：prepared、delivered、not_delivered；增加 delivery_unconfirmed。not_delivered 对应用户所说的 dropped，不再引入同义 code。
- 事件最小字段：`phase, code, reason, next_call_index`；预算停止另有受控 stop_reason。事件只记录程序状态，不保存提醒正文或 provider 原文。

- [ ] **1. 补回归并先见红。** 工具失败后第二轮收到提醒，prepared/delivered 都必须指向调用 2。第 2 轮容量丢弃、第 3 轮交付时，两个结果分别指向 2/3。
- [ ] **2. 验证已有重试语义。** 沿用当前容量丢弃后可重新准备的补丁；同一任务 delivered 最多一次，提醒不得出现在 SDK 持久历史。按实际正文断言 wire，不能搜索消息名判定交付。
- [ ] **3. 固定状态转换。**
  - 最终输入仍含提醒且模型正常返回：delivered，随后才消耗一次资格；即使该回答被协议拒绝，也已经交付。
  - 最终发送前因容量移除：not_delivered/context_capacity，资格仍可用。
  - 发送前预算守卫终止：not_delivered/budget_stopped，保留真实 stop_reason。
  - 模型调用抛异常且无法证明请求未发送：delivery_unconfirmed/model_call_failed，不虚报 delivered 或“确定未发送”。
- [ ] **4. 补故障测试。** 三次 HTTP 尝试属于同一探索调用，不把事件计数翻倍；正常 hint 与 lesson-decision 同时存在时只删除对应消息。记录写失败、trace 关闭、取消/终态均不得改变候选或预算结果。
- [ ] **5. 将事件接入当前 trace 标记机制。** 任务 events.jsonl 与 trace sink 独立失败隔离；只给受控 code/reason 与调用序号，不以采集完整性推断模型采纳了提醒。

Run:
```powershell
python -m pytest tests/integration/test_agentscope_experience_hint.py tests/integration/test_agentscope_runtime.py -q --basetemp=<仓库外唯一目录>
```
Expected: 同一调用的事件与实际 wire 对齐；容量丢弃可恢复；无重复交付、无孤儿 tool_result、无新增模型步骤。

## Task 3: 收尾与真实验证交接

- [ ] 更新 `docs/experience.md`，说明字段级诊断、提醒事件含义，以及“采集缺失”和“未发送”的区别。
- [ ] 执行完整离线 pytest，使用仓库外唯一 basetemp 和已有 `REPROAGENT_RG_PATH`。Windows 如需清理 PSModulePath，只改测试启动进程并记录。保留首次失败和全部 skip 原因。
- [ ] 记录当前源码路径、修改前后哈希、实际导入位置；若其他代理同时修改同一文件，停止重叠写入，先协调，不 reset 别人的补丁。
- [ ] 默认不执行真实模型。收到具体授权后仅跑一次产品 CLI pytest-5227，独立临时库，20 步/600 秒、学习按现有 30 秒限制；不使用 Astra，不发送 fixed 内容，不打印密钥。无金额硬封顶必须如实说明。
- [ ] 分开记录主复现、草稿、review、卡片及 index。仅当真实结果 written/duplicate 且新库目录/正文可读，才能确认该次写库链通过；没有草稿或 review_rejected 不硬凑成功。
- [ ] 将报告交用户，不自动提交、push 或合并。本次验证不能用于声称整体复现率或经验收益提升。

## 与重跑计划的边界

本计划不修改 `Verifier.confirm`、pytest probe 或失败签名。候选过多的独立根因另见 [重跑签名稳定性修复计划](2026-10-25-replay-signature-stability-repairs.md)，两份计划可分别审查和验收。
