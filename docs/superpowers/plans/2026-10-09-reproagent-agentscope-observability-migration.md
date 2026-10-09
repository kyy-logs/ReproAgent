# ReproAgent AgentScope Observability Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in the current session. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有自建通用追踪迁移到AgentScope原生TracingMiddleware与OpenTelemetry，保留默认本地采集、可读页面及ReproAgent业务验收信息。

**Architecture:** OTel提供唯一trace/span上下文；原生middleware提供Agent/model/tool节点，补充reasoning和已有业务span/event。同步任务processor把原生数据投影到有界、脱敏的本地sink，门面API和JSON/HTML交付保持稳定；生产不运行旧/新双套追踪。

**Tech Stack:** Python≥3.11、AgentScope==2.0.9、opentelemetry-api==1.45.0、opentelemetry-sdk==1.45.0、httpx.MockTransport、pytest。第一版不增加OTLP exporter/后端服务。

**Spec:** `docs/superpowers/specs/2026-10-09-reproagent-agentscope-observability-migration-design.md`。用户直接要求写迁移计划，本次交付设计与计划，不执行产品迁移。

**Baseline:** main `cd309c685a1591820330fef78893eb4641a68bd9`；实施前重新核对HEAD和并发改动。旧计划及其已完成验收记录保留，不能把旧结果改成新迁移通过。

## Global Constraints

- 普通run默认采集并自动生成observability/trace.json与trace.html；只有--no-trace关闭，无新增开启flags/配置文件/服务启动命令。
- 一个任务一个根OTel trace，包含学习；主时长/总时长、独立学习预算保持。只有OTel管理span上下文和ID，不保留第二套_current_span/uuid树。
- 原生TracingMiddleware不得被自建同名类冒充；不可修改site-packages、monkey-patch SDK/OTel私有函数或为测试重置全局provider私有状态。
- 不改变prompt、formatter调用数、模型配置/输出限额、工具集、权限三态、最后三步预留、验收短路/结论、原始日志/工件与学习来源。
- HTTP请求输入/响应复用已安全取得的数据，不新增read/aread或网络请求；不增加模型/工具/pytest/健康检查调用，观测错误不触发主任务重跑。
- 1024展示span/点；metadata每条2048字节、合计1MiB；512正文、单条含信封32768字节、合计4MiB；JSON6MiB、HTML8MiB。原生SDK临时序列化开销不伪称被这些保留限额约束。
- SpanLimits=64 attributes/32 events/0 links/65536字符属性值；丢弃/疑似截断须标partial。根计入1024。正文只本地保存，命名/异常文本也脱敏。
- schema2新写、schema1/2双读；OTel原始status与程序展示状态分开；未知token/cost保持unknown，只有HTTP叶子汇总费用。
- 走现有workspace_path/directory_path/atomic_write/is_within/identity_key，JSON→HTML独立原子写，失败仅固定警告不改变主退出码。
- 已有宿主provider不覆盖/不shutdown/不附加exporter；本版观测不可用但任务继续。本项目provider进程复用，任务结束只注销sink。
- 本次不接远程平台/自动OTLP上传、跨服务传播、自动回放、硬杀恢复或评分Agent；只执行离线测试，不自动push。

## Review Focus

1. Provider全局只允许设置一次：多任务/并发/宿主provider/--no-trace混用不能串trace或擅自上传；Task 1测试。
2. 原生middleware异常序列化与异步生成器close：不得替换领域异常、重复执行handler或把发布成功显示失败；Task 2/3测试。
3. 原生默认序列化不等于安全投影：SDK资源、异常栈、长内容、未知名称和原始call ID均不能漏到文件；Task 1/2测试。
4. SDK和wire双视图、logical/retry层级：协议拒绝内容仍可见，token/费用/执行次数不能倍增；Task 2/3测试。
5. 全局provider、旧schema和新ID影响安装包/重建/Windows：测试进程隔离，不污染历史数据，父节点省略如实显示；Task 4/5测试。

## 变更范围

Create:
- `src/reproagent/otel_backend.py`：provider/采样器/同步processor、根生命周期、trace路由。
- `src/reproagent/otel_projection.py`：原生字段白名单、安全投影/schema2与已有ContentStore适配。
- `tests/unit/test_otel_backend.py`, `tests/unit/test_otel_projection.py`。
- `tests/integration/test_agentscope_native_tracing.py`, `tests/integration/test_otel_observability_lifecycle.py`。
- `docs/reviews/2026-10-09-agentscope-observability-migration-acceptance.md`（实施完成时创建）。

Modify:
- `src/reproagent/observability.py`：兼容门面、OTel-backed handle，删自建上下文/ID/父子树；保留文件路径、摘要及对外接口。
- `src/reproagent/observability_content.py`：允许必要SDK来源、沿用脱敏/完整序列化限额。
- `src/reproagent/adapters/agentscope/observability.py`：缩为ReproTraceMiddleware，reasoning span与业务字段；不重复native模型/工具span。
- `src/reproagent/adapters/agentscope/runtime.py`：注册顺序；`middleware.py`：现有permission补充字段的绑定点。
- `src/reproagent/adapters/agentscope/model_factory.py`：仅切换现有span/capture到统一门面，保留重试/守卫/wire逻辑。
- `src/reproagent/cli.py`：结束根再finish及观测降级警告；`trace_rendering.py`：schema1/2、原始/展示状态与调用分组。
- `pyproject.toml`：显式添加两个OTel固定版本依赖；`README.md`, `docs/observability.md`。
- 现有test_observability/test_agentscope_observability/test_observability_decisions/test_observability_content/test_trace_rendering/test_observability_lifecycle/test_observability_end_to_end/test_cli/test_windows_long_paths：更新底座断言并保留功能回归。

原则上不改controller.py/core/verifier.py/runner.py/local.py/experience.py的业务调用；如门面迁移迫使调整，仅做签名适配并在回执列出。不改复现策略、模型协议/预算/官方评测或目标项目。

## 共享接口及数据合同

保留 `trace_session(*, enabled=True, secrets=())`、`tracing_enabled()`、`span(name, *, attributes=None, expected=())`、`mark(name, *, attributes=None)`、`capture(kind, value, *, source, availability='captured')`、`record_check(name, value, *, source='program_check')`、`tool_call_key(*, sdk_call_id, step_index)`、`write_trace(task_dir, document)`。

新内部接口：
```text
ensure_local_provider() -> ProviderActivation
ProviderActivation: available: bool, warning: str | None
TaskTraceSink(*, secrets=(), clock=time.monotonic, wall_clock=time.time)
TaskSpanProcessor(SpanProcessor): on_start(span, parent_context=None), on_end(readable_span), force_flush(timeout_millis=30000)->bool, shutdown()->None
SessionSampler(Sampler): should_sample(...)->SamplingResult
open_task_trace(sink: TaskTraceSink) -> ContextManager[TaskTraceSession]
TaskTraceSession.close() -> None  # 幂等：结束根、结束路由；不关闭provider
TaskTraceSink.finish(*, task_id, status, main_duration, learning=None) -> dict
project_native_span(readable_span, *, sink: TaskTraceSink) -> None
ReproTraceMiddleware(context, *, allowed_tools)
```

TaskTraceSession.close结束根后sink才finish；trace_session退出也必须幂等close。sink内部状态由锁保护；不依赖on_end时的ContextVar。provider在无会话时DROP；采集processor回调失败自身捕获、标degraded，不传播到SDK。底座创建失败yield None并固定警告，业务只执行一次。

schema2保留schema1的spans/contents/summary与展示字段，增加root_span_id；真实span条目增加otel_span_id、instrumentation_scope、otel_status、otel_start_time_ns、otel_end_time_ns；metadata attributes允许error_category/classification_source。展示status保持ok/error/cancelled语义。kind=point的展示ID不是OTel ID，otel_span_id=null；序号来自宿主event序列。缺失父节点不改原parent，viewer显示未保留父节点。

原生SDK消息内容来源为sdk_agent_input/sdk_agent_output/sdk_model_input/sdk_model_output，工具沿用sdk_tool_input/sdk_tool_result；wire_request/provider_response/provider_reasoning仍是优先来源。仅允许SDK已存在字段，未知/不支持内容标availability，不补写。SDK属性达到65536字符时保守标截断；不把残缺JSON当可解析完整消息。

## Task 1: OTel底座与安全投影

**Files:** Create otel_backend.py/otel_projection.py 与两unit测试；Modify observability_content.py、pyproject.toml。
**Interfaces:** Produces ProviderActivation、TaskTraceSink、TaskSpanProcessor、SessionSampler、open_task_trace、project_native_span。

- [ ] Step 1: 写 `test_provider_reused_without_global_reset`、`test_foreign_provider_disables_local_trace_without_touching_host`（独立子进程）、`test_parallel_sessions_route_end_by_trace_id`、`test_disabled_after_enabled_is_not_sampled`；断言同任务32/16位hex、不同任务不同trace、迟到结束不写下次sink、无网络exporter/后台线程。
- [ ] Step 2: 写 `test_native_projection_redacts_names_status_and_exception_events`、`test_native_content_sources_and_limits`、`test_dropped_sdk_attributes_mark_partial`；用已知秘密/JSON转义秘密、原始call ID/未知工具名、SDK resource、超限中文正文断言无泄露，既有1024/512/字节边界不变。
- [ ] Step 3: 跑 `.venv/Scripts/python.exe -m pytest tests/unit/test_otel_backend.py tests/unit/test_otel_projection.py -q` 确认RED指向未实现合同，而非fixture坏掉。
- [ ] Step 4: 实现上述接口；owned全局provider只装一次，同步processor安全投影、monotonic记时、异常隔离；加两个固定OTel依赖。不引入SDK私有patch或Batch队列。
- [ ] Step 5: 同命令GREEN，运行pip check核对固定版本兼容；提交 `feat: add task-scoped OpenTelemetry tracing backend`，只stage本任务文件。

## Task 2: 门面切换及原生AgentScope调用树

**Files:** Modify observability.py、adapters/agentscope/observability.py、runtime.py、middleware.py；Create test_agentscope_native_tracing.py；Update test_observability.py/test_agentscope_observability.py。
**Interfaces:** Consumes Task 1；Produces兼容门面与ReproTraceMiddleware，原生model/tool节点、补充reasoning周期。

- [ ] Step 1: 写 `test_native_agent_model_tool_spans_are_real_sdk_spans`：使用真实SDK+MockTransport，断言instrumentation_scope=agentscope的reply/model/tool来自原生类，trace_id等于任务根；reasoning每次on_reasoning一次。测试不得用fake middleware冒充native。
- [ ] Step 2: 写 `test_permissions_correlate_without_duplicate_tool_spans`：ALLOWED/DENIED/RESERVED_FOR_PUBLISHING与实际native tool同key、permission不计执行，6/经验7条件不变；被整份模型回复拒绝时没有假工具节点。
- [ ] Step 3: 写 `test_publish_generator_close_preserves_terminal_result`、`test_cancelled_stream_has_no_context_leak`、`test_native_observer_failure_never_replays_handler`：流对象原样一次yield、取消/GeneratorExit不替换业务异常、下次trace不串树。必须实测SDK序列化故障；若原生序列化异常会改变业务结果且公开扩展点无法隔离，标阻塞并改设计，不能关闭相关断言或重跑handler掩盖。
- [ ] Step 4: 跑相关新增和既有middleware/runtime测试RED。
- [ ] Step 5: 门面改OTel-backed，删旧ID/parent engine；原生TracingMiddleware外层，ReproTraceMiddleware只补reasoning/业务关联/终态，关闭时不注册。将通用节点投影命名规范化，原始SDK名另保留脱敏展示，终态依据仅来自既有程序结果。
- [ ] Step 6: 上述测试GREEN；保留全套旧内容/流/权限断言，新增原生证据；提交 `refactor: use native AgentScope tracing for agent calls`。

## Task 3: 业务链路、异常与单次记账

**Files:** Modify model_factory.py（门面适配）、otel_projection.py/observability.py；Create test_otel_observability_lifecycle.py；Update test_observability_lifecycle.py/test_observability_decisions.py。
**Interfaces:** Consumes统一当前OTel span；Produces四purpose、HTTP leaves、程序event与异常分类；不修改Verdict格式。

- [ ] Step 1: 写 `test_all_purposes_and_learning_share_one_trace`：contract/exploration/verdict/learning实际节点共享根，学习在seal后且预算独立，root状态等于TaskResult，不由learning异常覆盖。
- [ ] Step 2: 写 `test_retry_usage_is_charged_only_at_http_leaves`：一logical三HTTP时原生model token只展示不累加；丢失usage/价格时unknown及已知小计保持；协议拒绝前provider_response仍可见，wire实际messages/tools保持不变。
- [ ] Step 3: 写 `test_error_categories_preserve_native_and_domain_status`：400/401/429/503/transport/protocol/进程timeout/取消/unknown；otel_status保留，正常PhaseEnded/预算终止有受控display status；缺因果来源不猜client/server责任。
- [ ] Step 4: 写 `test_program_checks_keep_short_circuit_and_evidence_isolation`：check事件数量等于实际执行，bool原样返回、重复hash/resolve为零，provider_claim与程序验收分开；trace正文不进入Agent/Verifier/学习/manifest。preflight错误仍无任务trace且CLI错误行为不变。
- [ ] Step 5: 运行新增及controller/runner/local/verifier/experience/model_factory回归RED；按现有调用点最小适配、投影受控reason_origin/错误来源，不增检查；运行同组GREEN。
- [ ] Step 6: 提交 `refactor: correlate domain decisions and HTTP attempts with native traces`。

## Task 4: 双版本文件、页面与自动输出

**Files:** Modify cli.py/trace_rendering.py/observability.py；Update test_trace_rendering.py/test_cli.py/test_windows_long_paths.py。
**Interfaces:** Consumes schema2；Produces schema1/2双读、新写schema2、保持write_trace_html(task_dir, document)。

- [ ] Step 1: 写 `test_schema1_page_rebuild_remains_compatible`、`test_schema2_displays_native_domain_and_reasoning_links`：旧fixtures能读/重建、原始JSON不覆写；工具内容按调用分组、两状态/来源可见、point ID不冒充span ID。
- [ ] Step 2: 写 `test_missing_parent_and_late_span_are_explicitly_partial`：限额省略父节点/SDK截断诚实显示；root关闭在finish前、重复close无重复记录，active丢失节点不伪造完成。
- [ ] Step 3: 写 `test_default_run_writes_both_files_without_flags`、`test_disabled_run_does_not_initialize_tracing`、`test_html_failure_keeps_json_and_task_exit`；保留6MiB读限、8MiBHTML拒绝、注入、链接重定向、长路径临时文件、JSON写失败不生成HTML断言。
- [ ] Step 4: 运行render/CLI/Windows相关组RED；实现schema分派、纯文本显示、close→finish→JSON→HTML时序。无自启动浏览器/网络资源，不让错误内容变成链接。
- [ ] Step 5: 同组GREEN，独立wheel安装验证OTel显式依赖及template可用；提交 `feat: preserve local trace inspection across the OpenTelemetry migration`。

## Task 5: 等价回归、使用说明与验收回执

**Files:** Update test_observability_end_to_end.py、README.md、docs/observability.md、本计划状态；Create docs/reviews/2026-10-09-agentscope-observability-migration-acceptance.md。
**Interfaces:** Consumes完整迁移；Produces实测回执，非模型评分/第二套任务报告。

- [ ] Step 1: 写 `test_native_trace_and_disabled_run_are_behaviorally_equivalent`：真实SDK+pytest，固定随机ID/时钟输入后比较HTTP字节/次数、工具输出、预算、候选/证据/结论；运行包括拒绝/预留/重试/缺推理/collector失败的场景。无额外token与请求，正常run双文件、关闭无文件。
- [ ] Step 2: 跑端到端RED→GREEN，记录旧行为对照来源与允许变化（schema版本/节点ID/树层级/增加native视图），不要求非固定真实耗时相同。
- [ ] Step 3: 使用文档说明原生与业务分工、返回推理边界、默认命令、原生瞬时序列化限制、foreign provider降级、schema1/2兼容、未覆盖preflight/硬杀/远程平台。双层观测继续采用计划/检查表/实测回执。
- [ ] Step 4: 冻结当前版本/依赖，设置REPROAGENT_RG_PATH后跑 `.venv/Scripts/python.exe -m pytest tests/unit tests/integration -q -rs` 和 `.venv/Scripts/python.exe -m pip check`；保存实际输出到 `.local/agentscope-observability-migration-verification/`。用新结果，不能引用旧通过数字。
- [ ] Step 5: 按requesting-code-review技能做一次全分支独立审查，修复Important/Critical再跑必要全套；回执保留首轮失败及修复。review不代替运行测试。
- [ ] Step 6: 填下面矩阵并提交 `docs: verify native AgentScope observability migration`。无证据/skip必选关键断言只能not_run，未验证CI/平台/真实模型性能另列。

## 验收矩阵与修复定位

| ID | 必须满足 | 当前 | 未过时改哪里 |
| --- | --- | --- | --- |
| M01 底座真实 | native AgentScope spans、OTel唯一trace、无旧自建parent引擎 | not_run | runtime.py/observability.py/otel_backend.py |
| M02 上下文 | 根/并发/迟到/关闭任务隔离；foreign provider不触碰 | not_run | provider/sampler/processor/session |
| M03 内容 | wire+SDK视图/工具结果/返回推理来源诚实，所有落盘字段脱敏有界 | not_run | otel_projection.py/ContentStore/model_factory.py |
| M04 周期决定 | reasoning周期、工具选择/中间结果及程序判定可关联，无补写思考 | not_run | ReproTraceMiddleware/renderer |
| M05 异常 | SDK原状态与业务结果并列，分类有来源，生成器与observer异常不改变主任务 | not_run | native集成/门面/projection |
| M06 计数预算 | HTTP重试叶子一次记账；权限三态/工具执行无双计，6/7条件保持 | not_run | summary/model_factory.py/permission投影 |
| M07 验收学习 | 短路/证据/封存/学习/独立预算不变，trace不反馈模型 | not_run | 门面/生命周期集成 |
| M08 输出兼容 | 默认双文件、--no-trace、schema1/2、父节点缺失、长路径/wheel/失败降级 | not_run | CLI/rendering/paths调用/依赖 |
| M09 行为回归 | 两模式请求/工具/pytest/预算/结果等价；本版全测试及pip check实际通过 | not_run | 按首个失败test定位 |
| M10 接受依据 | 改/不改范围、命令/结果/证据/失败定位/重测及review齐全 | not_run | 验收回执 |

每项回执字段：expected/status/command/observed/evidence_ref/fix_location/recheck_command。迁移不以“trace文件存在”或测试数量作为通过标准。只有M01–M10必选检查有实测证据才宣称本地验收完成。

## 自检与执行交接

已检查设计覆盖、接口名称、schema与原生ID、两类状态、原生生命周期全局约束、现有8MiBHTML限额；五个Review Focus都有指定测试。新依赖与原生序列化故障需实施时实测，不提前声明兼容或测试通过。

当前全任务未执行，产品代码未修改。执行方式沿用用户此前选择的当前会话逐项执行；先审阅本文及设计，再开始迁移。不默认push。
