# Failure Artifact Hints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This document authorizes planning only; do not start implementation, paid runs, commits, pushes or merges from this handoff.

**Goal:** 验证一次有依据的失败材料提示，能否帮助探索 Agent 修正候选；最终看差分成功、证据完整性和总成本，不把读取工具次数当成功。

**Architecture:** 首版只处理“当前失败判定中有非空诊断正文未进入有界反馈”的确定性场景。Controller 从已保存的原版 Verdict 生成按原值提取的诊断材料；AgentScope 在下一探索阶段的首次模型请求中，至多增加一条短 user 数据提示。材料仍通过现有 MaterialStore/read_artifact 读取，提示默认关闭，不改变验收权限。

**Tech Stack:** Python >=3.11、AgentScope 2.0.9、现有 dataclasses/pytest/httpx.MockTransport、MaterialStore、read_artifact、OpenTelemetry facade；不增加依赖和模型调用。

**Spec:** 本文第 1–4 节是待审阅的首版设计约束，来源于本次对话已经讨论的“失败决策点提示”方案。它不是整个上下文管理系统的实施授权。

**Status:** 计划草案。当前只写文档，尚未执行任务。

## 1. 代码基线与范围

源码核对基线是 `C:/Users/Dell/.codex/worktrees/task-state-context/ReproAgent`，分支 `codex/task-state-context`，HEAD `ccd181d9476360ac378b2745f3fcb326245e7483` 加现有未提交修复，包括 STATE 默认关闭的改动。HEAD 本身不能代表待实现基线，执行前必须冻结实际 diff 和相关未跟踪文件哈希。

本计划存放于 `E:/ReproAgent/docs/superpowers/plans/`，便于用户查阅。E 盘主工作区当前没有 `src/reproagent/materials.py`，且存在其他任务的未提交修改；文档存放位置不代表可以直接在该主工作区实施。执行前复用原隔离工作区，或在已集成这些前置修改的用户指定基线上建立隔离工作区，不能直接 reset、覆盖或合并主工作区。

已确认的接入点：

- `core/controller.py::candidate_feedback`：已保存 Verdict 的有界反馈，使用 `render_verification_feedback`，随后进入下一阶段的 `AgentContext.feedback`。
- `core/verification_feedback.py::render_verification_feedback`：反馈上限 `min(8192, tool_response_bytes)`；保留主诊断与绑定，显式统计省略。
- `task_progress.py`：原版执行和 Verdict 已登记材料，fixed 执行不会供探索加载。
- `adapters/agentscope/runtime.py::explore`：同一任务保持一个 SDK Agent，按阶段接收反馈。
- `adapters/agentscope/middleware.py::on_model_call`：实际模型调用前的输入组装；已有预算、权限和发布预留规则。
- `materials.py::capture/read`：脱敏、配额、哈希和分页入口；材料 ID 不接受任意路径。

**重要诊断：** `projection.omitted_details > 0` 不是触发条件。现有渲染器删除空字段、缩短指令也会增加该计数。必须比较脱敏后的完整诊断值与实际反馈，确认非空内容被摘录或移除。

首版明确不实现：

- 不按模块名或关键词推测 Read/Grep/Glob 材料与失败的语义相关性。
- 不增加“重复失败就推历史”的第二套触发器，不修改经验检索。
- 不增加每轮目录、STATE 注入、自动摘要、transcript 归档、历史遮罩或 ArtifactStore 重命名。
- 不尝试从自由文本推断根因，也不声称本版能够提示所有被省略的堆栈。执行日志的可靠定位留作独立后续设计。

## 2. Global Constraints

1. `TaskRequest.failure_artifact_hints: bool = False`，旧配置缺字段等价 false；直接构造和 JSON 解码均拒绝非 bool。现有 `inject_task_state` 保持独立且默认 false。
2. 只使用当前候选、当前契约版本、已保存且哈希正确的 Verdict；全部关联 run 必须是 original。fixed、金标补丁及参考测试不得进入材料、提示或匹配素材。
3. 只在失败判定准备继续探索时选择提示；`decision=stop`、成功判定、没有合法 Verdict 的 provider/协议错误不触发。
4. 第一版仅一个受控原因：`FAILURE_FEEDBACK_OMITTED`。省略计数、单独省略引用、删除空字段、变更 instruction、缺少模型能力均不能单独触发。
5. 每个失败阶段最多选择一个字段、暂存一条提示；每任务最多暂存 3 条。对同一契约、同一候选内容、同一字段原值去重，不因候选 ID 或 run ID 变化重新提醒。
6. 提示正文最多 1024 UTF-8 字节，JSON 整体编码后核算；装不下时跳过，不截断 ID、读取参数或 JSON。提示是 user 数据，不能成为 system 规则。
7. 提示只用于下一探索阶段的首次逻辑模型尝试，不写进 SDK 持久历史；协议纠错产生的新逻辑调用不重复提示。同一逻辑调用的 HTTP 重试可重发同一请求，费用全部计入。
8. 原始反馈、Issue、契约、预算提醒和证据优先。可选提示不允许制造新的 NEEDS_INFORMATION：空间不足就撤掉提示；原输入本来不满足边界时仍按原规则停止。
9. 提示不占探索步骤、不调用模型。实际 read_artifact 仍占现有步骤/时间并接受原有权限检查，不添加免费读取额度，也不强制读取。
10. 提示的额度按“暂存”保守计数；暂存、加入请求、HTTP 发送、读取成功分别记账，不把它们互称“已阅读”。诊断日志失败不更改任务结果。
11. 所有文件访问继续走 `safe_child` 和 `paths.workspace_path()`；写入先脱敏。不得为提示放开冻结源码 Read 的路径范围。
12. 不使用 Astra 验收；无本轮付费模型运行授权。后续实验需明确模型、费用/Token、时长和调用上限后再运行。
13. pytest basetemp 必须位于所有相关 Git 仓库之外，例如 `$env:TEMP/reproagent-artifact-hints-<run>`；不能放进 E:/ReproAgent/repro-results。

## 3. 确定性提示与材料格式

### 3.1 省略检测

对已保存 Verdict 与 `candidate_feedback` 的实际 JSON 反馈分别脱敏后比较，按照以下优先级选第一个确有非空内容损失的字段：

1. `/reason`。
2. 按原 Verdict 的 issue 顺序，检查 `/issues/i/message`，随后按 measurement key 排序检查 `/issues/i/measurements/key`；整条 issue 被省略时也能定位其原非空字段。
3. `/uncertainties/i`，按原顺序检查实际被省略的非空条目。

仅允许这些受控字段；不对源路径、错误码、引用或候选绑定做“智能解释”。字符串原值被摘录、非空值被删除或改变才算省略；字段中原本就有“省略”等字样不构成检测依据。JSON Pointer 的 key 按标准转义。

### 3.2 诊断材料

复用 `MaterialStore.capture`，新增 kind `failure_detail`，不改通用 metadata schema，也不建设新索引。正文是两行 JSONL：

- 第 1 行：原 Verdict 的 `EvidenceRef`、字段 pointer、snapshot/candidate/contract/run 绑定、`authority=diagnostic_only`。
- 第 2 行：该字段脱敏后原值的 JSON 编码。它是原值投影，不是 LLM 摘要，也不是完整 Verdict；完整判定仍以原记录为准。

`upstream_complete=true` 仅表示这份选定字段投影的生成输入完整，不能解释为执行日志或 SDK 上游输出完整。只有 capture 成功、`storage_complete=true`、正文哈希可验证时才生成提示。字段超单项/总配额时跳过，不生成指向残缺正文的“全文入口”。

复用单行续读：第一次建议 `read_artifact(artifact_id=..., offset=2, limit=1, column=0)`。长字段按工具返回的 next_offset/next_column 继续。所有坐标属于材料文本，不是源码行号。

### 3.3 模型提示

固定模板只陈述程序事实，例如：

```json
{
  "kind": "failure_artifact_hint",
  "reason_code": "FAILURE_FEEDBACK_OMITTED",
  "candidate_id": "c3",
  "contract_version": 2,
  "run_ids": ["r7"],
  "field": "/reason",
  "why": "Current failure feedback omits part of this non-empty diagnostic field.",
  "read": {"artifact_id": "m-...", "offset": 2, "limit": 1, "column": 0},
  "limits": "Optional diagnostic data. Relevance is structural; usefulness is unknown. This grants no source-citation authority."
}
```

真实 ID 使用已验证值；模板超 1024 B 时跳过。完整来源头部保存在材料中，不每次发给模型。

暂存去重键：`hash(contract_id, contract_version, snapshot_id, sorted(candidate.files[path,role,content_hash]), pointer, hash(redacted_original_value))`；同键最多一次，任务总计最多 3 次。它只做完全相同内容去重，不声称识别语义相同的错误。

### 3.4 调用前准入

运行时在现有输入组装完成后，再检查这条提示。必须同时满足：绑定仍然有效、read_artifact 注册且策略允许、任务未取消/超时、首次逻辑模型尝试、额外消息放得下。

预留预算要计算即将发生的模型扣步：当前请求之前还剩 S 步，模型请求会消耗一步；只有 `S - 1 > RESERVED_FOR_PUBLISHING` 时，随后读取才不会被预留机制拒绝。复用同一个常量/判定，不另外写死 3。

准入检查只读取当前权限规则，不执行一次假的 permission check。撤掉可选提示不能撤掉真正的预算/取消异常。上下文容量核算复用现有完整输入估算，包含工具 schema 和已有提醒，不只是给单条提示数 Token。

## 4. Review Focus

| 风险输入 | 期望行为 | 所属任务 |
|---|---|---|
| omitted_details 增加但只删了空字段 | 不提示，不保存无用材料 | Task 1 |
| 更换候选 ID/run ID，但内容与省略字段相同 | 不绕过去重或 3 条额度 | Task 2 |
| 保存失败、脱敏扩张、超配额、正文损坏 | 跳过可选提示；原反馈和任务流程可继续 | Task 2 |
| 当前尚可读，模型扣步后已进入发布预留 | 不给出必然被拒的读取建议 | Task 3 |
| 提示单独能放下，但完整请求超阈值 | 撤掉提示，不制造额外停止或孤儿工具结果 | Task 3 |

## 5. 文件职责与接口

以下路径均相对于最终冻结的产品基线；标“新增”的文件目前不存在。

| 文件 | 操作与职责 |
|---|---|
| `src/reproagent/artifact_hints.py` | 新增：省略检测、绑定验证、原值材料生成、任务级去重/额度、1024 B 模板 |
| `src/reproagent/core/models.py` | 修改：TaskRequest 新开关；AgentContext 增加 `artifact_hint: dict | None = None`，仅承载程序数据 |
| `src/reproagent/core/controller.py` | 修改：每任务创建 policy；在现有失败反馈返回后暂存提示，传给下一次 AgentContext；契约变化清除待发送提示 |
| `src/reproagent/adapters/agentscope/runtime.py` | 修改：阶段开始时将 context.artifact_hint 暂存到 middleware，初始输入不混入提示 |
| `src/reproagent/adapters/agentscope/middleware.py` | 修改：首次逻辑调用进行可选消息准入；只在本次 messages 副本中追加 user 消息 |
| `src/reproagent/adapters/agentscope/model_factory.py` | 修改：从最终 HTTP payload 独立采集短提示正文，避免被整包请求截断掩盖 |
| `tests/unit/test_artifact_hints.py` | 新增：检测、绑定、失败回退、额度与兼容性 |
| `tests/integration/test_failure_artifact_hints.py` | 新增：真实 SDK + MockTransport 请求、读取回传、容量/预算边界 |
| `evals/failure_artifact_hints.py` | 新增：先离线机会检查/dry-run，后独立授权的 P0/P1 串行实验 |
| `tests/unit/test_failure_artifact_hint_eval.py` | 新增：实验配置、调度、指标和分母的离线测试 |
| `docs/agentscope.md`, `docs/observability.md` | 修改：开关、提示含义、观测与限制 |
| `docs/reviews/2026-10-18-failure-artifact-hints-acceptance.md` | 实施后新增：真实 RED/GREEN、完整回归、限制和实验状态 |

不修改 MaterialStore/read_artifact 的 API、EvidenceLedger/Verifier 的验收逻辑、发布预留规则、工具集或 trace viewer 布局。现有 verification_feedback 渲染结果先保持原样，旁路比较其实际输出。

拟新增接口，实施者不要自行改成另一套名字：

- `OmittedDetail(pointer: str, value: object)`：脱敏后的原字段值。
- `find_diagnostic_omission(verdict, feedback: dict, *, secrets: tuple[str, ...]) -> OmittedDetail | None`：纯函数，仅受控字段比较。
- `FailureHintPolicy(store, *, enabled: bool, secrets: tuple[str, ...])`：每任务一实例。
- `FailureHintPolicy.stage(verdict, feedback_text: str, *, contract, project) -> dict | None`：验证原记录/绑定、生成材料和模型提示；只处理 continue 反馈，不修改反馈。
- `FailureHintPolicy.clear_pending() -> None`：清除待发送项，不清空已用额度/去重集合。
- middleware 内 `_failure_hint_messages(agent, messages, tools) -> list`：async，进行一次性准入并返回原列表或带提示的副本。
- `summarize_experiment(run_records: list[dict]) -> dict`：离线指标；逐运行保留失败和原始分母。

## Task 1: 配置兼容与真实省略检测

**Files:** core/models.py；新增 artifact_hints.py、tests/unit/test_artifact_hints.py。

**Consumes:** 现有 Verdict、canonical serialization、真实 feedback JSON。
**Produces:** 新开关、AgentContext 可选字段、OmittedDetail、find_diagnostic_omission。

- [ ] 写失败测试：旧 TaskRequest JSON 缺字段为 false，true 可保存，字符串/数字/null 被拒；旧 AgentContext 无提示仍可构造。
- [ ] 写失败测试：reason/message 被摘录、非空 measurements 被删除、issue/uncertainty 被省略，返回确定 pointer 和原值；按 §3.1 顺序选择。
- [ ] 写 Review Focus 测试：仅删除空字段、仅 omitted_refs>0、仅变更 instruction、完整反馈、伪造“省略”字样均不触发；包含引号/换行的脱敏值仍可比较。
- [ ] 运行 `python -m pytest tests/unit/test_artifact_hints.py -q --basetemp <external-temp>`，记录具体 RED 断言。
- [ ] 实现配置验证与纯函数；不修改旧 renderer 的裁判信息或容量行为。
- [ ] 重跑新测试与 `tests/unit/test_verification_feedback.py`、`tests/unit/test_controller_verification_feedback.py`，要求 exit 0，保存 RED/GREEN 日志并核对 diff。

## Task 2: 绑定可靠的材料与任务级额度

**Files:** artifact_hints.py；tests/unit/test_artifact_hints.py。

**Consumes:** Task 1 的 OmittedDetail；原版候选/运行/Verdict 记录、MaterialStore.capture/read。
**Produces:** FailureHintPolicy.stage/clear_pending，返回符合 §3.3 的 dict 或 None。

- [ ] 写失败测试：当前失败的原值可从材料第 2 行读回；长单行可按 next_column 无丢字续读；header 的原记录哈希可核对。
- [ ] 写失败测试：旧契约/不同候选/未知 run/fixed run/损坏原记录/stop 或成功反馈不生成提示，且不得把 fixed 内容写入材料。
- [ ] 写 Review Focus 测试：相同内容换 ID 不重复；不同真实省略最多 3 条；契约变化不重置总额度。捕获异常、超配额、storage_complete=false、提示超 1024 B、脱敏扩张均返回 None，不篡改原反馈。
- [ ] 运行本组测试观察 RED；实现 §3.2 原值投影、§3.3 去重和模板，不增加通用索引或重写 MaterialStore。
- [ ] 重跑 `tests/unit/test_artifact_hints.py`、`tests/unit/test_materials.py`、`tests/unit/test_task_progress.py`，要求 exit 0，记录结果并核对本任务 diff。

## Task 3: 失败后的一次性实际请求提示

**Files:** controller.py、runtime.py、middleware.py；新增 tests/integration/test_failure_artifact_hints.py。

**Consumes:** Task 2 的 policy 输出；Task 1 的 AgentContext.artifact_hint。
**Produces:** 首次逻辑调用可选 user 消息，后续逻辑调用不重复；实际 read_artifact 沿用现有工具。

- [ ] 用真实 AgentScope Agent + MockTransport 写失败测试：第一候选执行后判定反馈确有省略，下一请求才有提示；初始探索、开关 false、无真实省略均无提示。
- [ ] 写失败测试：默认关闭路径的模型消息与对照一致；显式开启不依赖 STATE。固定测试时钟后比较实际 payload，不用 system prompt 有一句话代替 wire 验证。
- [ ] 写 Review Focus 测试：S=R+1 时跳过、S=R+2 时可提示；其中 R 来自现有 RESERVED_FOR_PUBLISHING。取消/超时/终态仍保持原优先级。
- [ ] 写容量测试：提示本体合规但完整输入不合规时撤掉它；相同原输入仍能发送，无新增 NEEDS_INFORMATION；原输入本已不合规时照旧停止。
- [ ] 写配对/历史测试：同阶段第二逻辑请求无新提示，SDK 历史无提示消息；HTTP 重试不假计多次决策；工具调用/结果成对，read_artifact 结果进入后续请求。
- [ ] 写隔离测试：材料不能增加 EvidenceLedger 引用，fixed 字符串不出现在任何探索输入；提示“可选”不成为强制发布门槛。
- [ ] 先运行新集成测试记录 RED，再接入 Controller/policy/AgentContext 和可选输入准入；原反馈不重复编码、不为提示缩短。
- [ ] 重跑新测试及 `test_task_state_delivery.py`、`test_assertion_discrimination_feedback.py`、`test_agentscope_runtime.py`、`test_agentscope_observability.py`，要求 exit 0。

## Task 4: 可解释的观测与离线机会检查

**Files:** artifact_hints.py、middleware.py、model_factory.py、tests/integration/test_agentscope_observability.py；新增 evals/failure_artifact_hints.py、tests/unit/test_failure_artifact_hint_eval.py；两份使用文档。

**Consumes:** Task 3 的真实请求与已有 model/tool 事件。
**Produces:** 不改变任务结论的提示事件，以及在付费前判断是否有可测机会的清单。

- [ ] 写失败测试：分别记录 selected、prepared、skipped；HTTP hook 的真实 wire/内容引用用于确认实际发送，prepared 不冒充 delivered。
- [ ] 按现有 `_capture_wire_task_state` 模式新增 `_capture_wire_failure_hint(payload)`，只从 exploration 的实际 user 消息捕获严格匹配 `kind=failure_artifact_hint` 的单条文本，source 为 `wire_failure_artifact_hint`；先于整包 wire_request 采集，保留实际文本字节/哈希而非重新编码后的近似文本。
- [ ] 在 `tests/integration/test_agentscope_observability.py` 验证：整包请求因 32 KiB 上限截断时短提示仍可独立核对；未提示时不伪造副本；重复或过大提示记 unsupported；总量/槽位不足明确 omitted_limit。采集异常不修改请求、不重发、不改变任务结果。
- [ ] 受控 skip 原因固定为 disabled、no_omission、binding_mismatch、not_original、duplicate、quota、material_unavailable、hint_capacity、reader_reserved、context_capacity；不要统一折叠成 invalid。
- [ ] 事件只记受控原因、候选/契约/run/material ID、pointer、字节数/哈希；不重复写正文或敏感数据，不把这些 ID 加到 Metrics 标签。
- [ ] 写失败测试：日志/trace 写入异常不逃逸，原工具结果和主任务结论不变。
- [ ] 写 dry-run：读取已有失败材料并按本计划重算省略机会，输出 case、原记录来源、候选/契约/run、真实省略字段、材料可保存性和预期准入；不读取 fixed/gold，不调用模型。
- [ ] 每个入选案例保存可核对的机会清单。找不到真实省略机会时，结论为“该首版规则尚无足够覆盖”，停止付费实验；不制造长诊断来伪装真实收益。
- [ ] 更新文档：给一个完整任务配置的单字段增量 `"failure_artifact_hints": true`；解释材料是字段原值而非完整日志、提示次数不是读取次数。
- [ ] 运行新指标单测与对应 trace/反馈回归，要求 exit 0。

## Task 5: P0/P1 最小实验与最终验收

**Files:** evals/failure_artifact_hints.py、tests/unit/test_failure_artifact_hint_eval.py；实施后新增 acceptance 文档。

**Consumes:** Task 4 的机会清单；产品 CLI/装配、现有真实案例环境、固定经验快照。
**Produces:** 预注册、运行配置和逐次结果；无授权时仅产出 dry-run 和离线验收。

- [ ] 写调度/指标失败测试：P0/P1 唯一配置差为 failure_artifact_hints；所有失败保留在分母；零成功时单位成功成本为 undefined，不写成 0。
- [ ] 冻结源码、dirty diff/相关新文件哈希、导入路径、模型及 endpoint、thinking/temperature、解释器、rg 路径、经验快照、卸载策略、固定源码版本和预算。STATE 两组都关闭，自学习两组都关闭。
- [ ] 预注册首轮 8 个独立 Bug、每组每例 3 次，共 48 次；含真实省略场景、首次失败后修正场景和无需提示的短案例对照。案例选择依据独立预检，不按正式实验哪组触发提示来筛选运行。
- [ ] 同一案例重复内随机交错 P0/P1，串行执行；沿用相同 20 步和逐案例固定的任务/命令时限。读取照常扣步，不额外赠送读取步骤或增大单组超时。
- [ ] 付费前先输出预计最大运行数、总调用/Token/金额上限、总墙钟上限及凭据环境变量名，获得用户明确授权。达上限停止并保留未运行条目，不用历史单案例授权覆盖本轮。
- [ ] 统计差分成功、原版复现、证据等级、停止原因、全部模型输入/输出/缓存 Token、实测费用或 unknown、耗时、读取消耗步骤、重复同类失败。
- [ ] 按 kind 分开统计 read_artifact；记录选定字段的读回覆盖、后续实际输入送达、后续动作及候选变化，不把工具成功或候选字节变化直接判成理解/改进。
- [ ] 列出双成功、双失败、仅 P0 成功、仅 P1 成功；按 Bug 聚类。8 个独立案例只是筛查，不把 24 次重复当 24 个独立问题，不据此默认开启。
- [ ] 正式默认启用前另行扩大独立案例：正确性无新增破坏；或差分成功率差的 95% 区间下界 > -5 个百分点且成本比区间上界 < 0.85；或成功率点估计增幅 >=10 个百分点、区间下界 >0 且平均成本增幅 <=10%。样本量需按筛查方差估算，区间不充分则保持关闭。
- [ ] 产品完成前运行项目完整 pytest，使用位于所有仓库之外的 basetemp；保留首轮失败并逐项定位，不把复测通过写成“第一次全量通过”。按实际 exit/count 报告。
- [ ] acceptance 文档逐项写配置、提示准入、绑定/证据/预算不变量、真实请求校验、RED/GREEN、完整回归、付费实验是否执行、有效触发率和未覆盖项。

## 6. 通过条件与回滚

- 功能通过：开关 false 无额外提示；true 在真实省略且准入满足时只提示一次；材料可读、来源可核对、配对/证据/预算规则不变。
- 观测通过：selected/prepared/实际 wire/读回四层可区分；容量或权限跳过能看到具体原因。
- 实验可解释：两组确有输入差异和真实触发机会，固定条件一致，失败分母完整；没有机会不能宣称机制无效，也不能宣称有收益。
- 默认策略：即使离线全部通过，也继续 `failure_artifact_hints=false`；实验结果混合或区间过宽时维持关闭。
- 回滚：关闭任务配置开关即可撤掉提示；已保存诊断材料仍是只读补充，不影响已有候选、Verdict、报告和交付包。无需回写旧任务。

## 7. 后续独立选题

本轮不实施以下项目：当前 run 执行日志的可靠行锚点；Read/Grep/Glob 与当前候选的结构化关联；条件提示与每轮提示的对比；工具观察遮罩；LLM 摘要及 transcript 统一外置。每一项都需要独立规则、输入核查、实验开关和收益判定，不与本轮 P0/P1 混在一起归因。

## 8. 执行交接

用户本次要求“先写计划文档”，尚未要求执行本计划。建议后续继续在同一会话按任务顺序实施，因为配置、Controller 和 middleware 接口关联紧密；先审阅本计划的首版范围与接口，再开始实施。

计划编写时只核对源码与文档；没有修改产品代码，没有运行真实模型，没有提交、push 或合并。
