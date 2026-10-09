# 可观测性（本地 trace）

一次任务会留下本地 trace：**模型实际收到什么、返回什么、工具被给了什么又给出什么**，以及系统在哪一步、
为什么接受或拒绝。trace 只是观测：**不增加模型请求与 token，不改变复现结论，不进入交付包**。

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
task-output/observability/trace.html     # 单页查看器，本地静态
```

run 结束（主结果封存、学习资源关闭）后自动写 JSON，再由**同一份**已脱敏的 document 渲染 HTML。
不需要第二条命令；离线子命令只在重建时用。两条命令都不需要网络，离线那条也不调用模型。

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
| 原始运行日志 | 已有 run 的 stdout/stderr/probe 引用；trace **不**复制这些正文 |

**关于"思考"的边界。** 这里显示的是**接口返回的推理文本**，不是模型内部的完整思维过程，也不保证
忠实反映内部决策。**不会**为了看思考去改 system prompt、开启 `thinking_mode`、提高输出额度或追加
追问。缺失、被关闭或不支持时明确注明，**不补写**。

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
| 单条正文 | 32768 字节 | 保留**标记过的头尾摘录**（只留头会让最新工具消息永远看不见） |
| 正文合计 | 4 MiB | 同上 |
| trace.json | 6 MiB | 读取前按文件大小拒绝 |
| HTML | 8 MiB | 渲染阶段拒绝（截断的页面会被读成完整的） |

`original_bytes` 是脱敏/截断**前**的 UTF-8 长度，`captured_bytes` 是脱敏摘录的 UTF-8 长度；
一律按字节而非字符计量。

**缺失不伪装。** 内容缺失分别记 `disabled` / `not_returned` / `unsupported` / `incomplete` /
`omitted_limit` / `capture_error`，各自含义不同——**已取得但被省略**和**接口根本没提供**分开。
根上还有三个独立标记：`partial`（整体不完整）、`metrics_complete`（元数据）、`content_complete`
（正文）。某条推理没返回是提供方的正常行为，不算系统故障。

## 失败时去哪看

| 现象 | 排查位置 |
| --- | --- |
| 模型答得不对 | `trace.html` 的 Calls 区：`wire_request` 看它收到了什么，`provider_response` 看它返回了什么 |
| 工具调用被拒 | 时间线上的 `permission` 点（三态）+ 同 `tool_call_key` 的 `tool.*` span |
| 复现判定可疑 | 时间线上的 `check.*` 点：`provider_claim` 是模型声明，`program_check` 是程序自己的检查 |
| 环境/进程问题 | `process` span 的 `health`/`stop_reason`/`cleanup_ok` |
| 页面丢了 | 用 `reproagent trace <task-dir>` 从 `trace.json` 重建 |
| 两个文件都没写 | 观测失败只打印固定 stderr 警告，主 JSON、导出包和退出码都不受影响；领域错误仍在 `events.jsonl` |
| **进程被硬杀** | 内存中的 trace 可能来不及落盘。这是已知限制：本版本不做崩溃恢复或逐事件落盘，**既有领域记录仍是排障依据** |

## 不做什么

不接远程平台/OTLP、不做实时监控、跨任务仪表盘、告警或自动优化；不自动回放模型请求或工具；
不新增"评分 Agent"，也不引入任意百分制——接受标准仍是既有的硬检查 + 语义核验 + 重复/可选差分。
用户审查的入口仍是既有 `report.md` 与交付包，trace 是**补充**而非第二套判定。
