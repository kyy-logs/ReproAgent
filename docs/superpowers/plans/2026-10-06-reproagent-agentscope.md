# AgentScope 模型与策略接入实施计划

> **For agentic workers:** 使用 executing-plans 在当前会话逐项执行；TDD，最终一次独立审查。

**Goal:** 原生和 AgentScope 模型/策略独立可替换，同时保持统一预算、动作验证和复现验收。
**Architecture:** Controller 注入 Explorer 工厂；可选 AgentScope 模型适配器实现 ModelGateway。AgentScopeExplorer 重用既有契约和动作校验，经真实 SDK Agent 执行单次决策，模型桥接仍调用统一 BudgetedGateway。
**Tech Stack:** Python >=3.11、AgentScope 2.0.9、pytest、httpx。
**Spec:** ../specs/2026-10-06-reproagent-agentscope-design.md。

## Global Constraints

- 原生后端默认；不安装 SDK 时 CLI/inspect/replay 正常。
- CLI model-backend 和 agent-backend 独立选择；后端实际值及 SDK 版本记录到事件。
- SDK 和底层客户端 max_retries=0；HTTP 有限重试逐次记账，费用未知不能作零。
- 取消、期限、协议纠正、原始事实、候选目录、Verifier 和冻结规则不绕过。
- 无 SDK 业务工具；每个决策独立上下文；最多一次 SDK 模型桥接调用，纠正仍由现有协议层控制。
- 保存产品接口和现有配置；历史包不改写。

## Review Focus

1. SDK 响应丢失供应商 finish_reason：从 HTTP 响应记录原始结束原因，截断不接受。
2. SDK/客户端隐式重试及 usage 缺失：禁用隐式重试，保留每次 HTTP 尝试与未知费用。
3. SDK 吞掉 CancelledError：外层 bounded 调用和取消检查确保不会重试或伪成功。
4. SDK 隐式注入、压缩或额外模型调用：空工具集、关闭注入、一次桥接限额；每个动作包含原始事实。
5. 可选依赖和四种组合：原生不导入 SDK，CLI 错误不静默回退，真实 SDK 离线执行证明。

## Task 1：后端选择与 Explorer 注入

文件：app.py、cli.py、core/controller.py；tests/unit/test_backends.py。
接口：create_controller(..., model_backend='native', agent_backend='native', explorer_factory=None)。Controller 注入 explorer_factory，默认 ReproAgent。

- [x] 写后端默认、错误选择、工厂实例和 CLI 参数失败测试，观察红灯。
- [x] 实现注入、CLI 选择、事件记录，保持原生路径通过。

## Task 2：AgentScope 模型适配

文件：adapters/agentscope/dependency.py、gateway.py；tests/integration/test_agentscope_gateway.py；pyproject.toml。
接口：AgentScopeModelGateway(config, transport=None).complete(request, context)。require_agentscope() 固定 SDK 版本并报明确依赖错误。

- [x] 写真实 SDK MockTransport 测试：参数、usage、长度结束、格式错误、限流、取消、硬费用上限和密钥脱敏。
- [x] 实现真实 SDK 调用、HTTP 观测、0 次隐式重试和逐次记账；确认请求不依赖模型网络服务。

## Task 3：AgentScope 单决策策略

文件：adapters/agentscope/explorer.py；tests/integration/test_agentscope_explorer.py。
接口：AgentScopeExplorer(gateway, context, candidate_parent)。继承原生协议实现，只将 analyze/next_action 的模型调用置于真实 SDK Agent 单次 reply 的生命周期。桥接模型将 SDK 消息转换回 ModelRequest，保留 response_kind 和输出上限。

- [x] 写真实 SDK 合成网关测试：契约来源、单动作、目录限制、共享步数、取消、状态不跨调用泄漏。
- [x] 实现无业务工具、无注入、一次桥接调用及严格回复验证。
- [x] 四种组合通过真实 pytest 原版重复执行、修复版通过、导出独立重跑。

## Task 4：完整验证与文档

- [x] 在原生环境跑完整离线测试，并在独立 SDK 环境跑真实 SDK 测试与 pip check。
- [x] 可选 SDK 安装环境验证 CLI、wheel 和缺依赖路径；原生环境检查没有 SDK 导入。
- [x] 最终独立审查，修复实际问题并保持回归通过。
- [x] 更新 README、AgentScope 使用文档、实现记录和证据回执。

## 执行记录

- 用户“实现”批准按现有设计执行，沿用当前会话逐项执行，无额外阶段确认。
- 当前目录没有 Git 仓库，在 E:\ReproAgent 工作，不创建 Git 仓库或工作树。
- 已下载官方 2.0.9 wheel 核对源码；安装测试使用独立工具环境，不改目标项目依赖。
- Ruling: 单次 Agent.reply 通过共享网关桥接，不注册业务工具 — 避免双重探索循环及隐式支出；本版不等同完整 AgentScope 自主工具循环。
- SDK 的 OpenAIChatFormatter 默认产生文本块数组，本实现转成与原生一致的纯文本消息；ModelBridge 显式提供 formatter，且关闭结构化输出回退。
- SDK 会丢失供应商 finish_reason，适配器在转换前记录并验证原值；ThinkingBlock 不作为最终协议文本。
- 额外动作及越界路径保留原有有限纠正行为，最终达到共享动作预算时为 EXHAUSTED；测试修正了错误的 NEEDS_INFORMATION 预期，产品判定规则未改。
- Claude Code 启动器新增两个后端参数，观察参数缺失 RED 后实现；5 项启动器测试通过。
- 原生无 SDK 全量：176 passed、4 skipped，111.11 秒；修复后主 .venv SDK 全量：198 passed、1 skipped，152.71 秒。真实 SDK 集成共 22 项；主工具与独立 SDK pip check 均无冲突。
- 独立审查三项 P2 分别为错误复制供应商字段、超限正文继续交给 SDK parser、压缩正文绕过读取上限。全部先复现后修复，并最终复查无遗留问题；采用 identity 请求编码、拒绝压缩响应而非实现任意解压。
- 主工具安装可选 extra；目标环境保持只有 pytest。原生组合仍默认。
- 已授权真实模型合成冒烟一次：DeepSeek + SDK 双后端 DONE / DIFFERENTIAL_VALIDATED，22.34 秒、7 次请求、21844 token、费用 unknown。原版两次失败、修复版通过，独立导出在两个新副本退出 1/0。历史 20 例未重测，未声称能力提升。
- 文档已更新；本地证据回执 `.local/agentscope-integration-verification.json`，真实报告 `repro-results/agentscope-smoke-001/artifacts/reproduction/report.md`。
