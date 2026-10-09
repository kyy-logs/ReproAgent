# ReproAgent Dual-Layer Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in the current session. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 既能审查 Agent 实际收到什么、返回什么、调用工具得到什么，也能审查系统执行过程及接受变更的依据；所有观测均从已有调用/工件采集，不额外请求模型。

**Architecture:** AgentScope 原生 middleware hooks + 现有模型工厂 HTTP 边界 + 领域程序边界，使用任务级 ContextVar 和有界内存 recorder。元数据与可选正文分开限额，结束后写同一 trace.json，离线 HTML 展开查看。已有契约/候选/verdict/report继续承担复现验收；开发过程以计划、验收矩阵和实测回执交付。

**Tech Stack:** Python 3.11+ 标准库、当前 AgentScope 2.0.9、pytest、httpx.MockTransport。无新增模型、服务或依赖。

**Spec:** 本文件“设计合同”是本次规格；业务与证据边界沿用 `docs/superpowers/specs/2026-10-05-reproagent-architecture-design.md`，学习时序沿用 `docs/superpowers/specs/2026-10-06-reproagent-experience-evolution-design.md`。

**Baseline:** 2026-10-09 核对本地 `76d4f95`；真实类为 `model_factory.py:_GuardedModel`，方法为 `__call__/_attempt/_account`，传输观察函数为 `_response_hook`。本次重写计划，未实现；执行前冻结最新基线、保留其他代理改动。

## 设计合同

### 两层分别回答什么

| 层 | 要回答 | 第一版交付 |
| --- | --- | --- |
| 运行时可观测性 | 系统与Agent实际做了什么，在哪一步失败 | 已有日志 + trace层级/耗时/预算 + 可选模型/工具内容 + 现有进程/健康信号 |
| 过程可观测性 | 按什么计划/标准行动，为什么接受或拒绝结果/变更 | 复现契约版本、候选与验收决定的可见关联；开发范围合同、检查矩阵、实测/审查回执 |

运行时采集与生成JSON/HTML不额外消耗模型token；过程文档由实现代理依据实际检查填写，不新增“评分Agent”。

### 使用方式与输出

```text
# 只有元数据
reproagent run --config task.json --model-config model.json --trace

# 同时保留脱敏内容，供优化时审查
reproagent run --config task.json --model-config model.json --trace --trace-content

# 离线生成查看页面
reproagent trace path/to/task-output
```

两种观测模式都属于本次必须实现的功能。`--trace-content` 未配 `--trace` 时返回配置错误2，创建任务/发模型请求之前拒绝。默认关闭所有trace；只有--trace时不采集正文。

每次Bug任务仍只增加两个观测文件：
```text
task-output/observability/trace.json
task-output/observability/trace.html
```

trace.json将正文按content_id存一次，各span通过content_refs关联；HTML只将每个content_id渲染一次。无额外每调用文件、正文目录或数据库。HTML为本地静态页，用details/pre展开，不执行采集内容、不用远程资源、不自动打开窗口。

开发本功能时另留一份 `docs/reviews/2026-10-09-dual-layer-observability-acceptance.md`，不要求每个Bug任务再生成一份开发验收文档。

### 第一版必须看见的内容

| 内容 | 实际采集来源 | 显示规则 |
| --- | --- | --- |
| 模型输入 | HTTP request已缓冲JSON中实际messages/tools/相关模型选项，覆盖exploration/contract/verdict/learning | SDK格式化之后的请求副本；不把格式化前Msg宣称为精确wire |
| 模型输出 | 现有_response_hook已经读入的choices[0].message：content和tool_calls | 保留协议拒绝/纠错前可取得的内容，区分provider返回与程序接受 |
| 接口推理字段 | 真实返回的reasoning_content；必要时SDK实际返回ThinkingBlock且注明来源 | 显示“接口返回的推理文本”；缺失/关闭/不支持明确注明，不补写 |
| Agent说明 | 模型真实content中的计划/解释，request_information或revise_contract的已有说明 | 有则显示，只有tool_calls时标“未返回说明”，不猜测选工具的理由 |
| 工具参数 | on_check_permission已校验的tool_input | 一次permission只捕获一次；允许/拒绝/发布预留均保留已取得的参数 |
| 工具结果 | on_acting原样透传的最终公开ToolResponse.content/state | 显示实际返回的脱敏副本；无final response时incomplete，不将碎片拼成完整结果 |
| 契约与验收决定 | 当前程序已有IssueContract、候选元数据、Verdict.reason/classification/unmet_checks/uncertainties/evidence_refs | 关联版本/ID，显示已有依据；空unmet_checks不代表所有检查都通过 |
| 原始运行日志 | 已有run的stdout/stderr/probe引用 | trace不重新读或复制正文；可查原任务记录 |

不保证取得模型内部完整思维过程。不得为“看思考”修改system prompt、开启thinking_mode、提高输出额度或发追问。推理文本是提供方返回的解释材料，不保证忠实反映内部决策。

工具最终结果与下一次模型请求中的tool消息分别标SDK结果/wire输入；若SDK对上下文进行了处理，两者不混称完全相同。工具选择被整份模型响应校验拒绝、尚未到permission时，参数只可能存在于provider输出，不能伪造tool请求或执行节点。

### 内容采集与资源约束

- metadata默认模式：最多1024个span/点、每条完整UTF-8 JSON≤2048字节，metadata部分≤1MiB，整个trace.json≤1MiB。
- content模式：最多512条正文，每条**最终序列化JSON含信封**≤32768字节；contents部分合计≤4MiB，整个trace.json≤6MiB。metadata上限不变。HTML metadata模式≤4MiB，content模式≤32MiB。
- 正文记录先按白名单选字段，在新对象/字符串副本上脱敏，然后计算实际UTF-8序列化大小；不能修改请求、Msg、ChatResponse、ToolChunk或ToolResponse。
- 脱敏已知模型凭据及标准敏感键（Authorization/api_key/password/secret/cookie等），处理JSON转义形式；不采集header/URL查询/环境变量。自由文本可能含其他秘密，因此正文须显式开启、只本地保存、不自动上传，不声称完整秘密扫描。
- 超限正文保留标注过的head/tail UTF-8摘录，记录original_bytes/captured_bytes/truncated/excerpt_mode；不把不完整JSON摘录当已解析完整工件。防止只取请求头部让最新工具消息永远不可见。
- original_bytes指所投影正文在脱敏/截断前的UTF-8长度，captured_bytes指脱敏摘录的UTF-8长度；最终信封序列化另外测量，不能用字数代替字节。
- 内容预算耗尽时不创建超限content记录，仍在span.content_status登记omitted_limit；元数据也缺失时root保持partial，不能把缺引用解释为未返回。
- 内容缺失分别标disabled/not_returned/omitted_limit/unsupported/incomplete/capture_error，不能都用空串代替。已取得但被省略与接口根本未提供要分开。
- 解析request只针对已缓冲、≤1MiB的JSON；流式/更大请求标不支持/超限，不为观测调用read/aread消费流。响应只使用现有安全读取已取得的数据，不重新请求、额外读取或放宽原1MiB响应上限。
- 对已缓冲但无效JSON的响应，可采集有界invalid_response_preview并标不能解析；被业务拒绝的编码/未完成流不得为了观测再读取。
- 只采集文本/JSON文本字段；SDK图片、音频、附件块只标类型/unsupported，不解码或复制二进制。wire.reasoning_content优先，缺失时才采用公开ThinkingBlock.text并标SDK来源；不重复存两份“思考”。
- 元数据或内容损失分开：partial为整体不完整，metrics_complete取决于元数据，content_complete取决于应采集内容是否保留。reasoning未提供是正常availability，不自动算系统故障。
- 缓存有界、采集错误失效关闭；仅本地CPU/内存/磁盘开销，不写events.jsonl。生成输出在主结果及学习完成后进行；硬杀可能来不及落盘，保留原日志作为排障来源。

### 原生hook、关联与计数

1. 新TraceMiddleware注册顺序为 `[TraceMiddleware, ExplorationMiddleware]`。现有ExplorationMiddleware.on_model_call直接调用模型，观测必须在外层，才能看到协议检查异常。
2. on_model_call观察sdk.model_round；_GuardedModel.__call__一次model.logical，_attempt每次model.http_attempt。真实request回调和_response_hook关联当前HTTP span，不能重复计logical或HTTP。
3. on_acting只包实际执行层，原样yield全部对象一次；最终ToolResponse.state在yield前记终态，合法发布后的SDK收尾取消不能把成功工具改成失败。
4. permission点固定三态ALLOWED/DENIED/RESERVED_FOR_PUBLISHING。DENIED也包括SDK deny规则，发布预留独立标签/颜色/计数；不再次调用权限检查。
5. tool_call_key=SHA256(trace_id + 最近explore span_id或root + 当前steps_used + SDK tool_call.id)前32位；permission参数与tool结果用同key关联。不存原始SDK ID，不按名称/时间猜配；SDK ID跨步重用不合并。
6. tool_executions只数进入on_acting的tool span，每span一次。permission点不计执行；允许后未执行、拒绝或预留均不产生执行数。
7. Runtime真实allowed_tools：无experience_view时固定TOOL_NAMES六个，有有效view时加read_experience共七个。不能仅凭six docstring写死；观测不增业务工具。
8. 只有HTTP叶子汇总usage/费用；sdk.model_round、logical和model.completed不重复加钱。unknown保持unknown。父子耗时不相加当总耗时。

### 进程、健康与领域决定

- LocalBackend.execute投影现有spawn、退出/超时/取消、terminate_tree清理结果。范围是其管理的probe/baseline/候选进程；工具内其他进程只用tool span，不声称OS全进程监控。不得新增poll/进程/终止调用。
- health只复用已有target_environment、process_cleanup及trace_capture信号：passed/blocked/failed/degraded/not_run/unknown。缺失不能标健康；环境原版/修复版分列。
- 业务execution_role保持original/fixed；repeat是操作标签。候选pytest退出1或baseline失败不自动变成环境坏；进程正常结束不等于Bug复现成功。
- Controller记录现有契约版本、修订/候选/缺信息的实际phase决定；不让Agent额外生成“计划”或自我评分。
- Verifier在原本的实际检查点记录program.check点（检查名、pass/fail/not_evaluated），保持原短路次序、只做一次原检查；未走到的检查不能标pass。不得为观测重复candidate_hash/resolve/check_framework或追加模型验收。
- Verdict理由在原返回点标reason_origin=program/provider/unknown，不靠字符串猜来源；不修改Verdict DTO。
- 语义布尔expected_assertion/target_triggered/failure_matches_issue标source=provider_claim；程序实际绑定/保护/清理/probe/引用与重复检查标program_check。最终Verdict/TaskResult标program_artifact，不能把模型自报成功当程序已接受。
- 内容模式可展开既有contract与verdict理由/不确定项；metadata模式只给ID、分类和受控检查状态。评分标准沿用现有硬检查+语义核验+重复/可选差分，没有新增任意百分制。
- trace及正文不成为EvidenceLedger引用，不送回Agent/Verifier/学习，不改变候选、契约、证据等级或已发布包。不自动回放模型请求或工具。

### 输出结构及界面

根schema_version=1，字段：trace_id/task_id/status/main_duration/total_duration/capture_mode/partial/metrics_complete/content_complete/warnings/spans/contents/summary。另保留根started_at用于时间展示。root status复制TaskResult；learning失败不改主status。

span：span_id/parent_span_id/name/kind/offset_seconds/duration_seconds/status/attributes/content_refs/content_status。offset与duration都使用任务单调时钟；wall_clock仅作根开始时间展示，不参与排序或预算。点事件duration=null；属性严格白名单，包括purpose、角色、控制码、工具集、tool_call_key、permission_result、预算、usage/费用、现有工件ID/引用。

content：content_id/owner_span_id/kind/source/availability/text/original_bytes/captured_bytes/truncated/redacted/excerpt_mode。仅已开启内容模式保存text；来源固定wire_request/provider_response/provider_reasoning/sdk_tool_input/sdk_tool_result/program_artifact/invalid_response_preview，不存原始HTTP包。

summary分列logical_calls/http_attempts/usage/known_cost_subtotal/unknown_cost_attempts、权限三态、tool_executions、main/learning，以及每种/角色有界health聚合。缺usage时总token为null、分列已知小计/未知attempt数；缺计价时总费用为null、保留已知小计。partial时明确“已保留部分”，不伪造精确总数。

HTML提供三个区域：
- 总览/时间线：状态、慢节点、HTTP重试、预算、权限预留与进程/健康。
- 调用详情：按span/tool_call_key展开输入、输出、已返回推理文本、参数、结果；截断/未提供醒目标注。
- 决策与验收：契约版本、实际program.check、provider_claim、候选/run/verdict关系和最终接受/拒绝理由；可查既有report，不新造判定。

所有文本html.escape放pre，代码/HTML/Markdown/链接/推理文本均当纯文本；不执行脚本，不根据正文生成任意文件链接，不加载远程资源。

## 变更范围与公共接口

Create:
- `src/reproagent/observability.py`：ContextVar、有界recorder/span/mark、指标/JSON持久化。
- `src/reproagent/observability_content.py`：白名单、复制脱敏、UTF-8 head/tail/序列化字节限额。
- `src/reproagent/adapters/agentscope/observability.py`：原生TraceMiddleware。
- `src/reproagent/trace_rendering.py`、`src/reproagent/resources/trace.html.template`：离线验证与HTML呈现。
- `docs/observability.md`、`docs/reviews/2026-10-09-dual-layer-observability-acceptance.md`及任务定义的测试。

Modify:
- `src/reproagent/adapters/agentscope/runtime.py`、`middleware.py`、`model_factory.py`：注册顺序、permission投影、_GuardedModel边界、已缓冲wire采集。
- `src/reproagent/core/controller.py`、`core/verifier.py`、`runner.py`、`adapters/runtimes/local.py`、`experience.py`：只包现有scope、投影现有检查/工件；保留短路、次数与顺序。
- `src/reproagent/cli.py`：两旗标和离线命令，非致命观测写失败；`tools.py`只同步六/七工具docstring。
- `README.md`及本计划状态记录。

不改：模型配置/temperature/thinking/output限额、system prompt、工具注册/输入输出、权限与最后三步预留、业务状态推进、预算、EvidenceLedger规则、TaskStore领域事件格式、Verifier接受标准、学习来源与时序、manifest/replay、官方评分、目标项目或依赖。超范围变更先记录原因并更新合同，不混入观测提交。

共享接口：
```text
TraceRecorder(*, capture_content=False, secrets=(), clock=time.monotonic, wall_clock=time.time)
trace_session(*, enabled: bool, capture_content=False, secrets=()) -> ContextManager[TraceRecorder | None]
tracing_enabled() -> bool
span(name: str, *, attributes: dict | None = None) -> ContextManager[SpanHandle]
mark(name: str, *, attributes: dict | None = None) -> SpanHandle
SpanHandle.annotate(**allowlisted_fields) -> None
SpanHandle.capture(kind: str, value, *, source: str, availability='captured') -> str | None
tool_call_key(*, sdk_call_id: str, step_index: int) -> str | None
record_check(name: str, value: bool, *, source='program_check') -> bool
TraceRecorder.finish(*, task_id: str, status: str, main_duration: float, learning: dict | None = None) -> dict
write_trace(task_dir: Path, document: dict) -> Path
validate_trace(document: dict) -> dict
render_trace(document: dict) -> str
write_trace_html(task_dir: Path) -> Path
```

record_check原样返回bool；disabled no-op不读时钟/正文、不创建文件。观测方法内部错误捕获并降级，领域异常/BudgetStopped/CancelledError原样传播。所有采集内容是副本。

## Global Constraints

- 不多调用模型/工具/formatter，不新增健康检查，不改输出额度或请求thinking；真实HTTP、预算/费用原规则保持。
- 作用域任务隔离，覆盖主资源close、封存与独立学习；主duration与包含学习的trace总时长分开。
- 主Controller返回且资源关闭后CLI写trace；写失败stderr固定警告，主JSON/退出码不变。原TaskStore错误仍按原流程，不被观测掩盖。
- 统一paths.workspace_path入口；建目录directory_path，写文件既有store.atomic_write/shared_path_form；位置检查is_within/identity_key。拒绝观测目录/文件链接重定向，不写artifacts，不接受任意--output。
- 所有元数据/正文/HTML的完整序列化限额与可用性标签是必须检查项，截断不能伪称完整。
- 调用内容是待审查数据，不执行、不送回模型、不作为证据、不自动上传。第一版不接远程平台/队列/自动评分/跨任务检索/自动请求回放。
- 本计划只授权离线实现与验证；真实模型费、部署和push另行授权。

## Review Focus

1. wire与SDK视图差异、缺推理、无效输出：来源/可用性诚实，坏内容可诊断，不能追加读取/推理调用。Task 1/2。
2. 原on_model_call绕next_handler、异步流与发布后取消：外层可见、对象/调用次数不变，拒绝/预留和执行正确关联。Task 2。
3. 短路验收、进程/健康与学习时序：不重复检查、不改变原结果，不把模型解释当已验证事实。Task 3。
4. 凭据/转义/UTF-8/大正文与损坏文件：脱敏副本、有界、明确截断、HTML纯文本，Windows长路径一致。Task 1/4。
5. partial/观测失败/重试/并发：保留主结果，成本只计HTTP一次，正文缺失不伪造内容或精确总量。Task 1–5。

## Task 1: 有界Recorder与内容策略

**Files:** Create `src/reproagent/observability.py`, `src/reproagent/observability_content.py`, `tests/unit/test_observability.py`, `tests/unit/test_observability_content.py`。
**Interfaces:** Produces recorder/scope/SpanHandle/mark/check/tool_call_key/finish；持久化与render由后续任务完成。

- [ ] Step 1: 写 disabled no-op、嵌套时钟、并发任务/跨步重用ID、短路bool返回、正文模式、已知凭据与JSON转义脱敏、复制不改原对象、UTF-8精确限额/head-tail、1/4/6MiB/1024/512边界和capture_error测试。
- [ ] Step 2: 跑新增两单测RED，确认缺新行为；用可控时钟/真实序列化字节，不能测试镜像实现。
- [ ] Step 3: 实现共享接口、白名单/有界副本、availability、partial/metrics/content完整性分开、HTTP叶子统计。所有不可得内容明确标注，无自动解释补写。
- [ ] Step 4: GREEN，新增单测全过，disabled不读取正文/时钟，元数据模式无正文，任何观测异常不改变原异常或bool值。
- [ ] Step 5: 提交 `feat: add bounded trace and content recorder`。

## Task 2: 原生Hooks与真实Wire输入输出

**Files:** Create `src/reproagent/adapters/agentscope/observability.py`, `tests/integration/test_agentscope_observability.py`；Modify `src/reproagent/adapters/agentscope/runtime.py`, `src/reproagent/adapters/agentscope/middleware.py`, `src/reproagent/adapters/agentscope/model_factory.py`, `src/reproagent/adapters/agentscope/tools.py`（最后者仅docstring）。
**Interfaces:** Consumes Task 1；TraceMiddleware(context: CallContext, *, allowed_tools: tuple[str,...])；Produces model/logical/HTTP、permission/tool关联与content_refs，覆盖四purpose。

- [ ] Step 1: 真实SDK+MockTransport写输入采用实际wire（含前轮工具结果和相关输出/temperature等配置）、输出content/tool_calls、reasoning有/无/空、参数/最终结果、协议拒绝内容、丢响应/坏JSON/大请求/未缓冲流可用性、raw对象不变与无额外read/request测试。
- [ ] Step 2: 跑RED；三HTTP/一logical、同一固定输入的off/metadata/content模式实际请求体字节与formatter次数一致（测试固定随机ID/时钟，或在一次发送前后核对不变）；正常chunk对象/顺序一次转发，ToolResponse.ERROR与发布后取消区分。
- [ ] Step 3: 注册外层hooks。_GuardedModel逻辑/尝试scope与_account原记账同源；启用内容时request callback只读已缓冲body，_response_hook复用原安全读取的数据，在协议拒绝前捕获已取得字段。不能扩大响应限制、二次读取或变更异常。
- [ ] Step 4: permission记录三态与一次参数；on_acting记录实际tool span和最终结果，当前step/phase/key一致。测4→3步预留、SDK deny、默认6/经验7、同SDK ID跨步、两个关联节点只计一次执行；未知工具整份拒绝不伪造执行。
- [ ] Step 5: 跑新增及test_agentscope_runtime/domain_tools/model_factory/gateway/experience。绿色后提交 `feat: capture AgentScope calls and tool results through native hooks`。

## Task 3: 领域决定、检查、进程与健康

**Files:** Modify `src/reproagent/core/controller.py`, `src/reproagent/core/verifier.py`, `src/reproagent/runner.py`, `src/reproagent/adapters/runtimes/local.py`, `src/reproagent/experience.py`；Create `tests/integration/test_observability_lifecycle.py`、`tests/unit/test_observability_decisions.py`。
**Interfaces:** Consumes scopes/check/content；Produces既有contract/candidate/verdict关系、program检查、process/health，与封存后learn scope。

- [ ] Step 1: 写 contract版本/修订决定、候选/run/verdict来源关联、reason/unmet/uncertainty可见、硬检查短路（不多hash/resolve）、provider_claim与program_check分开、无说明不补写、原版/修复版健康及进程退出1语义测试。
- [ ] Step 2: 跑RED；真实pytest对比trace off/metadata/content三种模式状态、证据、候选、HTTP、steps/资源close；不比较真实浮点耗时完全相同。
- [ ] Step 3: 在现有程序实际边界包scope，投影已取得工件，不为观测新增读文件。Verifier每个实际条件通过record_check包一次，保持or/and短路；未执行检查标not_evaluated，不凭默认unmet_checks为空当通过。
- [ ] Step 4: LocalBackend投影已有spawn/退出/终止与cleanup；Runner复用probe，分别标not_run/unknown/blocked/passed。保留超时/取消、execution_role枚举与学习30秒+收尾1秒。验证内容/trace不进入Agent/Verifier证据/学习材料/manifest。
- [ ] Step 5: 跑新文件及test_controller、test_runner、test_local_backend、test_experience_lifecycle与Verifier相关单测。绿色后提交 `feat: expose reproduction decisions and runtime health without changing acceptance`。

## Task 4: CLI落盘与内容时间线

**Files:** Create `src/reproagent/trace_rendering.py`, `src/reproagent/resources/trace.html.template`, `tests/unit/test_trace_rendering.py`；Modify `src/reproagent/observability.py`, `src/reproagent/cli.py`；Extend `tests/integration/test_cli.py`, `test_windows_long_paths.py`。
**Interfaces:** Consumes schema1、两模式；Produces write_trace/validate_trace/render_trace/write_trace_html 和两旗标/离线trace子命令。

- [ ] Step 1: 写参数错误先拒绝、主结果封存且学习资源close后落盘、JSON写失败原退出码保留、旧任务无trace、1/6MiB读取上限、未知schema/深层JSON、内容引用缺失/owner不匹配/父节点环、HTML注入/超限及long-path tests。
- [ ] Step 2: 跑RED；确认只改observability目录，不复制候选/原日志，不覆盖task.json/artifacts；正文未开启/不可得/截断在页面不同显示。
- [ ] Step 3: 实现CLI新trace_session；全部读写走workspace_path/directory_path/atomic_write/is_within/identity_key，拒绝目录/文件链接到artifacts或外部。JSON加载有界、验证contents与refs，bad输入返回2而不影响既有领域文件。
- [ ] Step 4: 静态HTML三个区域，正文纯文本展开，每content一次，显示原始来源/脱敏/截断与可用性。Windows覆盖observability目录248–259窗口、目标>260、仅临时名跨界，CLI保持短cwd。
- [ ] Step 5: 跑新render、CLI、Windows与exporter回归；模板独立wheel安装验证包含且可渲染。提交 `feat: add local trace content inspection and safe persistence`。

## Task 5: 双层验收与使用回执

**Files:** Create `docs/observability.md`, `docs/reviews/2026-10-09-dual-layer-observability-acceptance.md`, `tests/integration/test_observability_end_to_end.py`；Modify `README.md`及本计划状态。
**Interfaces:** Consumes前四项完整流程；Produces开发验收回执/使用说明，非新的模型评分接口。

- [ ] Step 1: 写真实SDK+pytest端到端：三种模式同业务结果；内容模式能按key看参数/结果、实际模型输入/返回推理（mock有/无两case），契约/检查/verdict接受理由和主/学习资源关闭链路。无正文模式不包含唯一正文marker，采集不加调用/token。
- [ ] Step 2: 跑RED并修到GREEN；包含误导模型自报、程序检查拒绝、健康blocked、内容超限、collector/persistence错误；预期负例正确拒绝也是该测试通过，不能靠trace存在就算验收。
- [ ] Step 3: 用法说明两flags、两文件、来源与思考边界、本地敏感内容/限额、无额外token、日志与trace角色、硬杀限制和每种失败的排查位置。不自动上传、回放、提高模型思考额度。
- [ ] Step 4: 冻结实现版本/环境，设置REPROAGENT_RG_PATH，跑 `python -m pytest tests/unit tests/integration -q -rs`、`python -m pip check`；读取真实输出并保存本地日志。一次全分支独立审查，Important/Critical按RED→GREEN修复后全套再验，保留初次失败。
- [ ] Step 5: 逐项填写下方验收矩阵与修复定位，记录源码/依赖/命令/结果/证据/取舍。只在必选项有证据pass时宣称本地验收通过；提交 `docs: record dual-layer observability verification`。当前所有任务均未执行。

## 过程层：验收标准、检查表与失败定位

执行前写清上述改动/不改合同，冻结基线与环境；执行中保存本计划执行台账与RED/GREEN/提交/取舍；实现后保存必要日志到 `.local/dual-layer-observability-verification/`，把可公开摘要写一份repo验收回执。原始失败不删除，不能用模型印象分/测试总数代替逐项结果。

| ID | 必须检查的功能/测试/边界 | 当前 | 未过时改哪里 |
| --- | --- | --- | --- |
| O01 范围/默认 | diff在清单内；默认off不读正文/增文件/改调用 | not_run | 计划、cli.py、runtime.py |
| O02 实际内容 | wire输入/输出、工具参数与最终结果可审查；坏输出、缺思考、截断诚实；不猜理由 | not_run | content模块、model_factory.py:_response_hook、SDK观测hooks |
| O03 关联/计数 | HTTP重试不双计；权限三态分列；检查与执行同key；真实六/七条件 | not_run | model_factory.py、middleware.py、recorder与viewer |
| O04 决策/验收 | 契约版本/候选/verdict关联；检查短路不变、未走到不标pass；模型声明与程序接受分开 | not_run | controller.py、core/verifier.py、决策呈现 |
| O05 业务/预算 | 三模式状态/证据/候选/调用/预算等价，学习在封存后且预算独立 | not_run | scope集成、cli.py、experience.py |
| O06 进程/健康 | 只复用真实probe/运行/清理；超时取消可见，候选退出1不伪报环境坏 | not_run | runner.py、adapters/runtimes/local.py |
| O07 有界/隔离 | UTF-8序列化大小、copies/脱敏、partial分层；错误不影响主结果；不进入模型/证据/学习 | not_run | observability.py、content模块、hooks |
| O08 输出/平台 | 仅两观测文件；HTML纯文本、refs可核对；长路径/临时名/链接/读限额/模板wheel通过 | not_run | rendering、CLI、paths/atomic_write调用 |
| O09 回归 | 当前完整unit/integration及pip check有实测日志，跳过/平台缺口单列，不借旧数 | not_run | 根据首个失败test定位，不降断言 |
| O10 接受依据 | O01–O09命令/结果/证据/未过修复文件和符号齐全；审查/修复/取舍可读 | not_run | 开发验收回执 |

回执每项字段：check_id/expected/status(pass/fail/not_run)/command/observed/evidence_ref/fix_location/recheck_command。必选关键断言只有skip或未运行时不能标pass；未验证OS/CI/真实模型性能比较明确另列，不用总体通过率抵消。

每次Bug的“为什么接受”仍来自现有Verifier与证据等级，trace展示/关联这些工件；开发本功能的“为什么接受”来自该矩阵和实测回执。两者不混成第二套Bug评分。

## 计划自检与当前状态

- 替换旧“正文以后再做”范围；第一版必须支持可选真实输入输出/工具内容/返回推理与决策工件。
- 已核对_GuardedModel/_response_hook、hooks顺序、ToolResponse最终态、条件六/七与三态预留。
- 来源/缺失/截断/信任等级明确，不能补写内部思考或新增模型请求；内容只在本地副本保存。
- metadata/正文/总JSON/HTML上限一致；模型费用只从HTTP叶子统计；并发与短路/long-path/非致命失败均有具体验证。
- 改动范围、公共接口、每task交付、矩阵失败定位完整；所有验收仍not_run。
- 执行方式沿用当前会话逐项实现；本次仅重写计划，未改产品代码、未调用真实模型、未push。
