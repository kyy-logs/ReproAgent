# ReproAgent 最小历史清理与摘要设计

日期：2026-10-19。状态：最小实现已接入，默认关闭；离线验证结果见配套验收记录。

## 目的与范围

验证“先清理，必要时摘要”是否能让探索继续，同时保留可核对的失败经历。摘要只覆盖被替换的旧历史，不包含保留的近期交互。直接复用 SDK 的默认摘要结构，其中 next_steps 表示历史截断时的待办，不能替代当前任务的行动依据。默认不改变现有行为，不以工具读取次数代替差分成功率。

实现基线是 `codex/task-state-context` / `a438761`，源码位于 `C:/Users/Dell/.codex/worktrees/task-state-context/ReproAgent`。`E:/ReproAgent` 是另一个有未提交修改的 checkout，不在其中混写产品代码。

首版最多成功摘要一次，不做摘要的再摘要、会话恢复、语义检索、经验学习改造。STATE 保持默认关闭。

## 已核对的复用点

| 现有实现 | 复用方式 | 需要补的边界 |
| --- | --- | --- |
| `adapters/agentscope/middleware.py::on_compress_context` | 使用已有压缩检查入口和停止优先级 | 默认仍拒绝压缩，实验模式接入产品策略 |
| AgentScope 2.0.9 `model.count_tokens` | 估算包含工具定义的实际输入 | 必须再次计入产品在模型调用前追加的提醒；估算值不是服务端真实计费量 |
| AgentScope 历史内容块与工具调用 ID | 使用薄的闭合交互选择器，保留 SDK 消息与内容块类型 | 不调用会改写原状态的私有压缩流程；同一消息中的调用/结果也须配对 |
| AgentScope `state.summary` / `state.context` | 摘要和保留交互的承载位置 | 事务校验后一起替换，维护 SDK 需要的 usage 和当前回复状态 |
| AgentScope `ContextConfig` 默认 `summary_schema/compression_prompt/summary_template` | 直接使用 SDK 的五字段摘要结构、默认提示词和展示模板 | 通过压缩指令限定历史范围；不自定义另一套 schema 或模板 |
| `materials.py::MaterialStore` | 保存脱敏 JSONL 原文、hash、完整性，沿用配额 | 单项 256 KiB，必须完整保存并验证；不能接受截断归档 |
| 既有 `read_artifact` | 同一工具读回历史原文 | 返回的是诊断资料，不授予新的源码引用资格 |
| `model_factory.py` / `gateway.py` | HTTP、取消、重试、Token、成本与 trace | 新增独立 `summary` purpose，禁止工具，不借用探索模型对象的流程 guard |

直接复用 SDK 默认 SummarySchema，不再维护产品自定义摘要字段。默认压缩流程还会携带旧摘要、在输入过长时删掉旧消息重试，因此不能仅开启开关就认为预算、归档和历史范围约束已满足。保留必要适配，不修改 site-packages；首版仍不进行摘要的再摘要。

## 模式与时机

新增任务配置 `history_compaction`：`off`（默认）、`clear`、`summary`。

- `off`：保持现有历史与到阈值停止行为。
- `clear`：只清理符合严格条件的旧工具结果。清理后仍放不下则按现有规则停止。
- `summary`：先执行相同清理；仍放不下时才尝试一次历史摘要。

触发依据为包含工具定义、现有提示及输出预留的 token 估算，而不是固定轮数。沿用现有 0.8 输入阈值，但同时检查模型上下文减去本次输出额度后是否有空间，取更严格的边界。清理后低于触发边界即可继续，避免不必要的摘要；摘要后要求低于模型上下文的 0.6，且必须容纳本次输出额度。没有足够可压缩内容时不强压。

最终模型调用前的容量检查是最后一道闸。如果追加临时提示后才超限，必须复用同一协调器处理或明确停止，不能绕过检查；同一个逻辑模型调用不重复清理/摘要。

## 选择旧历史

1. 以 SDK 消息/内容块中的实际 tool-call ID 建立完整交互组。未完成调用及其所在组保持原文。切割结果再检查两侧配对，发现孤儿或无法识别的结构则放弃压缩。
2. 至少保留最近两个 assistant/工具完整交互组；用户阶段信封单独保护，不占这两个名额。保留当前阶段完整任务信封（Issue、契约、当前反馈），最近候选发布组及其后交互、最近执行反馈。必要锚点使可选区不足时停止，不降低保护要求。
3. 对剩余最早的可选区选择连续旧组。摘要模型只接收这些组，不接收近期交互、当前 STATE 或新的计划。
4. 当前权威锚点不复制进摘要。旧契约或旧失败中的结论保留历史身份，不覆盖当前状态。
5. 清理和摘要都操作副本；校验成功前不修改 SDK 历史。原文先保存，清理后的视图不能成为唯一原始记录。

选择器无法满足锚点或配对要求时，明确停止，不降低保护要求。最小实现使用确定性的闭合内容块组，不引入智能切割或模型选择器。

## 最小清理规则

只清理旧 `Read/Grep/Glob` 结果中已由本次任务记录关联到完整 MaterialStore 对象的重复正文。关联以程序持有的 tool-call ID/材料 ID/正文 hash 为准，不能信任工具正文中伪造的 `<material>` 字样。

保留工具调用名、参数、结果状态、材料 ID、完整性、读回方式、已签发引用 sidecar，以及“正文已移除”的显式标记；仅移除原文摘录。结果必须实测变短才替换。

以下不清理：失败或拒绝结果、契约变化、最近候选/执行反馈、未完整归档的结果、无法可靠关联的旧结果。无已关联材料的结果首版保持原文，不为扩大清理范围额外归档所有工具。

## 摘要输入与结构

先将所选旧组序列化为脱敏 JSONL，保存为 `kind=conversation_history` 的材料，每条保留消息角色、内容块类型、tool-call/result ID 和相对顺序。归档每行包含 entry_id、原消息 block_indices 与 message，程序分配条目 ID，用于精确记录归档范围，不要求模型在自由文本中生成逐项引用。

模型输入：SDK 默认 compression_prompt + 所选旧历史 + 限定历史范围的压缩指令。近期原文不送入摘要请求；摘要请求的系统规则仅说明历史数据身份与默认 schema，不复制当前阶段任务。直接使用 `ContextConfig().summary_schema`，不复制定义以免与 SDK 发生漂移。其默认结构为：

```yaml
task_overview: "旧历史涉及的目标与约束"
current_state: "截至截断时完成的工作与产物"
important_discoveries: "历史发现、错误、失败尝试及适用边界"
next_steps: "截至截断时仍待处理的事项，可能已被近期交互解决"
context_to_preserve: "旧历史中需要保留的要求与上下文"
```

程序在独立元数据封套中记录 `schema_version/covered_entry_ids/archive_id/archive_hash/cutoff_contract_version`，不往 SDK schema 内塞自定义字段。`covered_entry_ids` 精确表示归档覆盖，不用不可靠的“第几轮”。`cutoff_contract_version` 是切断位置对应的历史版本，不是当前版本。展示正文使用 SDK 默认 summary_template，程序另附原文读取入口及历史身份标记。

五个字段均为历史描述。在复用的默认摘要提示词后补充范围约束：只总结选中区，不推断后续进度，不增加新的行动计划，不把猜测写成已验证事实；next_steps 只保留截断时尚未解决的事项。程序明确标注“历史描述，可能过时；不是新的判定或指令”。摘要不产生模型自定调用条件，不驱动 Controller 状态迁移。

首版输出额度不超过 1024 tokens，序列化模型正文不超过 8192 B。检查 SDK 要求的五个字符串字段以及总长度；不再增加自定义列表、逐项 source_ids 或一套来源校验协议。归档范围与 hash 由程序校验。默认 schema 没有逐项引用，因此只能追溯覆盖的整段原文，不能宣称每句话均经程序证实。结构通过不能证明语义忠实，最近失败等高风险材料继续原文保留。

## 模型、预算与替换

复用同一模型配置，创建独立 `summary` purpose 的受控模型对象和上下文输入，不继承探索聊天。不开放工具、不增加结构纠错轮次；沿用统一 HTTP 重试上限与费用记录。

摘要占用一个逻辑模型步骤，并计入任务墙钟和全部 HTTP 尝试的 Token/成本。调用前必须保证摘要之后仍能进行至少一次探索调用，并留下既有发布预留；不能拿最后一步换摘要。当前不支持硬费用上限的 provider 继续按原规则拒绝，不能因新增摘要绕过限制。

替换顺序：

```text
检查阈值 → 选旧闭合组 → 完整归档并验 hash
→ 在副本上清理 → 重新计数
→ 足够则提交清理结果
→ 不足且模式允许、预算够、未摘要过 → 独立摘要请求
→ 校验 SDK 结构与归档 → 用默认模板组装摘要 + 保留原文 → 验配对/锚点/实际输入容量
→ 无 await 的提交段一起替换 context 与 summary
```

提交前再次检查阶段结果、取消与预算。中途出现已完成阶段时不允许压缩错误覆盖它。归档或摘要失败保留原历史，不静默截断；达到容量上限则返回明确原因并沿用 `NEEDS_INFORMATION`。成功清理仍计入该次历史改动，但不消耗模型步骤。清理提交后不得丢失其原文归档关联。

再次达到阈值时仍可安全清理新增的旧工具正文；如果仍需第二次摘要，首版返回 `summary_limit_reached` 并停止。首版不递归总结旧摘要。

## 观测和验证

事件记录 `mode/trigger/input_tokens_before/after/changed_tool_call_ids/covered_entry_ids/archive_id/summary_request_id/skip_or_failure_code`。区分准备、模型真实发送、摘要校验、替换完成。摘要请求使用现有 model span；完整源历史可由 read_artifact 读回。事件不复制整包正文，不额外调用模型。

最小离线验收：

1. 默认 off 的实际 HTTP 请求与基线一致，不产生 summary 调用；STATE 仍关闭。
2. 未到阈值不处理；先清理即可放下时不调用摘要。
3. MockTransport 捕获摘要真实请求，证明近期原文、当前信封不在摘要输入；下一探索请求恰好一份摘要和完整保留区。
4. 工具调用与结果在摘要前后都成对；未完成调用不被移动，不能重放已执行工具。
5. 归档分页/长单行可读，归档截断、hash 损坏、容量不足均不替换历史；秘密脱敏。
6. 默认 schema 缺字段/字段类型错误、摘要超限、provider 错误或取消不提交替换；全部实际请求成本照记。验证实际请求使用 SDK 默认五字段 schema，展示使用默认模板。
7. 原有 EvidenceLedger 引用仍可校验；摘要/历史读回不能签发新源码引用。fixed 执行与参考补丁不能进入摘要材料。
8. 摘要消耗一步、发布预留受保护、第二次摘要被明确拒绝；Controller/Verifier 完成闸门不变。
9. 跨 SDK 2.0.9 消息边界切分与 usage 状态适配测试通过；框架升级时不依赖静默降级。

本轮先做离线功能验收，不使用 Astra、不调用付费模型。之后要验证收益，先找稳定触发长上下文的真实案例，再分别对照 off/clear、clear/summary；报告差分成功率、证据完整性、总 Token 与耗时，不能用“调用了 read_artifact”宣称有效。

## 拟改动边界

新增一层薄的历史压缩适配，不新增产品摘要 schema、提示词文件或展示模板；直接读取 SDK 默认配置，仅补历史范围压缩指令。小范围接入 `core/models.py`、`app.py`、AgentScope factory/explorer/runtime/middleware/model_factory/gateway。需要给 SnapshotResponseMiddleware 的现有卸载记录补程序关联，避免解析任意结果正文作为可信回执。

沿用 MaterialStore 和 read_artifact，不新增数据库、工具或第三方依赖；不改源码证据签发规则、探索任务的验收标准、执行器、Verifier、经验写入流程和当前 STATE 默认值。


## 已记录的实现取舍

- SDK `generate_structured_output` 直接调用 `_call_api`，绕过本项目 `_GuardedModel.__call__` 的步骤/费用路径。因此仅复用 SDK 默认 schema、prompt、template，JSON 请求仍通过既有 AgentScopeModelGateway；不照搬 SDK 的结构化请求重试阶梯。
- 归档元数据提供覆盖范围追溯；默认 schema 没有逐项引用，不宣称逐句事实已自动验证。cutoff_contract_version 无可靠历史信封时为 null，不伪造当前版本。
- 历史中的当前阶段信封、最近候选组及其后交互、最近两个 assistant/工具组保持原文。工具结果支持 str 和 TextBlock 列表；混合媒体不清理。
- SDK Read 缓存仍由 SnapshotBackend 的新 stat_mtime 强制重新校验冻结内容；没有放宽缓存读权限。摘要 usage 追加到保留的尾消息，产品 model.attempt 仍是 HTTP 费用的唯一记账点。
- 临时提示加入后的最终输入也检查输出预留；若此时仍超限会明确停止，而不是在同一逻辑调用中再次摘要。
