# ReproAgent 全面迁移 AgentScope 运行时

日期：2026-10-06。状态：迁移设计，待审阅；尚未实现。

## 1. 目标与现状

用户明确要求 native 全面转向 AgentScope。目标是由 AgentScope 承担模型调用、工具注册、任务内上下文和 ReAct 探索循环，复用 Glob/Grep/Read；ReproAgent 保留 Bug 复现的领域服务和证据验收。

当前 AgentScopeExplorer 继承原生 ReproAgent，每次决策创建新 Agent、空 Toolkit、max_iters=1，并禁止工具调用。它是单步适配，不能代表完整 AgentScope 运行时。本次要替换这套探索机制，终态只有一套 AgentScope 实现。

现有 DeepSeek 接口、Python/pytest 目标环境、Claude Code 调用入口和独立复现包继续支持。迁移效果通过真实评测确认，不预先承诺复现率提高。

## 2. 架构选择

采用单任务一个持续存在的 AgentScope Agent，领域工具调用现有 ReproSession 服务。分析契约、语义核验和经验提炼使用 SDK 的结构化模型请求；不增加多个自主 Agent 协作。

另一种方案是探索、验证、学习各自建立自主 Agent 循环，但会增加上下文交接、重复调用和成功判定边界，第一版不采用。

| AgentScope 负责 | ReproAgent 负责 |
| --- | --- |
| Agent 与 ReAct 循环 | 任务状态、候选与契约版本 |
| Msg/AgentState 任务内历史 | 不可变源码快照与证据引用 |
| Toolkit 与工具 schema/调用 | 受控候选写入和真实 pytest 执行 |
| Glob/Grep/Read | 读取范围、来源哈希和命中引用 |
| SDK 模型与格式化 | 截止时间、尝试计数、费用和原始响应检查 |
| SDK 中间件/事件接入点 | 重复失败、可选修复版验证与独立交付 |

```text
CLI / Claude Code
        ↓
Controller：准备原版快照和目标环境
        ↓
SDK 结构化分析：形成有来源的 IssueContract
        ↓
AgentScope Agent + Toolkit + Msg/AgentState
        ├─ Glob / Grep / Read → 受限 SnapshotBackend
        └─ 领域工具 → ReproSession → Runner / Verifier
                                   ↓
                   重复运行 / 修复版验证 / Exporter
```

## 3. 工具复用与读取范围

使用已安装 AgentScope 2.0.9 的 Read、Grep、Glob。Read/Grep/Glob 都支持注入 BackendBase 和工具中间件；通过这些公开接口绑定原版冻结快照，不修改 SDK 源码。

SnapshotBackend 是原版快照的只读视图，负责：

- 相对路径锚定快照根，绝对路径和链接解析后仍必须在允许范围内。
- 只返回已注册、哈希一致的文件；敏感文件和隐藏修复材料不能读取。
- Glob 返回文件名；Read/Grep 命中另外生成原文件哈希与真实行范围的证据 sidecar，不能靠模型输出伪造引用。
- Read 的长行截断或 Grep 的输出限制必须明确标记；不能把未完整展示的内容误认为已经读取完整。
- SDK 搜索需要的 rg/Glob 辅助命令经后端受控执行，受截止、输出上限与清理约束；不开放通用 Shell。
- Backend 的文件系统查询采用受限原语；不把任意 SDK backend 命令直接透传给主机。

第一版只开放UTF-8文本检索，不注册SDK的Bash、PowerShell、Write、Edit等通用执行/修改工具。测试输入的写入和执行走下述业务工具，保持现有领域检查。

## 4. 业务工具与领域服务

领域工具通过 SDK Toolkit 注册，参数使用受校验的 schema：

| 工具 | 责任 |
| --- | --- |
| write_candidate | 生成新候选ID，检查目录、文件角色、不可覆盖规则和哈希 |
| run_candidate | Runner 在新副本执行，Verifier 做硬检查与 SDK 语义核验 |
| submit_candidate | 核对已验证原版失败，执行独立重复及可选修复版检查 |
| revise_contract | 只使用已读取的原版证据；升级契约并废弃旧候选验收状态 |
| request_information | 记录缺失信息并结束任务 |

从现有 Controller 的 action 分支提取 ReproSession 服务供工具调用。Controller 不再逐步请求旧JSON动作、解析 name/parameters、分发 search_code/read_file；这些调度交给SDK。

SDK自然语言结尾不构成成功。Session内的证据状态才决定DONE、BLOCKED、EXHAUSTED、NEEDS_INFORMATION等结果。没有完成submit的候选不能因为Agent说“复现成功”而导出成功包。

submit/request_information完成后发出明确的领域结束事件。运行时停止后续探索模型请求，关闭并等待SDK任务；不设置用户取消标志来伪装正常结束。SDK结束原因与领域结果分开记录，避免额外总结调用或重复执行。

## 5. 模型统一使用 SDK

AgentScope 2.0.9 成为主依赖。删除产品中的 native 模型选择与 ChatCompletionGateway 执行路径；SDK OpenAIChatModel 继续使用既有 base_url、model、api_key_env。

SDK模型工厂供探索、契约、语义核验和后续经验提炼复用。已有 ModelGateway.complete 接口可以作为领域请求边界保留，其产品实现只有SDK；测试用假的HTTP传输不构成第二套产品运行时。

复用现有适配器的HTTP边界保护与逐次用量记账，但区分请求类型：

- 探索请求允许SDK的正常tool_calls；工具名、参数JSON与允许工具集合需校验。
- 契约/语义核验/经验卡请求要求完整的结构化结果；拒绝length、空结果和非法引用。
- 明确配置DeepSeek思考模式，测试多轮工具消息是否符合提供方协议；不能继续用当前只允许TextBlock的单决策桥接。
- SDK模型/Agent重试设置为0，由一层受控重试处理暂时网络错误；所有实际HTTP尝试记账。
- 保留取消、1MiB响应限制、脱敏、不自动跟随重定向和硬费用上限不支持时明确拒绝等行为。

不使用模型自报的“费用/成功”覆盖程序记录。

## 6. 预算和 SDK 隐含行为

沿用默认20个探索决策尝试、单命令60秒、任务900秒。一次探索模型逻辑请求计一步，格式不合法的纠正也计步；网络层重试单独记HTTP。第一版每次只允许一个工具调用，多工具响应在执行前拒绝并有限纠正，避免批量调用绕过步数。

分析/语义核验继续计时和计费；它们的有限纠正单独记录，不伪装成免费调用。工具和领域服务同样检查任务截止和取消。

SDK max_iters不是唯一硬上限：已核对2.0.9的structured_output_grace_iters默认5，字段要求大于0，不能直接设0。预算中间件与模型调用入口执行独立硬检查，SDK的额外总结、结构化补救或压缩都不得绕过。

第一版关闭SDK模型压缩工具与自动压缩调用，保留SDK消息历史；上下文不足时明确停止或返回缺信息。待源引用保留和预算测试通过后，另评估压缩。业务语义核验继续使用 [紧凑证据修复方案](2026-10-06-reproagent-evaluation-repairs-design.md)，历史记忆不进入最终成功判定。

取消必须等待Runner和工具子进程清理完成；文件副本仍不构成完整OS沙箱。

## 7. 项目边界与入口迁移

| 位置 | 迁移动作 |
| --- | --- |
| app.py | 只创建SDK模型工厂、任务Session与完整SDK运行时 |
| adapters/agentscope/runtime.py | 新增持续Agent、Toolkit和结束事件协调 |
| adapters/agentscope/tools.py、snapshot_backend.py | 新增业务工具绑定及受限SDK文件工具后端 |
| adapters/agentscope/middleware.py | 新增预算、来源引用、动作审计与调用边界 |
| core/controller.py | 保留外层准备/收尾，提取领域服务，移除JSON动作循环 |
| core/agent.py、adapters/agentscope/explorer.py | 旧探索实现与单决策桥接退出产品路径 |
| core/tools.py | 原生通用搜索/读文件退出；候选/契约检查按职责归入领域服务 |
| adapters/models/provider.py | native gateway退出；必要的独立预算辅助函数移至共享模块 |
| core/verifier.py、Runner/Workspace/Exporter | 保留证据职责，模型请求走SDK |

CLI不再提供两套backend选择。过渡期旧后端参数只做弃用兼容映射，所有请求进入AgentScope并记录真实backend；不保留native回退。更新仓库内Claude launcher、评测CLI、示例和说明，确保旧默认值不能选回旧运行时。

SDK只安装在工具解释器；目标Python/pytest和独立replay不依赖AgentScope。历史任务包与native评测结果保持原样，不重写成SDK成绩。

## 8. 与已有计划的关系

先前“native/agentscope双后端”方案由本设计替代。已有修复计划中双gateway/四种组合验证需要改为单SDK路径；核验证据、结果真实性、Windows路径、环境对照和来源固定等修复仍需要保留。

经验机制继续采用 [自动记录与摘要/详情渐进加载](2026-10-06-reproagent-experience-evolution-design.md)。它尚未实现，本次仅预留SDK工具与上下文接入点，不增加ReMe/Mem0、向量库或新的自主学习Agent。

## 9. 迁移验收

1. 真实SDK + MockTransport的多轮工具轨迹完成Glob/Grep/Read、写候选、真实pytest、语义核验、重复/修复版和独立replay；不是手写下一步动作绕过SDK循环。
2. 未注册工具、跨根路径、哈希变化、候选覆盖、隐藏修复读取及伪造引用仍被拒绝。
3. 第20步、格式纠正、工具多调用、结构化补救、最终总结和SDK压缩不能触发额外探索HTTP请求。
4. 领域已完成后没有额外模型请求或重复执行；取消/超时后的所有子进程清理有证据。
5. SDK只返回成功文字、测试未执行、原版通过、语义否定或修复版失败时，不伪造有效差分交付。
6. Windows/Linux、Unicode/长路径、目标venv身份和无SDK目标环境的replay通过回归；经验接口默认空时不改变判断。
7. Claude launcher与SWT批量入口报告唯一SDK运行时。新任务真模型定点和固定清单复测，分别记录迁移/模型配置/环境变化，不合并最佳结果。

实现方式沿用用户选择：当前会话逐项执行。下一步根据本设计改写实施计划，再开展迁移；本文没有声称迁移已完成。

## 10. 已核对的依据

- 本地已安装AgentScope 2.0.9：Agent、Toolkit、Read/Grep/Glob、BackendBase、工具与Agent中间件接口。
- [AgentScope 官方仓库](https://github.com/agentscope-ai/agentscope)：ReAct、Toolkit、Context和Middleware基础能力。实际实现以固定2.0.9安装包为准，不混用旧版函数式工具教程。
- 当前ReproAgent的app.py、单决策explorer.py与Controller动作分支：上述迁移针对实际实现，不把已有适配误当完整SDK Agent。
