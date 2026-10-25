# 经验草稿显式决定与兜底设计

日期：2026-10-25
状态：方向已确认，待用户审阅；尚未实现。
基础设计：[轻量踩坑草稿、审查与按需读取](2026-10-25-experience-draft-review-design.md)

## 1. 目标

解决真实运行中两个不同的问题：

1. 有可复用踩坑，但探索 agent 完全不写草稿；
2. 没有踩坑，或模型判断不值得写，却和“漏写”表现成同一个 `skipped_no_draft`。

同时明确：候选被 Verifier 打回，不等于草稿无效；草稿仍可审查，但不能把未验证的恢复写成成功。

## 2. 已观察到的证据

- 5227（source 修复后）：4 次 `write_candidate`，0 次带草稿；
- 8906：14 次 `write_candidate`，0 次带草稿；
- 更早的 5227：写过两次草稿，但 `source_ids` 是文件路径/内容哈希，审查未执行；
- 两次 source 修复后的运行都返回 `skipped_no_draft`，经验库为空。

结论：仅把来源 ID 变得可见还不够；模型仍可能完全跳过可选草稿字段。

## 3. 设计

### 3.1 显式决定，而不是静默可选

仅在 `draft_review` 实验模式中，为 `write_candidate` 增加一个可选的决定字段：

```text
lesson_status: "draft" | "none"
```

组合规则：

| lesson_status | lesson_draft | 行为 |
| --- | --- | --- |
| `draft` | 完整四字段 | 接收草稿 |
| `none` | 缺失或 null | 记录“无草稿”，不算失败 |
| 缺失 | 缺失 | 记录 `omitted`，说明模型没有做出决定 |
| `draft` | 缺失/无效 | 记录 `invalid_decision`，候选仍按原规则发布 |
| `none` | 有草稿 | 记录 `invalid_decision`，候选仍按原规则发布 |

`lesson_status` 和 `lesson_draft` 都不参与候选基础参数校验；坏值、超限、保存失败都不能阻止候选发布。legacy 模式不增加字段，工具 schema 保持原样。

### 3.2 决定点提醒

当上一阶段已经出现可恢复失败（verification feedback `decision=continue`、replay unconfirmed、受控工具错误等），在下一次探索模型调用前加入一条**短暂、非持久**的 system reminder：

```text
上次操作未通过。下一次 write_candidate 必须显式选择：
提供 lesson_draft，或声明 lesson_status="none"。
```

这条提醒：

- 不改变候选发布条件；
- 不新增工具；
- 不持久化到对话历史；
- 每任务最多注入一次；
- 不替代已有 `experience.hint`，后者仍只负责提示查阅历史经验。

### 3.3 任务结束后的兜底总结（可选实验开关）

仅当同时满足：

- `draft_review` 开启；
- 存在受控失败/验收失败；
- 没有合法草稿，且没有明确的 `lesson_status=none`（即 omitted 或 invalid_decision）；
- 配置显式开启兜底（默认关闭）；

使用独立的一次逻辑 learning 请求，预算上限 30 秒，读取与现有学习相同的受控材料，生成一份草稿候选；随后交给现有 review（review 仍有自己的 30 秒独立预算）。兜底：

- 不读取完整聊天；
- 不读取 fixed 代码或参考补丁；
- 不直接写卡；
- 失败时返回受控 code，不改变主任务结果；
- 与 `learn_experience=false`、取消、坏库等规则一致。

### 3.4 候选被打回时的草稿处理

草稿与候选验收解耦：

- 候选被 Verifier 打回，草稿仍保存在 `learning/draft.json`；
- 任务结束后的审查材料必须包含最终任务状态和候选结果；
- 审查可以 accept/revise 一条“框架/流程”教训，但不能声称候选已成功或恢复已验证；
- 只有审查 accept/revise 才写卡；
- 审查 reject 不写卡；
- 同一任务仍只保留最新一份合法草稿。

### 3.5 观测

`experience.draft` 增加受控结果：

```text
received
none
omitted
invalid_decision
invalid_sources
store_error
```

事件只记录 code、字节、来源数量、call_id、arguments_hash；不记录草稿正文或模型自由解释。

## 4. 预算与兼容

- 正常候选调用不增加模型调用次数；
- 决定点提醒只增加少量输入 token；
- 兜底总结是显式实验开关，默认关闭，独立 30 秒预算，不占主任务；
- legacy 模式字段、工具 schema、行为保持不变；
- 不修改 Controller 主结果、Verifier 判定、源码证据资格、fixed 隔离和硬步骤上限。

## 5. 验收

离线：

1. 合法 `draft` 被接收；`none` 被记录为无草稿；缺失记录为 omitted；冲突/坏值记录 invalid_decision，候选仍发布；
2. 坏草稿、来源错误、保存失败不改变候选和主任务；
3. 候选被打回后草稿仍可审查；审查不能把失败候选写成成功；
4. 兜底开关关闭时不发额外请求；开启时最多一次逻辑请求，失败隔离；
5. legacy 行为逐字节兼容。

真实（需单独授权）：

- 5227：至少一次模型显式选择 `draft` 或 `none`；
- 若选择 `draft`，来源必须是可见 `learning_source_id`；
- 有草稿时 review 实际发生且最多一次；
- 主任务结果、Token、费用、经验链路分开报告；
- 单次运行不能证明复现率或经验收益。

## 6. 非目标

- 不强制每个候选都写草稿；
- 不允许模型自己写 id/triggers/index；
- 不自动把候选成功当成草稿归因正确；
- 不用兜底总结替代 review；
- 不改变验收标准或放宽来源引用。
