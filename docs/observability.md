# 可观测性（AgentScope + OpenTelemetry 本地 trace）

一次任务会留下本地 trace：**模型实际收到什么、返回什么、工具被给了什么又给出什么**，以及系统在哪一步、
为什么接受或拒绝。trace 只是观测：**不增加模型请求与 token，不改变复现结论，不进入交付包**。

## 基础设施与链路

AgentScope 2.0.9 原生 `TracingMiddleware` 创建 Agent reply、模型调用和工具执行节点；
ReproAgent 通过其公开 hooks 增加推理周期、权限三态、真实 HTTP 尝试、预算、契约与 Verifier 检查。
原生 hooks 外的故障隔离层只保护观测错误，不重跑已调用的模型或工具。

OpenTelemetry 管理统一 trace/span ID 和父子上下文。一个任务的主流程与封存后的独立学习共享 trace；
工具调用与模型调用的层级按 SDK 实际执行关系保留，不把所有工具强行挂到模型节点下。
`trace.json` 是产品本地格式，不是 OTLP 报文；本版不发送到远程后端。

新文件使用 schema2，保留真实 OTel ID 和原始状态；旧 schema1/schema2 继续可读、可离线重建，原JSON不改写。本次新增采集健康与业务诊断字段是schema2可选扩展。
页面同时显示 OTel 原始状态与程序展示状态，例如预算正常终止或成功发布后的收尾可能被 SDK 记为 ERROR，
这与程序最终接受/拒绝结果分开。程序检查以 point 展示，point的展示ID不是OTel span ID。

每次on_reasoning形成一个推理周期；查看实际模型输入/返回解释、工具选择和中间结果。
缺失推理内容不补写，也不声明看到了模型内部完整思维。

## 使用方式

```powershell
cd E:\ReproAgent
# 正常运行：默认采集，并自动写出两个文件
.\.venv\Scripts\python.exe -m reproagent run --config examples\task.json --model-config examples\model.deepseek.json

# 本次完全关闭观测
.\.venv\Scripts\python.exe -m reproagent run --config examples\task.json --model-config examples\model.deepseek.json --no-trace

# 可选：页面丢失或需要重建时，离线从 trace.json 重新生成
.\.venv\Scripts\python.exe -m reproagent trace outputs\<task-dir>
```

每次任务只多两个文件：

```text
task-output/observability/trace.json     # 数据
task-output/observability/trace.html     # 单文件离线交互查看器
```

run 结束（主结果封存、学习资源关闭）后自动写 JSON，再由**同一份**已脱敏的 document 渲染 HTML。
不需要第二条命令；离线子命令只在重建时用。生成JSON/HTML不需要网络，离线重建也不调用模型；正常run仍会按已有模型配置请求接口。

`--no-trace` 关闭时 `trace_session` 不创建 recorder：不读正文与时钟、不建文件、不改变 SDK 的
middleware 链。这是"采集前后业务一致"的前提，也是测试断言的内容。

## 能看到什么

| 内容 | 来源 |
| --- | --- |
| 模型输入 | HTTP 请求体（httpx 已缓冲的那份），即 SDK 格式化**之后**真正发出去的内容 |
| 模型输出 | `choices[0].message` 的 `content` 与 `tool_calls`，**在阶段判定之前**取得 |
| 接口推理字段 | 提供方返回的 `reasoning_content`（缺失时才是 SDK 的 ThinkingBlock，并注明来源） |
| 工具参数 | `on_check_permission` 处已验证、已脱敏的 `tool_input` |
| 工具结果 | `on_acting` 原样透传的最终 `ToolResponse` 内容与状态 |
| 契约与验收 | 契约版本、候选/run/verdict 关联、程序检查、模型声明与最终理由 |
| SDK消息视图 | 模型输入优先通过公开on_model_call hook直接复制；其余消息从原生span投影。与wire视图区分，不把SDK视图当精确HTTP请求 |
| 原始运行日志 | 已有 run 的 stdout/stderr/probe 引用；trace **不**复制这些正文 |

**关于"思考"的边界。** 这里显示的是**接口返回的推理文本**，不是模型内部的完整思维过程，也不保证
忠实反映内部决策。**不会**为了看思考去改 system prompt、开启 `thinking_mode`、提高输出额度或追加
追问。缺失、被关闭或不支持时明确注明，**不补写**。

## 离线查看器

直接打开 `observability/trace.html` 即可，普通运行命令不变。页面的调用树与瀑布时间条保留真实父子关系，
点击节点查看输入、输出、已返回推理、工具参数与结果。工具详情通过 `tool_call_key` 关联权限参数；
`DENIED` 与 `RESERVED_FOR_PUBLISHING` 单独标注和筛选，权限检查不算额外工具执行。

搜索覆盖名称、受控属性和已保留正文，可与类型、失败筛选组合；匹配时展开祖先，清空后恢复手动折叠。
“验收”页签同时显示业务诊断链、程序检查、模型声明和既有 verdict，不新增评分。诊断项可点击跳转到原节点/正文；无JS时保留静态链接。运行提示由固定规则生成：慢步骤、
同一逻辑调用的多次 HTTP 尝试、发布预留、已记录失败位置；不让模型再次分析。

时间以记录的偏移和耗时计算，父子时长不累加；未知/非有限值不画假时间条，零耗时和点事件画刻度。
开始时间转为浏览器本地时区并标注时区。计数遵循原 summary；采集健康分别标注结构、计数和正文。正文截断不使已完整记录的token变成小计；`partial` 表示观察有实际损失，
不完整 token/费用标“已知小计”。TTFT 和缓存命中率未采集，不为显示这些字段追加请求。

HTML 内联打包的固定 JavaScript 模块和 CSS，无 CDN、服务器、外部资源或网络请求，生成与点击均不消耗模型 token。
正文每个 ID 在 HTML 只保存一次，JSON 展示模型只存引用。采集内容始终转义为文本，不能执行；
CSP 仅允许固定模块源码的 SHA-256 hash，并禁止连接、图片、iframe 等。禁用 JS 或初始化失败仍可
阅读静态概览、原生折叠正文和验收；8 MiB HTML、6 MiB JSON、Windows 路径和原子写限制不变。

## 采集边界

- **只做副本。** 采集基于拷贝，所以"记录这次请求"不可能改变真正发出去的请求。
- **脱敏**：已知模型凭据，以及 `Authorization`/`api_key`/`password`/`secret`/`cookie`/`token` 等
  标准敏感键；同时处理 JSON 转义形式（凭据嵌在 JSON 字符串里时是转义回来的）。不采集 header、
  URL 查询串或环境变量。
- **二进制只标类型**：图片/音频/附件块记为 `unsupported`，不解码、不复制。
- 正文只存本地，**不自动上传**；自由文本仍可能含其他秘密，**不声称完整秘密扫描**——需要时可
  `--no-trace` 完全关闭。
- trace 与正文**不进入** EvidenceLedger 引用，不送回 Agent/Verifier/学习，不改变候选、契约、
  证据等级或已发布包。

## 容量

| 限制 | 值 | 触及时 |
| --- | --- | --- |
| span/点事件 | 1024 | 保留已有记录，标 `partial` |
| 单条属性 JSON | 2048 字节 | 整条丢弃（不截断） |
| 元数据合计 | 1 MiB | 同上 |
| 正文条目 | 512 条 | 超出不创建记录，`content_status` 记 `omitted_limit` |
| 单条正文记录（含JSON envelope） | 256 KiB（262144字节） | 保留**标记过的头尾摘录**（只留头会让最新工具消息永远看不见） |
| 正文合计 | 4 MiB | 同上 |
| trace.json | 6 MiB | 写入编码后/读取前按大小拒绝；观测失败不影响任务 |
| HTML | 8 MiB | 渲染阶段拒绝（截断的页面会被读成完整的） |

原生OTel span另有64个属性、32个event、0个link、属性最多65536字符的限制；无法从直接hook取得的消息，若原生属性达到65536字符，标记疑似截断与未知原始长度；不猜测已经丢失了多少内容。
原生SDK在投影之前会序列化消息和异常，因此上述本地保留限额不保证瞬时序列化内存有同样上限。

`original_bytes` 是脱敏后、截断前的 UTF-8 长度；无法取得正文时为null（未知），不是0。`captured_bytes` 是脱敏摘录的 UTF-8 长度；
一律按字节而非字符计量。

**缺失不伪装。** 内容缺失分别记 `disabled` / `not_returned` / `unsupported` / `incomplete` /
`omitted_limit` / `capture_error`，各自含义不同——**已取得但被省略**和**接口根本没提供**分开。
根上还有三个独立标记：`partial`（整体不完整）、`metrics_complete`（统计覆盖）、`content_complete`
（正文）。某条推理没返回是提供方的正常行为，不算系统故障，也不会单独将partial或content_complete判为不完整。

## 失败时去哪看

| 现象 | 排查位置 |
| --- | --- |
| 模型答得不对 | `trace.html` 的调用树节点详情：`wire_request` 看它收到了什么，`provider_response` 看它返回了什么 |
| 工具调用被拒 | 时间线上的 `permission` 点（三态）+ 同 `tool_call_key` 的 `tool.*` span |
| 复现判定可疑 | 时间线上的 `check.*` 点：`provider_claim` 是模型声明，`program_check` 是程序自己的检查 |
| 环境/进程问题 | `process` span 的 `health`/`stop_reason`/`cleanup_ok` |
| 页面丢了 | 用 `reproagent trace <task-dir>` 从 `trace.json` 重建 |
| 两个文件都没写 | 观测失败只打印固定 stderr 警告，主 JSON、导出包和退出码都不受影响；领域错误仍在 `events.jsonl` |
| **进程被硬杀** | 内存中的 trace 可能来不及落盘。这是已知限制：本版本不做崩溃恢复或逐事件落盘，**既有领域记录仍是排障依据** |

## 生命周期与异常

Provider仅在启用时按需初始化一次，使用同步任务processor和会话采样器，没有后台发送队列。
任务并发按trace_id隔离，结束根span后冻结文件；迟到span不写入下一次任务。
同进程关闭任务不采样。如果宿主已配置外部provider，本版不覆盖/接管它，固定警告并关闭本次观测，
复现任务照常运行。

异常分类区分input、authentication、rate_limit、provider、transport、protocol、runtime、cancelled和unknown，
并注明来自HTTP状态、程序错误码或异常类型；不据此推断是谁的责任。
CLI解析/凭据缺失等任务开始前的错误仍使用已有CLI错误输出，不创建trace任务目录。

## 不做什么

不发送到远程平台/OTLP、不做实时监控、跨任务仪表盘、告警或自动优化；不自动回放模型请求或工具；
不新增"评分 Agent"，也不引入任意百分制——接受标准仍是既有的硬检查 + 语义核验 + 重复/可选差分。
用户审查的入口仍是既有 `report.md` 与交付包，trace 是**补充**而非第二套判定。

## 缺失原因与业务诊断链

采集健康的 `capture_health.issues` 按原因、span、来源、正文ID聚合，最多128条，超出计入issues_dropped。
原因包括 `PROVIDER_NOT_RETURNED`（正常未返回）、`CAPTURE_DISABLED`、`UNSUPPORTED_CONTENT`、
`STREAM_INCOMPLETE`、`RECORD_BYTE_LIMIT`、`CONTENT_ITEM_LIMIT`、`CONTENT_TOTAL_BYTE_LIMIT`、
`OTEL_ATTRIBUTE_POSSIBLY_TRUNCATED`、`CAPTURE_EXCEPTION`，以及结构/元数据损失。
实际损失有固定警告与可定位的原因；正文无法入库时依然记录其span/source，不生成悬空引用。
旧记录缺少具体信息则显示 `LEGACY_UNSPECIFIED` / 历史记录未提供，不能从旧partial推断具体原因。

业务诊断按已有事实显示初始契约、请求与实际修订、权限三态、发布/引用拒绝、预算快照和最终停止。
实际保存新版后才记录 `contract.revised`，字段差异来自真实before/after；请求revision本身不算修订成功。
权限允许后工具仍可能因未完成契约或未读完整引用而拒绝，这属于程序业务拒绝；与OTel状态分开展示。
诊断关系只代表同工具调用、同phase、实际契约变更或仅先后顺序，不能以时间顺序证明因果。

`budget.step_charged` 每次成功消耗探索步骤记录一次；HTTP重试不重复计步，工具调用/拒绝不额外扣步。
契约分析、verdict和学习模型调用也不标为探索步。快照展示steps_used/limit/remaining、剩余时间、已知费用和未知费用调用数。
停止维度 `steps/time/cost/cancelled` 来自程序实际预算检查分支；外部停止未知则不根据错误文案猜测。
`diagnostic_summary` 保留最终TaskResult状态、stop_reason与CLI取得的最终预算，span达到容量上限时仍可定位最终结论。

256KiB单条上限仍受4MiB总量约束；超限不保证整次会话完整。正文每个ID只存一次，trace仍只有两个文件。
thinking关闭不妨碍记录以上执行事实；记录与离线查看均不新增模型请求或token，也不补写未返回的推理。

完整性分项不会因为原生OTel的冗余event列表溢出就抹掉本地已完整保留的统计：仍记录结构损失警告；只有统计所需span/属性真的丢失或覆盖无法确认时，才把计数/用量标为不完整。最终预算快照或trace封存失败也走观测隔离边界，任务JSON、退出码与结论保持原值。


## 七步业务视图

新生成的trace.html默认打开“业务步骤”，可切换“调用树”和“验收”。七步分别为准备环境、建立契约、探索与生成候选、执行候选、验收、重跑确认、输出交付。
顶部卡片显示该步最近一次已记录的状态、耗时与次数；耗时来自对应span，不把重复尝试或嵌套span相加。卡片链接打开最近一次真实节点，下面的执行列表保留每次尝试的实际顺序。

探索、执行、验收可以反复出现；契约修订仍归入第二步，并从实际保存事件显示v1→v2。重跑中的第二次Verifier和可选修复版对照保留在第六步内，展开调用树即可查看。
“已执行”只表示操作结束，不能等同于复现通过；第五步使用已记录verdict，第六步使用程序重跑确认结果。预期pytest失败不会仅因退出码非零被改成系统异常。

新增run_candidate、replay、revise_contract业务span并标记任务的阶段边界版本；保留OTel真实ID与父子关系，无新的模型请求或token。结构完整、任务结束且包含新边界标记时，缺少的步骤才显示“未执行”。旧trace、采集不完整或任务未结束时显示“未记录”，不按执行次数猜测独立重跑。旧runner.execute仍可以导航到实际执行，轮次明确标为未知。

无JavaScript时同一七步视图与静态节点链接仍可用。此视图是观察与导航，不增加探索阶段约束，不改变预算、源码保护、模型提示词、Verifier规则或经验写入时机。
