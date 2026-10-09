# 可观测性（本地 trace）

一次任务可以留下本地 trace，用来回答"时间花在哪、失败发生在哪一层、重试和 token 怎么消耗"。
trace 只是观测：**不增加模型请求，不改变复现结论，不进入交付包**。

## 启用与查看

```powershell
cd E:\ReproAgent
# 正常运行：默认采集，并自动写出两个文件
.\.venv\Scripts\python.exe -m reproagent run --config examples\task.json --model-config examples\model.deepseek.json

# 本次完全关闭观测
.\.venv\Scripts\python.exe -m reproagent run --config examples\task.json --model-config examples\model.deepseek.json --no-trace

# 可选：已有 trace.json 时离线重建页面
.\.venv\Scripts\python.exe -m reproagent trace outputs\<task-dir>
```

run 结束后自动写 `<task-output>/observability/trace.json` 与同目录的 `trace.html`，
直接双击打开即可，不需要第二条命令。离线子命令只在页面丢失或需要重建时用。

`--no-trace` 关闭时 `trace_session` 不创建 recorder，因此不读正文与时钟、不建文件、不改变 SDK
的 middleware 链——这是"采集前后业务一致"的前提，也是测试断言的内容。

## trace.json 里有什么

根字段：`schema_version`、`trace_id`、`task_id`、`status`（**严格复制 TaskResult.status**）、
`main_duration`、`total_duration`、`partial`、`metrics_complete`、`warnings`、`spans`、`summary`。

每个 span：`span_id`、`parent_span_id`、`name`、`kind`（`span`/`event`）、`offset_seconds`、
`duration_seconds`（点事件为 `null`）、`status`、`attributes`。

典型层级：

```text
task                       # 整个 Controller.run，含导出与学习
├─ prepare / analyze
├─ explore
│  ├─ sdk.model_round      # 一次 SDK 回复及其协议检查
│  │  └─ model.logical
│  │     └─ model.http_attempt
│  ├─ permission           # 点事件
│  └─ tool.Read
├─ verify
├─ export
└─ learn
```

三条读法上的注意事项：

1. **父子耗时重叠，不能相加。** 各 span 的时间区间互相包含，页面只显示各自的 duration，
   不会给出一个"合计"。`total_duration` 包含导出与学习，`main_duration` 是任务本身的结果时长。
2. **HTTP/token/费用只记在 `model.http_attempt` 叶节点。** 一次逻辑调用重试三次，是一个
   `model.logical` 加三个 `model.http_attempt`；费用不会被重复计三遍。
3. **`permission` 是点事件，`ALLOWED` 不等于执行。** 受控结果码有三种：`ALLOWED`、
   `DENIED`（引擎拒绝，含工具集外）、`RESERVED_FOR_PUBLISHING`（最后几步留给发布，
   读者被拒）。真正执行过的是 `tool.*` span；未注册的工具名在 SDK 动手之前就被整体拒绝，
   不会产生 `permission` 点。

缺失的数字显示为 `unknown`，不当作精确的 0；`metrics_complete=false` 时页面明确标注不完整。

## 记录边界

- **不记录正文**：源码、Issue 原文、prompt/response、思维链、工具参数与结果、异常原文都不进入 trace。
- 属性使用白名单：`purpose`、工具名、`tool_call_key`、`tool_set`、受控结果码、`execution_role`、
  candidate/run/contract ID、`attempt`、`output_limit`、`budget`、`usage`、`cost`、`unknown` 标记。
  白名单之外的键直接丢弃。
- **`tool_call_key` 是权限点与执行点的关联键**（SDK 自己的 call id）。同一工具在一轮里会被调用很多次，
  仅凭工具名或时间相邻无法判断哪次决策放行了哪次执行——尤其是其中一次被拒时。
- 每个阶段开始时记录一条 `exploration.tools`：真实注册的工具集与当时的剩余步数/时间。
  没有它，trace 说不清阶段为什么结束，六工具与七工具的任务也分不出来。
- 已知凭据在投影前脱敏；这不等于能扫描所有秘密。

## 容量与失效

| 限制 | 值 | 触及时的行为 |
| --- | --- | --- |
| span/点事件 | 1024 | 保留已有记录，标 `partial`，不中断主任务 |
| 单条属性 JSON | 2048 字节 | 整条丢弃（不截断，避免残缺事实被当成完整） |
| trace.json | 1 MiB | 写入时标 `partial` 并置 `metrics_complete=false` |
| HTML | 4 MiB | 渲染阶段**拒绝**超大页面（截断的半张时间线会被读成完整的） |
| 查看器读取 | 8 MiB | 按文件大小在读取前拒绝：被读的文件是不可信输入 |

采集只在内存进行，过程中不写 `events.jsonl`、不访问网络。观测自身出错时按固定诊断失效关闭，
**领域异常、`BudgetStopped`、`CancelledError` 原样传播**；写 trace 失败只打印一条固定 stderr
警告，保留主 JSON、导出包与退出码不变。

trace 只写在 `<task-output>/observability/` 下，两个目标文件固定、不接受自定义路径，
拒绝符号链接/目录联接。它不进入 manifest、Verifier、契约引用或学习材料。

## 已知限制

- **进程被硬杀时内存里的 trace 可能没落盘**，此时既有领域记录仍是排障依据；本版本不做崩溃恢复或
  逐事件落盘。
- 旧任务没有 `trace.json` 时，`reproagent trace` 明确报错"该任务未启用 trace"，**不会**声称
  历史耗时可恢复。
- 不做远程 OTLP/平台、实时监控、跨任务仪表盘、告警或自动优化；也不采集 prompt/response 正文。

后续若要接远程平台，JSON schema 已保留 `parent_span_id` 与时间字段，可在不改变采集点的前提下转换；
第一版不预留多后端 sink、队列、采样或后台 flush。
