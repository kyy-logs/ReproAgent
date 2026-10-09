# ReproAgent Minimal Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in the current session. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 同时提供运行时信号与实现过程验收工件：定位任务耗时、失败、进程状态和预算消耗，并明确变更范围、通过条件、实测证据及失败修复位置；不增加模型请求或改变复现结论。

**Architecture:** 复用已安装 AgentScope 2.0.9 的原生 middleware hooks，配合现有 SDK 模型工厂的实际 HTTP 尝试边界、Controller/Runner/Verifier/ExperienceService 的程序边界。用任务级 ContextVar 与有界内存 Recorder 关联父子 span，任务结束后可选写入本地 JSON；HTML 查看命令独立运行。领域事件和已封存包保持原有职责。实现代理复用执行台账，按本计划的验收矩阵留下一份实现回执，不增加独立评分 Agent。

**Tech Stack:** Python 3.11+ 标准库（contextvars/time/uuid/json/html）、现有 AgentScope 2.0.9、pytest、httpx.MockTransport。无新增服务和依赖。

**Spec:** 本文件“设计约定”是本次最小版本的设计规格；业务边界沿用 `docs/superpowers/specs/2026-10-05-reproagent-architecture-design.md`、经验时序沿用 `docs/superpowers/specs/2026-10-06-reproagent-experience-evolution-design.md`。

**Baseline:** 原规划读取本地提交 `88eb3b4`；2026-10-09 审查反馈核对当前 `338316c`。仅编写计划，尚未实现；执行前核对最新代码，保留其他代理改动。

## 双层可观测性与本次变更合同

### 第一层：运行时——系统做了什么

| 信号 | 最小版本来源与展示 | 原意必须保留 |
| --- | --- | --- |
| 日志 | 既有 events.jsonl 和运行日志；trace只关联任务/候选/run ID，不复制正文 | 原领域日志语义与保存规则不变 |
| 追踪 | SDK原生hooks、HTTP工厂、Controller/Runner/Verifier/学习边界 | 父子关联、耗时、三态权限、实际调用与用量 |
| 进程事件 | LocalBackend.execute 内现有spawn/poll/terminate_tree结果，记录process启动、结束、清理 | exit_code可为null；stop_reason、cleanup_ok来自真实执行 |
| 环境健康检查 | Runner.prepare 已有目标解释器/pytest/模块probe与准备结果 | 不新增检查进程，不额外ping模型或服务 |
| 观测自身状态 | recorder partial/metrics_complete、落盘固定警告 | 采集缺失明确降级，不能把不完整显示成完整 |

health仅展示已有检查：target_environment、process_cleanup、trace_capture。状态固定passed/blocked/failed/degraded/not_run/unknown；未运行与未采集不算健康。候选pytest退出1是原始观察，可能正是所需Bug，不自动变成environment blocked；具体复现是否成立仍由原Verifier决定。baseline失败同样不能自动否定已通过的环境probe。

process span记录受控spec标识、exit_code、stop_reason、cleanup_ok、execution_role，不存argv/env/cwd或输出正文。spawn后才记录started；超时/取消记录实际stop_reason，清理结果单列，exit_code缺失显示null。spawn异常记录固定码并原样传播，不为观测增加poll或二次终止进程。本轮process事件覆盖LocalBackend管理的probe/baseline/候选进程；工具内部其他进程仍只由tool span显示耗时，不声称已采集所有OS/子进程事件。

Runner的业务execution_role仍只有original/fixed；重复确认是在original上的repeat操作标签，不能把repeat加进业务枚举。trace的命令进程与对应Runner execute/probe/baseline scope关联。process span的正常退出不代表候选符合Issue，页面分别显示“进程退出”与“Verifier判定”。

### 第二层：实现过程——为什么接受这个变更

第一版复用已有计划和执行台账，不新建harness框架。执行前读本文件范围/验收矩阵，冻结基线SHA与环境；执行中按Task记录RED/GREEN、命令、结果、提交和设计取舍；结束时逐项填实测证据及未过项，保留可审查回执。

- 计划：本文件，定义改动范围、决策、公共接口和验收条件。
- 执行台账：沿用executing-plans的本计划工作目录；完成后将必要日志保存在 `.local/minimal-observability-verification/`，避免临时工作区清理后证据消失。
- 验收回执：实现完成时创建 `docs/reviews/2026-10-09-minimal-observability-acceptance.md`，按下方矩阵填写；当前所有项均not_run，不伪造通过数。
- 回执列：check_id、expected、status（pass/fail/not_run）、command、observed、evidence_ref、fix_location。跳过项另列原因/覆盖缺口，不改成pass；若必选承诺仅有跳过证据，不能接受该承诺。
- 失败项必须写明“哪个ID没过、观察到什么、去哪一个文件/符号修、重跑哪条检查”；修复后的RED→GREEN、完整回归与审查结论追加保留，原始失败不删除。
- 通过依据是实际测试/命令/工件检查，不用LLM印象分代替。负例的正确拒绝可通过对应测试；一个Bug任务BLOCKED/EXHAUSTED也不自动说明观测功能实现失败。

这份实现回执只在开发本功能时产生一份，不要求每个Bug任务新增验收文档。每次复现已有contract/verdict/report.md承担领域验收；trace用ID与版本关联它们，不重做评分或改变证据等级。trace.json/HTML仍由本地程序生成，无额外模型调用；过程文档由实现代理依据实测结果填写。

### 明确改哪些、不改哪些

产品修改仅限后面的文件清单：新增Recorder、SDK观测middleware和HTML模板；在Runtime、模型工厂、权限、Controller、Runner、LocalBackend、Verifier、学习、CLI的既有边界投影元数据；补对应测试、用法与验收回执。

保持不变：复现策略/候选内容与生成方式、业务状态机推进顺序、预算数值及扣减方式、工具注册与权限规则、源码/证据保护、TaskStore领域事件格式、导出manifest/replay、Verifier验收标准、学习来源/读写时序及现有评测评分。允许为观测包scope与投影已有结果，不重构这些业务实现。不改目标仓库，不引入远程服务、额外健康检查命令或额外模型调用。

新增文件和现有文件的实际diff若超出清单，先记录必要原因与影响，并更新计划/对应验收项；不能把额外业务改动混进trace提交。

### 必选验收矩阵（当前均未执行）

| ID | 通过条件与证据 | 状态 | 未过时优先修改位置 | 归属 |
| --- | --- | --- | --- | --- |
| O01 范围 | git diff文件/符号对照清单，无未说明业务改动；保存基线与最终SHA | not_run | 本计划与超范围文件 | 全任务 |
| O02 默认及业务等价 | trace off无新文件/链路；on/off状态、证据、候选、steps/HTTP及封存包语义一致 | not_run | cli.py、observability.py、runtime.py、controller.py | T1/2/3 |
| O03 模型与成本 | 三HTTP归一次logical；只计HTTP叶子；unknown诚实；不新增调用/扣步 | not_run | model_factory.py:_GuardedModel、observability.py统计 | T1/2 |
| O04 权限与执行 | ALLOWED/DENIED/RESERVED三态可见；关联key跨步不混；权限不翻倍执行计数；真实六/七工具条件正确 | not_run | middleware.py、SDK观测middleware、renderer | T1/2/4 |
| O05 进程及健康 | 真pytest进程启动/退出/清理可关联；超时取消保留；probe缺依赖/失败可见；候选退出1不伪报环境坏 | not_run | runner.py、adapters/runtimes/local.py | T3 |
| O06 时序与隔离 | 主封存后学习在root内；预算分列；trace不入Verifier/学习材料/manifest，不覆盖task.json | not_run | controller.py、experience.py、cli.py | T3 |
| O07 边界与失效 | 并发不串线、流不改、取消不吞；容量/内存/落盘失败保留主结果；无正文/凭据泄漏 | not_run | observability.py、SDK观测middleware、cli.py | T1/2/3 |
| O08 工件与平台 | JSON/HTML可离线读；转义正确；长目录/长文件/长临时名通过；模板随wheel；健康/权限降级清楚 | not_run | paths入口、atomic_write调用、HTML模板、cli.py | T4 |
| O09 回归 | 当前完整unit/integration与pip check实际通过，记录环境、命令、通过/失败/跳过数；不借用旧结果 | not_run | 根据首个失败test定位，不降断言 | T4 |
| O10 可接受性回执 | O01–O09逐项有证据/未过修复地址；设计取舍、独立审查与修复记录可读；不存在无证据的“全通过” | not_run | docs/reviews/2026-10-09-minimal-observability-acceptance.md | T4 |

验收准则：必选项全部有可核验的pass证据才标“本地实现验收通过”；fail、未执行或关键断言仅skip都要明确列出，不能用总体通过率抵消。尚未跑的其他OS/CI/真实模型收益比较另列未验证，不声称已验证。这里的通过标准衡量观测实现，不替代ReproAgent对Bug的验收。

## 设计约定

### 用户要看到什么

1. 哪个阶段最慢：分析、探索、pytest、Verifier、重跑、导出、学习。
2. 模型共发出多少逻辑请求和实际 HTTP，重试发生在哪里，token/费用如何消耗。
3. Read/Grep/Glob/领域工具实际执行了多少次，成功、失败、权限拒绝分别在哪里。
4. 为什么结束：原始任务状态、stop_reason、候选/契约/run ID、失败节点与当时剩余步数/时间。
5. 开启和关闭 trace 时，原复现判定、候选执行次数、模型请求次数、预算扣减及封存包语义一致。
6. 系统信号与实现验收分开可见：环境/进程检查指出运行问题；验收矩阵指出功能、测试、边界哪个未过及其修复位置。

### 第一版范围

选择“本地 trace + 单页查看器”。其他方案是直接接远程平台（需部署与数据上传配置）、只看原事件日志（已有基础，但缺少工具执行耗时与完整关联）；本地方案便于先定位瓶颈。

启用命令：
```text
reproagent run --config task.json --model-config model.json --trace
reproagent trace path/to/task-output
```

`--trace` 默认关闭。第一条命令结束后生成 `observability/trace.json`；第二条离线生成 `observability/trace.html`，用户只需打开 HTML。不开浏览器服务，不使用 CDN，不自动打开窗口。

暂不做：远程平台/OTLP、实时监控、跨任务仪表盘、告警、自动优化、prompt/response/工具正文采集、历史日志补造完整 trace。旧任务没有 trace.json 时明确说明“该任务未启用 trace”，不声称历史耗时可恢复。

### 原生 hook 如何复用

已确认当前 SDK 的 `MiddlewareBase` 支持 `on_model_call`、`on_acting`、`on_check_permission` 等。项目当前 `ExplorationMiddleware` 已使用模型、权限和压缩 hooks；这次增加独立观测 middleware，不替换领域 middleware。

- `on_model_call`：外层包住模型调用链，记录 `sdk.model_round` 耗时及协议检查结果。现有领域 middleware 的 `on_model_call` 直接调用模型、不委托 next_handler，因此观测 middleware 必须注册在它之前。
- `on_acting`：原样透传 async generator，记录已获准工具的真实执行。SDK 明确这个 hook 不包含权限检查、输入校验与上下文写入。
- 权限判定：从既有 on_check_permission 的真实判定投影 permission 点事件，固定单列 `ALLOWED` / `DENIED` / `RESERVED_FOR_PUBLISHING`。DENIED 包含工具集外拒绝及 SDK deny 规则；RESERVED_FOR_PUBLISHING 是读工具被最后三步预留阻止，不能与普通拒绝合并。不得再次调用权限检查。
- 工具集必须记录实际 `Runtime.allowed_tools`，不得仅凭 docstring 写死数量。默认/disabled/store_error 或未传 experience_view 时，恰好 `TOOL_NAMES` 六个；传入有效 experience_view 时，恰好六个加 `read_experience`，共七个，顺序保持 build_toolkit 的断言。观测不新增业务工具。
- permission 与 on_acting 使用同一个 `tool_call_key`：按当前 trace_id、最近的 explore span_id（无则root）、当前探索 steps_used 及 SDK tool_call.id 生成 SHA256 前32位。只存散列，不存原始SDK ID；两个 hook 必须取同一作用域/步索引。同一 provider ID 在不同步重用时也不能误合并。缺关联信息/丢点时明确显示未配对，不按工具名或邻近时间猜配。
- 工具执行次数只统计进入 on_acting 的 tool span，每个 span_id 一次，包含成功/错误/取消的实际执行尝试。permission 三种决策分别统计检查次数，不计入 tool_executions；ALLOWED 但未进入 on_acting 不算执行。
- HTTP：在 `AgentScopeModelFactory` 的 `_GuardedModel.__call__/_attempt` 记录 `model.logical` 和 `model.http_attempt`，覆盖 exploration/contract/verdict/learning。结构化请求没有探索 Agent hooks，必须在工厂层观测。
- 工具流：只识别公开 `ToolResponse.state` 和控制异常，不读取 content。最终 response 在 yield 前标记终态，避免合法发布候选后 SDK 收尾取消被误标为工具失败。
- 不吞掉/重排/复制流事件，不多调用一次 next_handler，不修改 response、消息、工具输入、AgentState、权限或 phase gate。

典型结构：
```text
task                         # 整个 Controller.run，包含封存后学习
├─ prepare / analyze
│  └─ model.logical
│     └─ model.http_attempt
├─ explore                    # 每一轮探索
│  ├─ sdk.model_round         # SDK 调用及协议检查
│  │  └─ model.logical
│  │     ├─ model.http_attempt # 重试仍属于同一次 logical
│  │     └─ model.http_attempt
│  ├─ permission             # 三种决策的点事件，与tool共享tool_call_key
│  └─ tool.Read / tool.write_candidate
├─ controller.action          # 当前 Step：执行/确认/修订等
│  ├─ execute                 # execution_role=original/fixed；repeat是操作标签
│  │  └─ process             # started/ended/cleanup，关联原始执行结果
│  ├─ health                # 已有probe/cleanup结果，非新增检查
│  └─ verify
│     └─ model.logical
├─ export
└─ learn
   └─ model.logical
      └─ model.http_attempt
```

父子耗时会重叠，不能加起来当总耗时。只在 `model.http_attempt` 叶节点汇总实际 HTTP/token/费用；sdk.model_round、model.logical、model.completed 都不再重复收费。

### 数据、预算与失败边界

- 一个任务一个随机 32位 hex trace_id；span_id 为 16位 hex。父 ID 仅来自本任务 recorder。ContextVar 在退出时恢复，两个并发任务不串联。
- 最多1024个 span/点事件；每条完整 UTF-8 JSON 最多2048字节；trace.json 总大小最多1MiB，HTML最多4MiB。触及限制时保留已有记录、标记 partial，不中断主任务。
- span 的 start/offset 使用时间点，duration 用单调时钟。总 trace 时长与 TaskResult.duration 分列，前者含学习；学习独立预算不与主预算合并。
- 白名单属性：purpose、工具名、实际 allowed_tool_names、tool_call_key、permission_result（固定三值）、受控结果码、spec_id、exit_code、stop_reason、cleanup_ok、health_check、health_status、execution_role、candidate/run/contract ID、attempt序号、有效输出上限、预算快照、usage、费用估算及 unknown 标记。字段保留 known/unknown，不把缺 usage 或计价当精确零。
- 不记录源码、Issue 原文、prompt/response、思维链、工具参数/结果、异常原文、Authorization 或环境变量内容。已知凭据脱敏后检查元数据大小；不宣称能扫描所有秘密。
- recorder 只在内存收集；采集过程中不写 events.jsonl，不访问网络。所有观测内部错误采用固定诊断并失效关闭；领域异常、BudgetStopped、CancelledError 原样传播。
- existing TaskStore 领域事件写入失败仍按原流程处理。新观测失效不能遮盖这些原始错误，也不能声称所有日志失败都非致命。
- 观测 API 只读已有预算信息，不调用 take_step/check/reserve_cost，不新增期限或重试。
- Controller.run 返回、学习资源关闭后，CLI 写独立 trace.json；写失败只打印固定 stderr 警告，保留主 JSON/退出码。不重导出包、不重写 task.json；trace 不进入 manifest、Verifier、契约引用或学习材料。
- trace 输出目录及两个目标文件拒绝符号链接/目录联接，按解析路径确认在任务内且不在 artifacts 中；不接受自定义输出路径。
- Windows 长路径沿用 `paths.workspace_path()`：任务根与所有 JSON/HTML 文件读写入口统一转换；建目录走 `paths.directory_path()`，持久化走既有 `store.atomic_write()`，复用其 target/temp 的 shared_path_form。路径包含关系使用 `paths.is_within()`，需要位置等价时用 `identity_key()`；display_path 只用于展示，禁止用裸 Path.open/read_text/write_text、自写临时重命名或混用长/短形式绕过这些入口。
- trace 页面用任务状态/受控码说明过程，不作为新的证据或评分。HTML 严格转义；不提供任务外路径或任意文件读取。
- 若进程被硬杀，内存 trace 可能未落盘；既有领域记录仍是排障依据。MVP 不加崩溃恢复或逐事件磁盘写入。
- 截断或观测异常时 metrics_complete=false，明确统计范围；完整任务的费用仍以既有 model.attempt 记录为准。

## 文件与公共接口

Create:
- `src/reproagent/observability.py`：schema、任务级 recorder、ContextVar scope、失效隔离、统计、JSON持久化和 HTML渲染。先保持一个小模块，内部按采集/输出分区，超过清晰边界再拆。
- `src/reproagent/adapters/agentscope/observability.py`：原生 TraceMiddleware。
- `src/reproagent/resources/trace.html.template`：无外部资源的表格和 CSS 时间线；现有 package-data 已包含 resources/*.template。
- `docs/observability.md`；测试文件见任务。
- `docs/reviews/2026-10-09-minimal-observability-acceptance.md`：实现完成时才填写的过程验收回执，记录本计划O01–O10证据，不提前填通过。

Modify:
- `adapters/agentscope/runtime.py`：启用时将 TraceMiddleware 放在现有 middleware 前；默认链不变。
- `adapters/agentscope/middleware.py`：将已有权限决策的受控字段投影为观测点，不新增权限调用。
- `adapters/agentscope/model_factory.py`：_GuardedModel 的logical/HTTP边界与同源用量投影，不改原记账。
- `adapters/agentscope/tools.py`：只同步基础六工具与可选第七工具的docstring；middleware相应数量说明也同步，不改注册与权限规则。
- `core/controller.py`、`runner.py`、`core/verifier.py`、`experience.py`：仅在现有程序边界包span；Runner投影已有环境probe检查，不新增检查。
- `adapters/runtimes/local.py`：投影已有进程spawn/退出/清理结果，不增加poll、进程或清理调用。
- `cli.py`：--trace、离线 trace 子命令、非致命写失败警告。
- `README.md`：链接使用说明。

共享接口：
```text
TraceRecorder(*, clock=time.monotonic, wall_clock=time.time, secrets=())
trace_session(*, enabled: bool, secrets=()) -> ContextManager[TraceRecorder | None]
tracing_enabled() -> bool
span(name: str, *, attributes: dict | None = None) -> ContextManager[SpanHandle]
SpanHandle.annotate(**allowlisted_fields) -> None
mark(name: str, *, attributes: dict | None = None) -> None
tool_call_key(*, sdk_call_id: str, step_index: int) -> str | None
TraceRecorder.finish(*, task_id: str, status: str, main_duration: float,
                     learning: dict | None = None) -> dict
write_trace(task_dir: Path, document: dict) -> Path
render_trace(document: dict) -> str
write_trace_html(task_dir: Path) -> Path
```

关闭时 span/mark 为 no-op，不读时钟、不创建文件。所有采集 API 隔离观测错误；单独渲染命令的输入/写入错误通过 CLI 返回2。

根 schema 固定：schema_version=1、trace_id、task_id、status、main_duration、total_duration、partial、metrics_complete、warnings、spans、summary。每 span：span_id、parent_span_id、name、kind（span/event）、offset_seconds、duration_seconds（点事件为null）、status（ok/error/cancelled/incomplete）、attributes。unknown 字段使用null或明确unknown标记。根 status 严格复制 TaskResult.status；learn span 失败不将主状态 DONE 改成失败。summary 至少单列 permission_checks、permission_allowed、permission_denied、permission_reserved_for_publishing、tool_executions；tool_executions 只来自实际tool span，点事件不参与。完整trace的 permission_checks 等于三种判定数之和；partial 时注明只统计保留部分。summary.health_checks按target_environment/process_cleanup/trace_capture分列已有检查，包含name/status/result_code与适用的execution_role；每类/角色只保留一项聚合，失败优先、缺失为unknown、无观察为not_run，截断则degraded，不随进程数量无限增长；不等同TaskResult或Bug成功判定。

## Global Constraints

- 默认关闭，无额外 HTTP、模型请求、预算扣减、工具执行或 AgentState 变更。
- 同任务作用域必须覆盖主资源关闭、导出和独立学习；不同任务隔离。
- 实际 HTTP 叶节点唯一记账，完整数据不能重复或漏计。
- 观测失败不改变原任务状态、退出码、候选和已封存包；不吞领域取消。
- 不上传数据、不收集正文；只写 task-output/observability 下的独立文件。
- 1024条/2048字节每条/1MiB JSON/4MiB HTML，截断明确显示不完整。
- 保持真实工具集：无经验视图时固定六个，有有效经验视图时固定六个加 read_experience；从 Runtime.allowed_tools 投影并测试两种情况。最后三步预留、协议校验与学习30秒+收尾1秒不变。
- 权限三值分列，发布预留独立显示；按tool_call_key关联检查与执行，执行次数只计on_acting。
- 观测路径沿用 workspace_path/directory_path/is_within/identity_key/atomic_write，保留Windows长路径和临时文件同一表示不变量。
- 进程/健康信号仅来自已有执行和probe；不新建检查、额外poll或模型ping。
- 范围、验收矩阵、实测日志与回执构成过程层；未过项附修复文件/符号和复测命令，不用印象评分。
- 本计划不授权真实模型费用、部署、push 或引入远程平台。

## Review Focus

1. 既有 on_model_call 绕过 next_handler：外层 hook 仍可见调用和协议失败，不能改变预算与默认链。Task 2。
2. async generator 正常发布、错误返回与取消：流对象不变、调用恰好一次、合法发布不误标失败；权限三态/重复SDK ID正确关联，检查点不翻倍执行计数；六/七条件准确。Task 2。
3. 父子调用和 HTTP 重试：只计 HTTP 叶节点一次，unknown保留，两任务不串线。Task 1/2。
4. 学习晚于封存、进程超时/取消/清理失败、环境probe失败、观测异常和硬杀：结果与健康含义分开，写失败保留主结果；partial/incomplete诚实展示。Task 1/3。
5. 大元数据、恶意HTML文本、文件损坏、Windows目录保留窗口/跨260字符文件与长临时名：有界投影、转义、长路径入口一致，不读正文或破坏已发布包。Task 1/4。

## Task 1: 有界任务级 Recorder

**Files:** Create `src/reproagent/observability.py`, `tests/unit/test_observability.py`。
**Interfaces:** Produces 上述 TraceRecorder/trace_session/tracing_enabled/span/mark/SpanHandle/finish/tool_call_key；其他任务只依赖这些接口。

- [ ] Step 1: 写 `test_disabled_trace_is_noop`、`test_nested_spans_use_monotonic_duration`、`test_concurrent_tasks_do_not_share_parent`、`test_recorder_failure_preserves_domain_exception`、`test_caps_mark_partial_without_faking_totals`、`test_metadata_projection_excludes_content_and_credentials`、`test_tool_call_key_separates_reused_ids_across_steps`。覆盖容量精确边界、时钟倒退、未知费率和父子重复统计。
- [ ] Step 2: 跑 RED：`python -m pytest tests/unit/test_observability.py -q`，确认是缺少新行为。
- [ ] Step 3: 实现白名单、ContextVar恢复、NoopHandle、失败隔离和仅HTTP叶节点统计。内存数据有界，根/父节点 ID 合法，异常不存自由文本。
- [ ] Step 4: 跑 GREEN；期望上述测试全部通过。disabled不创建文件也不调用时钟。
- [ ] Step 5: 提交 `feat: add bounded task-local trace recorder`。

## Task 2: SDK 原生 Hooks 与 HTTP 尝试

**Files:** Create `src/reproagent/adapters/agentscope/observability.py`, `tests/integration/test_agentscope_observability.py`；Modify `src/reproagent/adapters/agentscope/runtime.py`, `src/reproagent/adapters/agentscope/middleware.py`, `src/reproagent/adapters/agentscope/model_factory.py`；只同步 `src/reproagent/adapters/agentscope/tools.py` 中描述工具数量的 docstring，不改变工具注册。
**Interfaces:** Consumes Task 1 API；Produces TraceMiddleware.on_model_call/on_acting、model.logical/model.http_attempt 与三态permission点/关联key；不更改 SDK/ModelRequest 公共接口。TraceMiddleware(context: CallContext, *, allowed_tools: tuple[str,...]) 接收现有调用上下文和已算出的工具集，只读 context.budget.steps_used；权限点在现有判定返回前投影，执行点读取同一 tool_call.id/steps_used。tool_call_key 用既有steps_used整数（可为0），无ID/有效作用域则返回None并标未配对，不能伪造关联。

- [ ] Step 1: 写真实 SDK + MockTransport 测试 `test_native_hooks_observe_without_changing_response`、`test_outer_hook_sees_protocol_rejection`、`test_denial_is_not_tool_execution`、`test_tool_stream_is_forwarded_once`、`test_published_candidate_survives_retirement`、`test_cancelled_tool_is_not_reported_success`、`test_three_http_attempts_share_one_logical_call`、`test_reserved_permission_is_not_generic_denial`、`test_permission_and_execution_share_key_without_double_counting`、`test_runtime_records_exact_toolset_with_and_without_experience`。
- [ ] Step 2: 跑 RED；覆盖一次step/三次HTTP、chunk身份和顺序、成功与ToolResponse.ERROR、disabled保持原middleware链；剩4步时读工具ALLOWED，剩3步时读工具RESERVED_FOR_PUBLISHING，SDK deny/工具集外仍是DENIED，发布工具允许。一次获准并执行产生两个关联节点但只计1次执行；拒绝/预留均计0次执行；相同工具名/SDK ID跨步重用分开计数。
- [ ] Step 3: 实现注册顺序 [TraceMiddleware, ExplorationMiddleware]；on_acting按 SDK 公开 async generator/ToolResponse 协议透传。工厂一次logical scope包住重试循环，每attempt own scope，投影 `_GuardedModel._account` 元数据。调用位置以当前 model_factory.py:166/183/198/271 为核对锚点，执行前以符号定位，不依赖固定行号。
- [ ] Step 4: 跑新增文件及 `test_agentscope_runtime.py`、`test_agentscope_domain_tools.py`、`test_agentscope_model_factory.py`、`test_agentscope_experience.py`。期望工具、发布预留、steps及原HTTP记账不变；outer/sdk/parent不重复加费用。
- [ ] Step 5: 提交 `feat: observe AgentScope hooks and HTTP attempts`。

## Task 3: 主流程关联与可选落盘

**Files:** Modify `src/reproagent/cli.py`, `src/reproagent/core/controller.py`, `src/reproagent/runner.py`, `src/reproagent/core/verifier.py`, `src/reproagent/experience.py`, `src/reproagent/adapters/runtimes/local.py`；Create `tests/integration/test_observability_lifecycle.py`。
**Interfaces:** Consumes Recorder/scope API；Produces run --trace、write_trace(task_dir, document)、根范围覆盖 Controller.run 及 learning；process span与health点投影现有RawExecution/probe结果，保留原业务角色枚举。

- [ ] Step 1: 用现有 SDK +真实pytest fixtures 写 `test_trace_on_off_preserves_domain_outcomes`、`test_main_and_learning_have_separate_budget_metrics`、`test_trace_write_failure_keeps_original_exit_code`、`test_cancelled_task_closes_trace`、`test_trace_not_in_manifest_or_learning_material`、`test_process_start_exit_and_cleanup_are_observed_without_extra_execution`、`test_timeout_and_cancel_preserve_process_result`、`test_health_reuses_existing_probe_and_marks_blocked`、`test_candidate_exit_one_does_not_fail_environment_health`、`test_unobserved_health_is_not_reported_passed`。对比状态、证据等级、候选内容、HTTP数、探索steps和资源close；不比较两个真实运行的浮点duration完全相等。
- [ ] Step 2: 跑 RED，模拟span begin/end/annotate内部错误、文件replace失败，要求原结果保留而stderr出现固定诊断。
- [ ] Step 3: CLI显式启用trace_session，Controller.run外围为root；在现有prepare/analyze/explore/Step/export、Runner.prepare/execute、Verifier.evaluate、ExperienceService.learn中包scope，原控制流顺序不变。LocalBackend.execute在已有spawn/poll/terminate_tree边界投影started/ended/cleanup与最终结果，Runner.prepare投影既有probe与准备判定。只在程序已有数据可得时填有限属性，不为观测新增读文件/进程/轮询；不把baseline或候选退出1改成环境阻塞。
- [ ] Step 4: Controller返回后 finish；JSON持久化统一 workspace_path 入口、directory_path 创建和既有 atomic_write。JSON超限partial，采集异常使metrics_complete=false。跑新文件、test_controller.py、test_cli.py、test_exporter.py、test_experience_lifecycle.py、test_local_backend.py和test_runner.py，期望trace不进证据/封存包，学习写失败及取消规则不变。
- [ ] Step 5: 提交 `feat: trace reproduction lifecycle without changing results`。

## Task 4: 离线 HTML 查看与文档

**Files:** Modify `src/reproagent/observability.py`, `src/reproagent/cli.py`, `README.md`；Create `src/reproagent/resources/trace.html.template`, `docs/observability.md`, `docs/reviews/2026-10-09-minimal-observability-acceptance.md`, `tests/unit/test_trace_rendering.py`；Extend `tests/integration/test_cli.py`、`tests/integration/test_windows_long_paths.py`。
**Interfaces:** Consumes schema1 trace.json；Produces render_trace/write_trace_html 和 `reproagent trace TASK_DIR`。路径固定，不提供 --output 或任意文件加载；另外交付由实现代理依据实测填写的O01–O10过程回执，非每个Bug任务自动生成。

- [ ] Step 1: 写 `test_trace_html_renders_failure_and_cost_breakdown`、`test_rendering_escapes_untrusted_metadata`、`test_unknown_or_partial_data_is_explicit`、`test_viewer_is_offline`、`test_missing_or_corrupt_trace_is_rejected`、`test_trace_cli_changes_only_observability_files`、`test_observability_output_cannot_redirect_to_artifacts`、`test_permission_badges_and_counts_remain_distinct`、`test_health_and_process_results_are_distinct_from_bug_verdict`、`test_trace_outputs_support_windows_long_paths_and_temp_names`。覆盖旧任务无trace、未知schema、深层JSON、超1MiB、1024条及HTML限额。长路径测试复用现有fixture，在最终observability目录248–259字符保留窗口、trace目标文件跨260字符、目标尚短但原子写临时文件跨260字符三种位置实际生成JSON和HTML；CLI从短cwd访问长task_dir，未增加子进程cwd限制。
- [ ] Step 2: 跑 RED。HTML展示层级时间线、慢节点（不把嵌套时长相加）、失败、三态权限（允许/策略拒绝/发布预留）、HTTP/token/费用、预算快照、进程生命周期、已有环境/清理健康状态、学习分项和partial提醒；按tool_call_key在同一调用行展示权限+执行结果，未配对单独注明。颜色建议允许绿、拒绝红、发布预留橙，并显示明确文字标签，不能仅靠颜色区分；没有span duration的点事件不画虚假时长。
- [ ] Step 3: 实现UTF-8单页，html.escape全部元数据；不执行脚本、不使用远程资源，不展示正文。读JSON、查文件及写HTML均走统一长路径入口与atomic_write，受控输入的readonly加载与写入错误返回2，不改任何领域文件。
- [ ] Step 4: 跑定向 GREEN，验证模板随wheel安装；文档说明启用/关闭、两条命令、数据字段、容量、失效关闭、进程/健康语义、硬杀可能无trace和远程扩展边界；按O01–O10逐项整理证据及修复位置，确认只修改计划清单内文件。
- [ ] Step 5: 设置REPROAGENT_RG_PATH后跑完整 `python -m pytest tests/unit tests/integration -q -rs` 与 `python -m pip check`。记录实际结果，不复用旧554数。一次全分支独立审查，问题按RED→GREEN修复。填写过程验收回执，列出环境、源码版本、每项命令/结果/证据、fail/not_run/skip缺口及对应修复地址，保留原失败和决策取舍。任何必选未过项不得写成“全部验收通过”；提交 `feat: add offline trace viewer and usage guide`。

## 验收与后续优化

按O01–O10矩阵逐项给结论，不能仅列一个pytest通过总数。功能失败、测试失败和边界缺口分别指出原始现象与修复文件/符号；运行时异常与实现验收失败不混为一谈。

验收是开启trace后实际跑一次离线SDK+pytest复现，看到完整模型/HTTP/工具/执行/验收/学习关系，且关闭时业务不变；不是只有mock recorder被调用。

看 trace 优先回答三个问题：读了多少次才发布测试、失败发生在工具/候选/语义验收哪一层、模型等待及重试占多少时间。用相同冻结案例和模型配置对比优化前后步骤、HTTP、有效交付和成本；trace本身不评分。

后续再按真实使用需要增加：可选脱敏输入输出、跨任务聚合、远程 OTLP/平台适配器。第一版无 TraceSink 多后端接口、队列、采样、后台flush或复杂配置；JSON schema保留parent关系和时间字段以便后续转换。

## 计划自检

- 原生hooks与领域hooks的调用顺序明确；权限允许、工具完成、HTTP成功、协议接受、最终复现五种事实分开。
- disabled不改变原调用链，正常发布后的取消、SDK重试与独立学习都有行为测试。
- 不把TaskResult.duration、父子span时长或learning分项叠加成虚假总量；费用只来自actual HTTP leaf。
- 已核对真实类名 _GuardedModel；真实工具集按experience_view分为固定六个或固定七个，docstring残留不作为事实依据。
- 权限三态分别显示，检查/执行按调用key关联；执行次数不含权限点。Windows长路径及临时名边界已有具体实现入口和测试。
- 运行时覆盖日志/trace/进程/已有健康检查，过程层覆盖范围/决策/标准/实测与未过修复位置；不新增检查服务或评分Agent。
- O01–O10均映射任务与优先修复位置，当前全部not_run；回执只在实际实现后填写。
- 新模块、公共签名、边界限额、CLI错误行为均定义；每项 Review Focus 已分配具体测试。
- 执行方式沿用当前会话逐项实现。此文档仅为待审阅计划；未改产品代码、未调用真实模型、未push。

## 2026-10-09 审查反馈处理

- 更正错误类名为 _GuardedModel，覆盖 __call__/_attempt/_account 真实边界。
- 不将所有配置误写为恰好六个：tools.py:609–613 的经验分支与 runtime.py:86 明确存在第七工具；计划定死两种条件并要求记录真实toolset。
- 补齐 workspace_path/directory_path/is_within/identity_key 与既有atomic_write入口，覆盖目录保留窗口、长文件和长临时名。
- 发布预留单列为 RESERVED_FOR_PUBLISHING；DENIED也包含SDK deny规则。
- 请求检查与实际执行用任务/探索轮次/步索引/SDK调用ID散列关联，避免ID重用误合并及执行次数翻倍。

此次仅修订计划，产品代码尚未修改。

## 2026-10-09 双层可观测性补充

补齐运行时的已有进程/环境检查信号；过程层用范围合同、执行台账、O01–O10验收矩阵和一份实现回执说明接受理由。明确功能/测试/边界未过项的定位与复测方法；不改变复现判定，不为每个Bug任务再复制一份验收文档。当前只修订计划，所有验收项未运行，产品未实现。
