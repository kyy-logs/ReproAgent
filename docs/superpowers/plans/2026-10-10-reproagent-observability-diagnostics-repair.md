# ReproAgent Observability Diagnostics Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in the current session. Steps use checkbox (`- [ ]`) syntax for tracking. 沿用用户已选择的当前会话逐项执行；不默认派发子代理。验收不得使用 Astra。

**Goal:** 保持 thinking 关闭，按“明确缺失原因 → 串起契约、拒绝、预算与停止的诊断链 → 补足输入正文保留”的顺序修复现有本地观测。

**Architecture:** 复用 AgentScope 2.0.9 原生 TracingMiddleware 与现有 OpenTelemetry provider、processor 和真实父子关系。在产生事实的位置补充受控观察，再由本地投影生成诊断视图；程序诊断只依据明确记录的事件，不让模型重新解释。模型和工具正文仍通过已有 ContentStore 脱敏、去重、限额保留，直接采集 SDK 模型输入以绕过原生属性截断。

**Tech Stack:** Python >=3.11；AgentScope 2.0.9；OpenTelemetry API/SDK 1.45.0；pytest；现有单文件 HTML/CSS/JavaScript；Node 内置测试与隔离浏览器验收。无新运行时依赖或后台服务。

**Spec:** [既有观测架构](../specs/2026-10-09-reproagent-agentscope-observability-migration-design.md)与[交互查看器计划](2026-10-10-reproagent-interactive-trace-viewer.md)中的安全、生命周期、离线和关联约束继续适用；本计划下文“已确认范围与修订”是本次补充需求。用户已选择先修现有观测链并要求先写计划；此文档不是代码实施或验收完成回执。

## 已确认范围与修订

本次解决三个问题：人能知道内容为何没保存；人能读出一次任务的实际业务推进与终止过程；在既有总容量内保留更长的实际请求和 SDK 模型输入。用户要求轻量，选择了先修本地采集；不接 Phoenix、Langfuse、OTLP exporter 或数据库。

1. 缺失必须显示具体原因与来源。接口未返回 reasoning 属于正常情况，不单独导致 partial；实际截断、丢弃、异常必须有定位信息。
2. 诊断按明确事实关联：“当时的契约 → 请求/实际变更 → 权限或业务拒绝 → 预算状态 → 最终停止”。请求修改契约不等于契约已经变更；时间先后不自动等于因果。
3. 保留真实模型输入，不改变请求、提示词、工具、重试或预算策略。采集关闭与开启的业务结果、模型请求数、计费方式必须一致。
4. 本次修订单条正文记录上限至 **256 KiB（262144 字节，含 JSON envelope）**；正文总量仍 **4 MiB**。超限继续保留明确标记的头尾摘录，绝不承诺无限量完整保存。
5. 新增字段采用 schema2 的可选扩展，旧 schema1/schema2 不改写；旧文件缺少精确原因、预算维度或契约差异时显示“历史记录未提供”，不追补事实。

## Global Constraints

- 正常 run 默认采集、自动产生 `task-output/observability/trace.json` 与 `trace.html`；命令不增加参数或额外步骤；`--no-trace` 完全关闭采集。
- 不主动开启 thinking、不提高模型输出预算、不追问、不新增模型调用/token；仅保留接口实际返回的公开文本。
- AgentScope 原生创建 Agent、模型、工具 span；OTel 继续生成 ID/parent。point 的展示 ID 不能冒充 span ID，跨调用关联不能改写父子关系。
- 权限三态固定为 `ALLOWED` / `DENIED` / `RESERVED_FOR_PUBLISHING`。权限点不算工具执行；一次逻辑探索模型调用消耗一步，HTTP 重试、工具拒绝与工具执行不单独加一步。
- 原样传递模型、工具返回和原有异常；观测失败不重跑 handler、不改变 task/result/verdict、候选、源代码保护或交付包。
- 保留实际注册工具集：基础 6 个，启用经验时可增加 `read_experience`；从 runtime 的 allowed_tools 读取，不硬编码“六/七”。
- 正文脱敏发生在副本上；不记录 headers、环境变量、URL 查询串；二进制只标类型。trace 不作为证据、不回灌模型、Verifier 或学习。
- 512 个正文条目、正文总量 4 MiB、span/point 合计 1024、单条元数据 2048 字节、元数据总量 1 MiB、JSON 写入/读取 6 MiB、HTML 8 MiB。所有限额包括其序列化开销。
- OTel 原生限额继续为 64 attributes / 32 events / 0 links / 单属性 65536 字符；本次绕过正文对属性的依赖，不放宽全局 provider 限额。
- 使用既有 `_trace_destination()`、`paths.workspace_path()`、原子写、Windows 长路径与 junction/symlink 校验；不增加正文 sidecar 文件。
- HTML 单文件离线、正文只存一次、安全文本 DOM、固定脚本 hash CSP、零外部请求、无 JS 静态回退继续成立。
- 不改业务预算值、探索策略、提示词、契约/引用验收规则、Verifier 判定、经验系统或 benchmark；不默认 commit/push/merge。

## Review Focus

1. thinking 关闭、接口没有 reasoning：应显示正常缺失，正文和 token 统计不因此被误报不完整（Task 1）。
2. SDK 原生属性恰好 65536 字符与超出、中文及大量 JSON 转义：不能猜测原始长度或把残缺 JSON 当完整正文（Tasks 1、5）。
3. 权限允许但发布失败、revision 请求被拒或实际修订成功：应区分业务拒绝和 OTel 状态，并只在真正保存新契约后显示版本变化（Tasks 2、3、4）。
4. HTTP 重试、重复工具 ID、跨 phase、预算终止及迟到 span：不得重复计步、错误关联或漏掉最终停止摘要（Tasks 3、4、6）。
5. 采集故障/限额、旧文件、恶意正文、Windows 长路径：应明确降级且不影响复现结果、离线重建或执行安全（Tasks 1、4、5、6）。

## 当前基线与修改地图

计划编写时 main 为 `15338f5`，工作区干净。以下是读代码得出的基线，不是新修复的测试结果：

- `observability_content.py:23/146/201`：单条 32768 字节；所有 missing state 都会将 complete 置 false；总容量失败只保留笼统 omissions。
- `otel_backend.py:69` / `otel_projection.py:221`：原生属性受 65536 字符限制，投影将达到边界的 SDK 消息记 omitted_limit，未记录具体原因。
- `otel_projection.py:116`：一个 `_partial` 同时控制整体与 metrics_complete；正文缺失会污染计数完整性。
- `_GuardedModel` 位于 `adapters/agentscope/model_factory.py`；不存在 `_BoundedModel`。预算探索步由 `ExplorationMiddleware.before_model_call()` 消耗，HTTP 尝试由 `_GuardedModel._attempt()` 记账。
- Controller 已把 action/phase 记录到 `events.jsonl`，但这些事实并未全部进入 trace。不能通过给 TaskStore 所有事件加 trace 来扩大权限或复制无关正文。
- `write_trace()` 当前在 JSON 超 6 MiB 时只标记后继续写；需修复为拒绝超限写入，沿用 CLI 的固定观测失败警告，不影响任务。

| 文件 | 本次职责 |
| --- | --- |
| Modify `src/reproagent/observability_content.py` | 具体原因、未知长度、正文完整性、256 KiB 记录上限 |
| Modify `src/reproagent/otel_projection.py` | 有界缺失摘要、独立完整性、原生截断原因、直接 SDK 采集去重 |
| Modify `src/reproagent/observability.py` | 受控字段、capture 可选元数据、finish 兼容接口、JSON 写入限额 |
| Create `src/reproagent/observability_decisions.py` | 安全的业务观察副本：契约快照/差异、预算快照、拒绝元数据 |
| Modify `src/reproagent/core/controller.py` | 初始/实际修订契约、业务步骤结果、任务终止观察 |
| Modify `src/reproagent/core/candidate_service.py` | 在实际发布拒绝分支记录精确程序原因 |
| Modify `src/reproagent/core/budget.py` | BudgetStopped 可选耗尽维度；原预算行为保持 |
| Modify `src/reproagent/adapters/agentscope/middleware.py` | 计步观察、三态权限与契约/预算关联 |
| Modify `src/reproagent/adapters/agentscope/tools.py` | 真实引用校验分支的拒绝原因，不解析错误文本 |
| Modify `src/reproagent/adapters/agentscope/runtime.py` | phase 开始/完成观察与当时契约 |
| Modify `src/reproagent/adapters/agentscope/observability.py` | 公共 hook 直接复制 SDK 模型输入，不依赖原生属性正文 |
| Modify `src/reproagent/cli.py` | finish 传实际 stop_reason 与最终预算快照，仅启用采集时构造 |
| Modify `src/reproagent/trace_view_model.py` / `trace_rendering.py` / `resources/trace_viewer.mjs` / `resources/trace.html.template` | 缺失摘要、业务诊断链、静态与交互视图 |
| Modify `docs/observability.md`; Create `docs/reviews/2026-10-10-observability-diagnostics-repair-acceptance.md` | 更新语义、容量和验收证据 |

不改 `otel_backend.py` 的 provider 结构与 SDK/OTel 依赖版本，不改 TaskStore 事件格式、核心记录序列化、模型配置/调用参数、EvidenceLedger、Workspace 保护和导出包结构。测试文件随以下任务列出。

## 数据与接口约定

### 缺失原因与完整性

保留 availability 原词汇；可选扩展到 record：`reason_code: str | None`、`limit_scope: str | None`、`limit_value: int | None`。`original_bytes` 改允许 null：已取得时是脱敏后、截断前 UTF-8 字节数；无法取得时未知。`captured_bytes` 仍为实际正文 UTF-8 字节数，缺失为 0。

| reason_code | 触发位置/语义 |
| --- | --- |
| `PROVIDER_NOT_RETURNED` | 接口未提供公开文本，正常提示；不凭此断言 thinking 配置 |
| `CAPTURE_DISABLED` | 明确关闭某项采集，正常提示 |
| `UNSUPPORTED_CONTENT` | 类型无法采集；解释为限制，不能假装完整文本 |
| `STREAM_INCOMPLETE` | 没有最终流结果 |
| `RECORD_BYTE_LIMIT` | ContentStore 单条记录被裁剪 |
| `CONTENT_ITEM_LIMIT` / `CONTENT_TOTAL_BYTE_LIMIT` | ContentStore 无法保留更多记录 |
| `OTEL_ATTRIBUTE_POSSIBLY_TRUNCATED` | 属性字符数达到限额，疑似被截断；原始长度未知，不能断言一定截断 |
| `SPAN_LIMIT` / `METADATA_LIMIT` / `OTEL_DROPPED_FIELDS` / `SPAN_UNFINISHED` / `PARENT_MISSING` | 结构或受控元数据损失 |
| `CAPTURE_EXCEPTION` / `OBSERVATION_EXCEPTION` | 采集/观察自身失败；不混作业务失败 |
| `LEGACY_UNSPECIFIED` | 旧文件仅有笼统缺失/partial，精确原因未知 |

新增顶层 `capture_health`：`structure_complete: bool`、`counts_complete: bool`、`content_complete: bool`、`issues: list[dict]`、`issues_dropped: int`。issue 包含 `code/severity/span_id/source/content_id/limit_scope/limit_value/original_bytes/captured_bytes/count`，未知值为 null；按同一原因、span、source、content_id 聚合，最多 128 条，超过后增加 issues_dropped 并保留固定警告。缺失的 record 未能入库时 issue 仍可定位 span/source；不得创建悬空 content_refs。

- `partial` 表示实际观察损失；`PROVIDER_NOT_RETURNED` 与 `CAPTURE_DISABLED` 为 info，单独存在不设 partial。其他原因按发生的维度降级，error/warning 必须有对应 issue 或溢出计数。
- `metrics_complete` 保留字段，但映射到 counts_complete（统计所需 span/属性是否完整）。正文截断不能使它 false。
- `content_complete` 映射到支持的正文保留情况；unsupported/incomplete/裁剪/省略/采集异常使它 false；正常未返回/明确关闭不冒充采集故障。
- `summary.usage.complete`、`summary.cost.complete` 独立。HTTP 尝试或其用量字段丢失时 usage 为已知小计；未配置价格时 cost 未知，不能因此声称 token 不完整。结构/字段丢失影响范围无法确定时保守降级相关统计。
- legacy 沿用原字段值、不重新声称完整；只对已经记录的 truncated/availability 提示有限原因，不能从旧标志推断具体耗尽限额。

`ContentStore.capture(..., reason_code=None, limit_scope=None, limit_value=None, original_bytes=None) -> str | None`、`SpanHandle.capture()`、模块级 `capture()` 与 sink `_capture()`同步扩展关键词参数，已有调用不变。新增 `ContentStore.issues() -> list[dict]` 和 `TaskTraceSink.note_capture_issue(...) -> None`，捕获异常不能返回笼统 None 后被投影误标成容量问题。

### 业务观察

新增 `observability_decisions.py` 的准确接口：

- `budget_snapshot(budget: Budget) -> dict`：只读取；含 `steps_used/steps_limit/steps_remaining/seconds_remaining/cost_spent/cost_limit/unknown_cost_calls`；Decimal 金额转十进制字符串，未配置 limit 为 null；调用者先检查 tracing_enabled。
- `contract_snapshot(contract: IssueContract) -> dict`：只复制已有 dataclass 字段。
- `contract_diff(before: IssueContract, after: IssueContract) -> dict`：仅比较 `trigger/expected/reported_actual/observable_checks/sources/assumptions/missing_information`，返回 changed_fields 和每个变更字段的 before/after；不调用模型。
- `observe_decision(name: str, *, attributes: dict, content: dict | None = None) -> None`：tracing 关闭立即返回；开启时调用现有 mark/capture，content source 为 `program_artifact`；安全失败不逃逸。

Step 保存自己收到的 context/contract；`Controller.action_end(selected, started, code, *, context=None, contract=None)`增加兼容关键词参数，由 Step 传入。trace 使用结束时的预算快照与实际契约，原 events.jsonl 的 selected/payload 不变。observe_decision 从当前 sink 的真实 explore 祖先获取 phase_id；若 `_explore_scope()`回到 root，记录 null，不把根当探索 phase。

扩展 ALLOWED_ATTRIBUTES：`decision_code`、`phase_id`、`step_index`、`previous_contract_version`、`changed_fields`、`budget_dimension`、`source_ref`（受控 path/hash/start/end）、`task_status`。自由文本/契约快照/差异正文放 ContentStore，不塞进 2048 字节属性。`phase_id` 使用实际 enclosing explore span 的 ID；无 phase 时 null，不生成新的业务身份。

| 观察名称 | 唯一写入位置 |
| --- | --- |
| `contract.established` | Controller 初次 save_contract 成功之后 |
| `contract.revised` | Controller 实际保存新版后，包含旧/新版与真正差异 |
| `phase.started` / `phase.completed` | runtime 实际开始/得到 PhaseResult 处；不能把 SDK 自然语言收尾当成功 |
| `budget.step_charged` | middleware.before_model_call 的 take_step 成功之后，每步一次 |
| 既有 `permission` | 保留一个点，补三态 permission_result、契约、step_index、预算；不额外复制权限点 |
| `publication.rejected` | CandidateService.publish 的实际契约拒绝检查分支 |
| `citation.rejected` | ReviseContract._cite 的实际 hash/显示范围拒绝分支 |
| `action.completed` / `action.rejected` | Controller.action_end；映射原受控 code，不复制整个 TaskStore |
| `task.stopped` | Controller 已取得终止结论处，记录明确预算维度；最终摘要另由 CLI 实际 TaskResult兜底 |

拒绝 decision_code 固定包括 `NO_BOUND_CONTRACT`、`STALE_CONTRACT`、`SNAPSHOT_MISMATCH`、`CONTRACT_UNGROUNDED`、`CITATION_HASH_MISMATCH`、`CITATION_NOT_DISPLAYED`，Controller action 使用现有 ACTION_RESULT_CODES。其他工具 ERROR 原样保留工具正文，显示“工具执行返回 ERROR；精确程序原因未记录”，不靠关键词猜 code。

`BudgetStopped(reason: str, message: str = "", *, dimension: str | None = None)` 添加兼容可选字段。在实际 throw 分支明确 `steps/time/cost/cancelled`，原 reason/message、优先级与控制行为不变；外部 BudgetStopped 没有维度时显示 unknown，不通过英文 message 解析。

`TraceRecorder.finish` / `TaskTraceSink.finish`兼容增加 `stop_reason: str | None = None, budget: dict | None = None`；CLI 从最终 TaskResult 和预算提供值。文档顶层增加 `diagnostic_summary = {status, stop_reason, budget, budget_dimension}`；维度只来自与最终停止事实匹配的明确事件，否则 null。span 容量耗尽时最终结果摘要仍存在，绝不将最后一次工具拒绝推测成最终 stop_reason。

## Task 1：明确缺失原因，拆开完整性

**Files:** Modify `observability_content.py`、`otel_projection.py`、`observability.py`；Test `tests/unit/test_observability_content.py`、`test_otel_projection.py`、`test_observability.py`。

**Interfaces:** 产出上文 capture 可选参数、ContentStore.issues、note_capture_issue、capture_health；后续任务按原 capture 返回 ID/None，正文与元数据仍分别受限。

- [x] Step 1：先写失败测试 `test_absent_reasoning_is_informational`：not_returned reasoning → complete=True、原始长度 null、reason=PROVIDER_NOT_RETURNED；`test_body_loss_does_not_invalidate_token_counts`：正文被裁剪而 HTTP 用量已知 → partial=True、content_complete=False、metrics_complete=True、usage.complete=True。
- [x] Step 2：增加参数化测试覆盖每个缺失状态、单条/条目/总字节限额、复制异常、原生恰好/超过 65536 字符、未结束 span、missing parent、元数据与 span cap；断言准确 code/limit/source/span、无悬空引用。采集异常不能被误标 CONTENT_TOTAL_BYTE_LIMIT；issue 达 128 后计数与警告可见。
- [x] Step 3：运行 `.\.venv\Scripts\python.exe -m pytest tests/unit/test_observability_content.py tests/unit/test_otel_projection.py tests/unit/test_observability.py -q`；确认新断言失败于现有误判，不因测试导入或 fixture 失效。
- [x] Step 4：实现明确 reason/issue 与维度完整性；此任务保留 32768 字节上限，Task 5 再调整。未知 original_bytes=null。finish 汇总时跟踪丢弃的 HTTP 统计字段，异常及 writer 拒绝走原观测隔离边界；write_trace 在编码 >6 MiB 时拒绝写，不能留下刚写出的不可读取文件。
- [x] Step 5：同一测试集变绿；新增 writer 边界恰好/超限与 JSON/HTML 一致性测试。核对正常 not_returned 不再误报，真实损失不再出现无原因的 partial。记录结果；如用户授权提交，提交 `fix: explain trace capture loss without corrupting metrics`。

## Task 2：记录真实契约与拒绝事实

**Files:** Create `observability_decisions.py`；Modify `observability.py`、`core/controller.py`、`core/candidate_service.py`、`adapters/agentscope/tools.py`；Test Create `tests/unit/test_observability_decision_events.py`；Modify `tests/unit/test_controller.py`、`tests/integration/test_agentscope_domain_tools.py`。

**Interfaces:** 产出 contract_snapshot/contract_diff/observe_decision；为 Task 4 提供可关联的 contract.established/revised 与精确拒绝点。

- [x] Step 1：写 `test_contract_change_requires_successful_save`：初始 v1、一项修订成功 v2、一次失败修订不新增版本；差异准确命中 expected/missing_information 等实际变更，旧候选仍按原规则失效。`test_revision_request_is_not_a_contract_change`：请求成功但 Controller 尚未修订时不存在 contract.revised。
- [x] Step 2：写 `test_publication_rejection_keeps_actual_contract` 与 `test_citation_rejection_records_the_checked_range`：权限 ALLOWED 后业务拒绝仍为业务拒绝；citation hash 与未显示行范围有不同 code。禁止读额外文件、重复验证引用或用异常文字识别 code。
- [x] Step 3：运行 `.\.venv\Scripts\python.exe -m pytest tests/unit/test_observability_decision_events.py tests/unit/test_controller.py tests/integration/test_agentscope_domain_tools.py -q`，确认 RED。
- [x] Step 4：实现观察辅助函数和字段白名单；在现有判断/保存成功位置加副本观察，不提前改变检查顺序。Controller.action_end 对现有结果加 trace 点，仍保留原 events.jsonl；不要通用拦截 TaskStore。
- [x] Step 5：同一测试集 GREEN；注入 observe_decision/capture 故障，断言拒绝异常与业务结果未变化。可授权提交 `feat: trace contract changes and domain rejections`。

## Task 3：关联权限、真实预算消耗与最终停止

**Files:** Modify `core/budget.py`、`core/controller.py`、`observability_decisions.py`、`adapters/agentscope/middleware.py`、`runtime.py`、`observability.py`、`otel_projection.py`、`cli.py`；Test `tests/unit/test_budget.py`、`test_observability_decision_events.py`、`tests/integration/test_agentscope_observability.py`、`test_otel_observability_lifecycle.py`。

**Interfaces:** 产出 budget_snapshot、BudgetStopped.dimension、上文 finish 扩展与 diagnostic_summary；每个事件明确当时契约/phase/step，permission 与工具沿用 tool_call_key。

- [x] Step 1：写 `test_one_step_three_http_attempts_has_one_charge`：一次逻辑探索调用、三次 HTTP 尝试 → charged 点1、step增1、HTTP计费尝试3。`test_denied_tool_is_not_a_second_step`：权限拒绝不调用工具，也不额外扣步；允许工具及其结果仍只计一次 execution。
- [x] Step 2：写 `test_stop_dimension_comes_from_budget_branch`，覆盖 steps/time/cost/cancelled/外部 unknown，断言 reason/message不变；fake clock 验证观察时间读取不篡改 deadline。`test_stop_summary_survives_span_cap`：结构 cap 后最终 status/stop_reason/预算仍与 TaskResult 一致。
- [x] Step 3：写跨 phase 重复 SDK call ID、三态、非探索 structured contract/verdict 调用不被标成探索扣步、export 后最终状态变化测试。run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_budget.py tests/unit/test_observability_decision_events.py tests/integration/test_agentscope_observability.py tests/integration/test_otel_observability_lifecycle.py -q`，确认 RED。
- [x] Step 4：实现 dimension 与只读 snapshot；观察开启才读预算时钟/构建副本。只在 middleware 的实际 take_step 后加 charge；拒绝点给预算快照不声明“此次工具消耗一步”。runtime 记录 phase 的真实结果。CLI finish 传最终结果，task.stopped 与最终摘要不互相覆盖事实。
- [x] Step 5：同一测试集 GREEN；trace/no-trace 对比步数、调用、结论与 cancellation/cleanup。可授权提交 `feat: connect trace decisions with budget and task termination`。

## Task 4：把缺失与诊断链呈现为可读视图

**Files:** Modify `trace_view_model.py`、`trace_rendering.py`、`resources/trace_viewer.mjs`、`trace.html.template`；Test `tests/unit/test_trace_view_model.py`、`test_trace_rendering.py`、`tests/frontend/trace_viewer.test.mjs`。

**Interfaces:** 新增 `build_diagnostic_chain(document: dict) -> list[dict]` 到 trace_view_model.py；build_trace_view 产出 `diagnostics` 与 header.capture_health/diagnostic_summary。每行含 `kind/node_key/phase_id/contract_version/decision_code/budget/content_keys/relation`，不嵌入正文。relation 仅 `same_tool_call/same_phase/contract_transition/sequence_only`；无明确关联时 sequence_only。

- [x] Step 1：写 `test_diagnostic_chain_distinguishes_revision_and_refusal`：v1初始→预留拒绝→发布契约拒绝→两次引用拒绝→步骤耗尽；没有 v2、candidate、run 或 verifier，就不能画出这些阶段。成功修订例显示 v1→v2 并提供实际字段差异。
- [x] Step 2：写 `test_partial_legacy_chain_does_not_invent_causes`：旧 schema1/2、字段缺失、未知预算、缺失 parent、相同offset与同call ID跨phase，显示已知事实与缺口；顺序以实际 offset、原列表顺序稳定排序，不凭顺序给因果箭头。
- [x] Step 3：写 Python/Node测试：完整性分项标签、精确原因、未知原始长度、点击诊断跳转原节点/正文；OTel OK + result_code ERROR 展示业务拒绝，三态独立，工具计数不翻倍。运行 `.\.venv\Scripts\python.exe -m pytest tests/unit/test_trace_view_model.py tests/unit/test_trace_rendering.py -q` 与 `& E:\node\nodejs\node.exe --test tests/frontend/trace_viewer.test.mjs`，确认 RED。
- [x] Step 4：在既有“验收”页签增设业务诊断区、概览增加分项采集健康提示；不改整套页面导航。诊断行引用正文容器而非再复制契约/工具正文。静态页面显示同样事实，交互点击展开定位原节点；未知或真实损失明确显示。
- [x] Step 5：Python/Node GREEN；用本地隔离浏览器检查跳转、键盘、窄屏、关闭 JS、恶意内容/CSP、零网络。固定脚本改变后重新计算现有 hash，不放宽 CSP。可授权提交 `feat: show capture health and a factual diagnostic chain`。

## Task 5：在固定总容量内补足输入正文

**Files:** Modify `observability_content.py`、`otel_projection.py`、`adapters/agentscope/observability.py`；Test `tests/unit/test_observability_content.py`、`test_otel_projection.py`、`tests/integration/test_agentscope_observability.py`、`test_agentscope_native_tracing.py`。

**Interfaces:** 新增 `capture_sdk_model_input(input_kwargs: dict) -> None` 到 adapters/agentscope/observability.py；ReproTraceMiddleware.on_model_call 在原生 span 已成为当前上下文、next_handler调用前执行。输入取该公开 hook 实际提供的 messages，Msg实例使用已核对存在的 `model_dump(mode="json")`（2.0.9没有`Msg.to_dict()`）、mapping/list递归副本，经过project_sdk_messages与ContentStore处理。只展示实际模型消息，不附带工具运行环境；不使用SDK私有extractor，不重新format/count_tokens/prepare模型输入。

- [x] Step 1：写 `test_hook_keeps_sdk_input_beyond_native_attribute_limit`：实际 SDK 消息 JSON >65536字符、序列化 record <256KiB，直接采集完整，SDK投影不再另造 omitted_limit；真实 trace_id/span/parent不变。恰好边界仍无法判断的原生-only输入必须显示疑似截断。
- [x] Step 2：写 `test_wire_input_under_256k_is_retained_whole`：80KiB真实请求、尾部工具结果与中部标记保留；`test_record_ceiling_includes_json_escaping`：中文、引号/反斜线/多行、恰好/超限序列化记录均按字节计数；更新旧100k会截断等硬编码测试为基于新限额的边界。
- [x] Step 3：写 `test_capture_preserves_requests_and_faults_do_not_replay`：直接hook采集失败→原生fallback+明确原因，handler仍调用一次，原消息/工具参数不变。直接输入成功与原生同span/source只存一个；共享正文仍可多span引用，SDK input 不冒充 wire_request。运行 `.\.venv\Scripts\python.exe -m pytest tests/unit/test_observability_content.py tests/unit/test_otel_projection.py tests/integration/test_agentscope_observability.py tests/integration/test_agentscope_native_tracing.py -q`，确认 RED。
- [x] Step 4：实现 hook直接副本采集、同span/sourcefallback去重与256KiB上限；总4MiB/512条不变，超过时继续明确缺失。保留真实HTTP request hook原路径；结构化gateway调用仍以wire_request为实际输入，不伪造SDK span。
- [x] Step 5：同一集合 GREEN；用21轮、每轮约80KiB、同时SDK+wire保留的确定性fixture验证限额内正文可见，并以更大fixture验证容量降级可定位。不要求超限fixture完整；测序列化大小与页面大小，超6/8MiB时明确拒绝而不是写半份文件。可授权提交 `fix: retain longer model input without relying on otel attributes`。

## Task 6：端到端验收与文档

**Files:** Create `tests/integration/test_observability_diagnostic_chain.py`；Modify `tests/integration/test_observability_end_to_end.py`、`test_cli.py`、`test_windows_long_paths.py`（仅相关断言）、`docs/observability.md`；Create 上述验收回执。证据保存在 `.local/observability-diagnostics-repair-verification/`，不提交生成trace/大日志。

**Interfaces:** 复用现有 run_sdk_task/MockTransport 与真实pytest执行fixture；自动产生新版trace，浏览器只打开本地产物。历史pytest #5227 trace仅只读对照，禁止覆盖原件或假装修复后可恢复旧正文。

- [x] Step 1：写两个失败的端到端测试：`test_ungrounded_publish_and_unread_citations_end_in_step_exhaustion`（权限ALLOWED、发布失败、两次引用失败、明确steps停止、没有candidate/run/verifier）；`test_successful_revision_and_candidate_keep_the_business_result`（确实修订、发布、pytest与原验收结论一致）。响应脚本设置thinking disabled，记录实际出站请求用于断言。
- [x] Step 2：run `.\.venv\Scripts\python.exe -m pytest tests/integration/test_observability_diagnostic_chain.py tests/integration/test_observability_end_to_end.py -q`；先记录RED，再补集成遗漏，GREEN后用trace/no-trace比较原请求体、响应、步数、HTTP尝试数、工具执行数、结果/证据等级/交付候选。
- [x] Step 3：一次运行本次相关回归：`tests/unit/test_observability*.py`需在PowerShell用`Get-ChildItem tests/unit/test_observability*.py`展开为路径数组传pytest，另加 `test_otel_projection.py test_trace_view_model.py test_trace_rendering.py test_budget.py test_controller.py`；integration选 `test_agentscope_observability.py test_agentscope_native_tracing.py test_agentscope_domain_tools.py test_observability_diagnostic_chain.py test_observability_end_to_end.py test_otel_observability_lifecycle.py test_cli.py test_windows_long_paths.py test_installed_package.py`；Node frontend；`.\.venv\Scripts\python.exe -m pip check`。仅新故障或跨层修改才扩大/重复集合。
- [x] Step 4：浏览器验收新生成的正常/耗尽/长正文/限额/legacy/恶意内容/1024节点文件，点击原节点与正文定位，核对统计、静态回退、窄屏和零外部请求。禁止使用Astra；自动化浏览器本身不用模型。不引入生产前端依赖；复用现有本地验证脚本与可用Playwright/隔离Chrome。
- [x] Step 5：更新 docs/observability.md 容量、reason、独立完整性与诊断入口。验收回执逐项记录 expected/status/command/observed/evidence_ref/fix_location/recheck_command；所有失败明确定位，未测/skip单列。注明“真实SDK+MockTransport+真实pytest”，不能宣称完成真实模型能力评测；付费真实模型运行另需可用凭据与已授权案例范围，不作为本修复的默认步骤。
- [x] Step 6：自检计划覆盖与结果，按下表逐项验收。若用户授权提交，提交 `docs: verify local observability diagnostics repair`；push/merge仍等待用户明确请求。

## 通过标准与失败定位

| ID | 必须通过 | 失败优先检查 |
| --- | --- | --- |
| D01 | 正常未返回reasoning不误报；实际损失有精确原因、限额和定位；无原因partial仅允许legacy且明确标注 | ContentStore / TaskTraceSink |
| D02 | 正文截断不污染计数/token；价格缺失只影响费用；真实丢失的统计不声称总额 | finish / summary / viewer header |
| D03 | 初始契约、真正修订与拒绝请求区分；每项差异来自实际已保存版本 | Controller / contract_diff |
| D04 | 三态权限与执行后业务拒绝可区分；引用失败记录实际检查范围；无正文关键词推断 | middleware / domain检查分支 |
| D05 | 一逻辑探索步只扣一次，HTTP重试不重复计步，最终停止与TaskResult一致且span cap后仍可见 | Budget / runtime / CLI.finish |
| D06 | 可读链关联有依据，可点击原span/content；旧trace显示缺口、不生成不存在的candidate/verifier | view_model / renderer / JS |
| D07 | >65536字符SDK模型输入限额内可完整保存；80KiB实际wire输入可完整查看；256KiB/4MiB超限明确 | public hook / ContentStore |
| D08 | thinking仍disabled；开启/关闭trace模型请求、步数、工具、复现结论一致；故障不重放 | integration / SafeTracingMiddleware |
| D09 | 单文件/无JS/CSP/零网络/长路径/旧schema/6MiB写读与8MiB页面限制通过 | renderer / writer / paths / 浏览器 |
| D10 | 新回执有命令和证据、未测项明确，未拿旧回归数量或历史失败运行充当新验收 | docs/reviews |

## 执行与计划自检

执行顺序固定为 Tasks 1→2→3→4→5→6，与用户优先级一致：先让损失可解释，再把业务链读清楚，再提高正文保留。每个任务先RED再GREEN；途中若需改预算、业务验收规则、增加服务、外部正文文件或模型调用，应停止该扩展并单独说明，不能藏在观测修复里。

自检：capture关键词参数在facade/handle/sink/store一致；schema保持2可选扩展；原生属性字符限额与本地序列化字节限额明确分开；工具权限与执行共用原tool_call_key；预算消费位置与实际类名已核对；未知长度为null；精确reason不从旧记录或异常文案猜测；每个Review Focus都有测试归属；编写时尚未执行产品变更或付费模型验收。执行状态：**本地实现与验收完成（2026-10-11）**；760通过、5环境跳过、0失败，Node9与浏览器11项通过。详见[验收回执](../../reviews/2026-10-10-observability-diagnostics-repair-acceptance.md)。复查采用语义等价的新增回归与完整pytest集合；条件性commit/push/merge未触发，所有代码保留在codex/observability-diagnostics工作区。
