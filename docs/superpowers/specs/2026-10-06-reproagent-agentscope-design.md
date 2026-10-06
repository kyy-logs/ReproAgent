# ReproAgent 接入 AgentScope：模型与策略可替换设计

日期：2026-10-06。状态：用户批准后已实现；使用真实 AgentScope 2.0.9，模型与受控单决策策略可独立选择。实现边界和验证见 [使用说明](../../agentscope.md)。

## 1. 目标与范围

用户确认的目标是“可替换的模型与 Agent 策略，方便扩展和对比评测”。成功标准是同一任务可以选择原生或 AgentScope 后端，并共用原始问题、工具边界、预算、源码冻结和验收规则，结果可归因、可重放。

本次覆盖模型适配、探索策略适配、依赖注入、CLI 选择和后端记录。保留现有 Python/pytest、Verifier、Runner、Workspace、Store、Exporter 和独立 replay.py。Web 界面、多智能体协作、框架服务部署和 SWT-Bench 数据接入属于另行设计的扩展。

继续支持现有 DeepSeek base_url、model、输出 token 配置和 API key 环境变量；配置中不写密钥。

## 2. 方案比较与推荐

| 方案 | 收益 | 成本与限制 |
| --- | --- | --- |
| A：只接模型 | 最小改动，模型供应商可替换 | 探索策略仍固定，无法完成用户要求的策略对照 |
| B：模型与策略分别适配，Controller 继续管理执行和验收 | 能独立对照模型层与策略层，保留现有证据规则 | 需要处理 SDK 内部请求、结构化输出及取消行为；推荐 |
| C：将整个任务循环迁入 AgentScope | 可使用完整工具循环和服务生态 | 两套状态、预算和验收规则需要重新整合，难以归因成绩变化 |

已批准并实现方案 B。AgentScope 接入是可选能力，原生后端继续作为默认和对照。

## 3. 当前结构与接入点

core/ports.py 定义 ModelGateway.complete 和 Explorer.analyze/next_action。app.py 负责组装组件，Controller.run 已改为使用注入的 Explorer 工厂；现有 ReproAgent 为默认策略。

工厂在任务开始时创建策略实例，接收统一的模型网关、任务上下文和候选安装目录，保持每个任务状态独立。

```text
CLI / 应用组装
  ├── model_backend: native 或 agentscope
  │     └── 统一 ModelGateway / BudgetedGateway
  └── agent_backend: native 或 agentscope
        └── Explorer：分析问题、提出下一项动作
                    ↓
          Controller：校验和分发动作
                    ↓
          Tools / Workspace / Runner
                    ↓
          Verifier / 重复确认 / 修复版对照
                    ↓
          Exporter / report.md / replay.py
```

模型后端与策略后端独立选择，支持四种组合；策略不能决定自己的验收标准，也不能直接写最终成功状态。

## 4. 两个适配器的职责

### AgentScopeModelGateway

实现现有 ModelGateway.complete，将 ModelRequest 的 system/user 消息、输出限制和 JSON 要求映射到固定版本 SDK，再将返回结果转换为现有 ModelResponse。

只接受完整结束的有效文本或明确的结构化结果；SDK 的 INTERRUPTED、空内容和截断结果不能当成正常响应。usage 能映射时保存供应商数据，缺失时保持未知；不能通过估计 token 数冒充账单。

SDK 默认请求重试关闭，由外层适配器实行有限重试和逐次记账。底层客户端隐藏重试同样必须禁用或纳入记账；配置不支持时明确报错，不能默默重复请求。现有不支持硬费用上限的行为不变。

### AgentScopeExplorer

实现现有 analyze 和 next_action，输出继续是 IssueContract 与单个 AgentAction。原始问题、来源证据、当前契约版本、候选目录、允许动作及剩余预算都由现有任务上下文提供。

使用 AgentScope 的消息和策略能力提出下一步动作，业务工具仍由 Controller 分发。第一版不注册框架的任意 shell、文件编辑或执行工具。策略侧能够“提出执行动作”，但不能绕过 Runner、源码保护和 Verifier。

每次 next_action 只能交付一个通过现有 schema 校验的动作。SDK 的结构化输出回退、内部推理调用与纠正都必须经过统一调用门，每一次请求均计入任务期限、用量以及相应动作预算；不得形成 Controller 与 SDK 两个独立、不受限的探索循环。

每个任务创建新的策略实例，不使用跨任务共享记忆。契约修订时清除依赖旧契约的 SDK 上下文，并按最新版本重新注入原始事实。模型不能访问修复材料或隐藏判分测试。

## 5. 版本、依赖与配置

固定版本为 AgentScope 2.0.9：设计时核对该 PyPI 发行版，工具 Python 3.12 满足其 Python >=3.11 要求。[PyPI](https://pypi.org/project/agentscope/2.0.9/)

新增可选依赖 extra `agentscope`，不导入时不要求安装它。SDK 调用集中在 adapters/agentscope 下；核心模型、语言适配器和导出包不依赖 SDK 类型。原生模型与策略路径不导入 AgentScope。

已新增 CLI 参数：

```text
--model-backend native|agentscope     默认 native
--agent-backend native|agentscope     默认 native
```

现有 task.json/model.json 的字段与命令保持有效。未安装可选依赖但选择相应后端时，返回明确安装提示和配置错误退出码；不自动回退到原生后端，以免评测结果标错。

记录后端选择、AgentScope 版本、模型 ID 和请求统计，不保存密钥或原始 HTTP 响应。相关记录进入任务事件和运行元数据，后续评测可以从结果确认实际运行的组合。

官方 2.0.9 源码的 ChatModelBase.__call__ 自带请求重试；generate_structured_output 还包含多种回退策略。SDK 对取消可能返回 INTERRUPTED，这些都需要适配测试。[固定版本源码](https://github.com/agentscope-ai/agentscope/blob/v2.0.9/src/agentscope/model/_base.py)

安装及代码示例使用与固定版本匹配的文档，不混用 1.x、2.x 或 latest 开发文档。[版本索引](https://docs.agentscope.io/llms.txt)

## 6. 错误处理与预算

- SDK 加载、配置和不支持的参数错误在模型请求前显式报告，不自动安装依赖。
- SDK 内外层请求、格式纠正、契约分析和语义核验共用任务期限；有限纠正策略遵守现有最多三次尝试约束。
- SDK 内部产生的新请求要逐次显式登记；一次 Agent.reply 不等于一次模型请求。
- 取消或超时必须停止底层请求并返回原任务的 CANCELLED/EXHAUSTED，不能把 INTERRUPTED 当作 JSON 错误继续重试。
- 拒绝未知工具、越界候选路径、跳过保护或无证据提交；硬性检查失败不能由 SDK 或模型覆盖。
- 明确的语义否定不能通过协议纠正提升为成功。
- 依赖缺失、协议无效和运行失败沿用现有诊断与 stop_reason，不把所有错误合并成“复现失败”。

## 7. 验收与评测接口

先做离线契约与 SDK 集成测试，再做已授权条件下的真实模型冒烟；接入本身不承诺提升复现率。

验收至少覆盖：

1. 四种后端组合的选择和实例注入，原生默认行为保持一致。
2. SDK 2.0.9 消息、文本/结构化结果、usage、结束原因和输出长度转换。
3. 请求与输出失败时的有限纠正、取消、总期限和逐次记账，无隐藏重试。
4. 同一工具集合、候选目录限制和原始预期来源；契约修订后旧证据失效。
5. 空响应、未知动作、额外动作、格式回退和明确否定的负例。
6. 使用真正安装的可选 SDK执行离线传输/合成模型测试；不只测试替身对象。
7. 真实 pytest 的两次问题版观察、修复版对照和独立导出重跑。
8. 未安装 SDK 时原生路径与 wheel 安装仍正常，inspect/replay 不要求 AgentScope。
9. 输出报告和事件准确记录实际后端，没有凭据和修复材料进入生成上下文。

对比评测按“固定模型和输入、固定总预算、只替换策略”运行；模型后端的兼容性对照单独进行。现有20个案例用于开发回归，未见过的新案例用于正式成绩。所有尝试与失败保留，不把不同后端的最好结果合并。

## 8. 实施交付与审阅

主要涉及 app.py、cli.py、Controller 的策略工厂注入，新增 adapters/agentscope 模型/策略适配模块，以及 pyproject.toml 可选依赖、配置说明和回归测试。

用户批准设计并要求实现后，在当前会话按实施计划执行。当前目录没有 Git 仓库，没有初始化 Git 或创建工作树。实际交付和验证见使用说明与实施记录。
