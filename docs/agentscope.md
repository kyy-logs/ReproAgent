# AgentScope 基础设施

ReproAgent 只有一套基础设施：AgentScope。模型调用、受限文件工具、阶段内 ReAct 执行和消息历史都由
SDK 提供，复现策略、业务编排、证据核验与独立交付仍属于 ReproAgent。没有第二个运行时可以切换，
也没有 native 备选路径可以回退。

AgentScope 是主依赖（`pyproject.toml` 的 `dependencies` 固定 `agentscope==2.0.9`），随产品一起安装：

```powershell
cd E:\ReproAgent
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

已安装 `reproagent-local` 的环境无需额外步骤。空的可选 extra `[agentscope]` 仅为一个弃用周期保留，
使旧的 `pip install reproagent-local[agentscope]` 命令仍可执行；它不安装任何额外内容。
依赖缺失或版本不是 2.0.9 时，装配阶段明确报错，不会退回其他实现：

```text
the AgentScope infrastructure requires agentscope==2.0.9; installed <version>
```

## 运行

继续使用现有 task.json、model.json 和 API key 环境变量；目标项目仍需提供可运行的 Python/pytest 环境。

```powershell
.\.venv\Scripts\python.exe -m reproagent run `
  --config <task.json> --model-config examples/model.deepseek.json
```

`--model-backend` / `--agent-backend` 是迁移前的两个旧参数，现在都已弃用且不再选择运行时：传入任一
旧值（`native` 或 `agentscope`）都会打印弃用提示并照常运行同一套基础设施，其他值直接拒绝。
Windows / Claude Code 启动器的 `-ModelBackend` / `-AgentBackend` 同样保留为弃用别名。

可继续传入 `--fixed-repo`、`--fixed-python`；修复材料只用于冻结后的验证。需要新的 output_dir，
inspect 和 replay 不调用模型。

## 一条任务里有什么

`create_controller` 是唯一装配点，它构建一个 `AgentScopeModelGateway`（领域结构化请求）和一个
`AgentScopeExplorer` 工厂（探索阶段）。两者都从同一份模型配置与同一个任务 store 构建
`AgentScopeModelFactory`（每条路径各有自己的工厂实例，探索模型按任务创建），所以每条模型路径都经过
同一套有界 HTTP 尝试循环、响应检查与记账规则；阶段守卫由运行时装在探索模型上，因此只有探索请求计
探索步数。注入自定义 gateway/explorer 只用于测试与自定义策略，记录会标为 `not_recorded`/`custom`，
不会冒充产品自身的基础设施。

- **契约分析**：`analyze` 通过 gateway 发出一个结构化请求（无 tools、`response_format=json_object`）。
- **探索阶段**：`AgentScopeExplorer` 每个任务创建一个 `AgentScopeRuntime`，由 SDK `Agent` 在自己的
  `AgentState` 里跑 ReAct 循环。同一个任务的所有阶段共用这段历史；每个阶段开始前绑定当前契约版本。
  阶段输入同时携带冻结的原始 Issue 正文与哈希，包括位于仓库外的 issue_file；先校验原始字节哈希，
  再脱敏已知 API key 后发送。契约是解释，Agent 可对照原文核对返回/抛出和触发条件；原文与契约合计
  超过输入容量时明确停止，不能删去原始事实后继续。修订契约不改变这份原文。
- **业务推进**：阶段结果交回 Controller。候选随即在原版执行、被语义核验，并在复现时独立重复一次；
  给出 `--fixed-repo` 时再在固定版执行同一候选。没有"是否执行"或"是否提交"的模型决策。
- **交付**：导出与 replay 不依赖 SDK，也不需要 AgentScope。

## 阶段工具面

一个探索阶段只注册六个工具，顺序固定：

| 工具 | 作用 |
| --- | --- |
| `Read` | 读取注册快照中的文件，行号格式与 SDK 相同 |
| `Grep` | 用 ripgrep 搜索注册快照，只在 `content` 模式下显示整行 |
| `Glob` | 在注册快照内做文件名匹配 |
| `write_candidate` | 发布一个候选并结束本阶段 |
| `revise_contract` | 引用本阶段显示过的原版行，申请契约修订 |
| `request_information` | 报告无法继续所缺的信息并结束本阶段 |

不注册 Bash/PowerShell/通用 Write/Edit，也不注册 `run_candidate`/`submit_candidate`。文件工具的
`BackendBase` 由 `SnapshotBackend` 实现：只服务冻结清单里的文件，拒绝写入和删除，只执行 SDK 自己
发出的两种程序形状（ripgrep argv 与 SDK 的 `_glob_helper.py`），并拒绝该名字触发的敏感文件读取。
其他文件系统查询从清单回答，因此冻结后写入快照的文件不可见，字节变化的注册文件在每次读取时被拒。

Grep 需要主机上的 ripgrep（`shutil.which("rg")`）。没有 ripgrep 时搜索被明确拒绝为"无法运行"，
而不是报"无匹配"；`REPROAGENT_RG_PATH` 只被测试用来指定一个已知的二进制位置。

## 证据与阶段结果

工具响应受 `tool_response_bytes`（默认 32768）限制，被截断时带明确标记；一个文件工具把它完整显示
过的行写进响应的 `<evidence>` 边车，只有这些行可以被 `revise_contract` 引用。Read 一行最多
2000 字符，Grep 因 ripgrep `--max-columns 500` 最多 500 字节：被任一工具截断的行不是完整原版行，
不是证据。引用由 `EvidenceLedger` 签发，`Verifier.resolve` 再次核验，伪造的路径、哈希或行号没有
可以附着的对象。

阶段结果由领域工具通过 `PhaseGate` 产生，一个阶段只产生一个：先到者生效。`write_candidate` 只发布
不可变候选并给出真实 id，它不执行候选、也不是判定。SDK 的自然语言结尾不产生阶段结果，也不会成为
成功；模型说"已复现"而没有任何工具调用时，这个阶段是 `no_candidate`，任务以诊断结束。

## 预算、权限与压缩

一次探索模型逻辑请求计一步（默认 20）；网络重试是同一逻辑请求的另一次 HTTP 尝试，不额外计步，
单逻辑请求最多 3 次 HTTP。单命令 60 秒，任务 900 秒，HTTP 响应上限 1 MiB，只接受
`Accept-Encoding: identity`。契约分析和语义核验不占探索步数，但计时间、HTTP、token 和费用。

阶段运行在 `DONT_ASK` 权限模式：没有用户可以回答确认，因此领域工具的权限决策是 `ALLOW`（`DENY`
规则仍然优先），`EXPLORE` 模式会拒绝非只读工具，阶段不应在其中运行。一次响应最多一个工具调用，
整份响应在任何一项执行前就被检查：多于一个调用、未知工具名或非法 JSON 参数都会被整份拒绝，并按
固定的协议码在有限次尝试内纠正。

自动压缩被显式阻止：`on_compress_context` 在上下文达到阈值时以 `NEEDS_INFORMATION` 停止阶段，
而不是用摘要替换携带来源引用的历史。SDK 的 grace、总结与压缩都不能绕过预算。

## MockTransport 与真实模型

离线测试用 `httpx.MockTransport` 走完整链路：探索 wire 携带 tools 且每个阶段工具都真实执行，
领域请求是无 tools 的结构化请求，Controller 的执行业务步骤不经过模型。这些测试证明的是产品行为
（边界、预算、证据、装配），**不是模型能力**，也不能用来宣称复现率提升。

真实模型的定点与冻结轮次的执行状态见 [基础设施迁移评测](evaluations/2026-10-07-agentscope-infrastructure.md)；
历史验证记录见 [实现记录](implementation-status.md)。
