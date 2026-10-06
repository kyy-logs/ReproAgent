# AgentScope 接入与使用

ReproAgent 可分别选择模型后端和 Agent 策略后端。当前工具环境已安装 AgentScope 2.0.9；新环境按下面命令安装可选依赖。普通安装仍以原生后端运行。

```powershell
cd E:\ReproAgent
.\.venv\Scripts\python.exe -m pip install -e ".[agentscope]"
```

## 运行

继续使用现有 task.json、model.json 和 API key 环境变量；目标项目仍需提供可运行的 Python/pytest 环境。

```powershell
.\.venv\Scripts\python.exe -m reproagent run `
  --config <task.json> --model-config examples/model.deepseek.json `
  --model-backend agentscope --agent-backend agentscope
```

两个参数均默认为 `native`，可以分别选择：

| model-backend | agent-backend | 用途 |
| --- | --- | --- |
| native | native | 原生基线 |
| agentscope | native | 单独对照 SDK 模型调用 |
| native | agentscope | 单独对照 SDK Agent 生命周期 |
| agentscope | agentscope | 两层都经过 SDK |

可继续传入 `--fixed-repo`、`--fixed-python`；修复材料只用于冻结后的验证。需要新的 output_dir，inspect 和 replay 不调用模型。选择 SDK 后端而没有安装依赖，或版本不同于 2.0.9，会明确报错。

Windows / Claude Code 启动器同样支持选择：

```powershell
powershell.exe -NoProfile -File E:/ReproAgent/.claude/skills/reproagent/scripts/reproagent.ps1 `
  -TaskConfig <task.json> -ModelBackend agentscope -AgentBackend agentscope
```

本机全局入口引用该启动器，默认仍使用原生组合。项目 Skill 已说明新参数；在 Claude Code 请求使用 SDK 时明确写出这两个后端选项。

## 实现边界

`AgentScopeModelGateway` 使用真实 SDK 的 OpenAIChatModel 调用兼容 Chat Completions 的 JSON 文本接口。支持现有 DeepSeek 配置、输出 token 上限、供应商 usage 和可选估算费率。当前没有将 SDK 的所有供应商、流式输出、多模态或工具调用暴露为产品接口。

`AgentScopeExplorer` 每次分析或动作决策创建独立的真实 SDK Agent，通过模型桥接调用共享 BudgetedGateway。每个决策最多一次模型调用，无业务工具注册、上下文自动压缩或运行状态注入。动作纠正与探索循环仍由现有协议层和 Controller 管理。这是受控的 SDK 单决策接入，后续可以经 Explorer 工厂增加不同策略；当前没有证明 SDK 策略能提高复现率。

两种后端共用契约来源、目录与动作验证、执行预算、Probe、Verifier、重复复现、修复版对照和独立导出。SDK 不能自行宣布复现成功。SDK 和底层客户端隐藏重试关闭；适配器最多三次 HTTP 尝试，逐次记账。取消和任务超时会终止调用；费用不完整时保持 unknown，仍拒绝无法保证的硬费用上限。

适配器在 SDK 转换前检查原始 finish_reason，拒绝截断、空文本、工具调用和不兼容结构。HTTP 响应限制为 1 MiB，超限停止读取并关闭响应。请求要求 `Accept-Encoding: identity`，非 identity 响应在解压前拒绝；不支持强制压缩的代理。错误信息不复制供应商响应正文或重复字段名。

## 结果与对照评测

`events.jsonl` 的 `backend.selected` 事件，以及导出 `report.json` 的 `backends` 字段，记录模型后端、Agent 后端、model ID、是否注入自定义网关和 SDK 版本。`report.md` 也展示组合，历史包保持原样。

对照评测应固定原版源码、Bug 描述、目标环境、模型配置、预算及修复版；四种组合分别使用新的结果目录，记录成功证据等级、HTTP 尝试、token、费用未知情况、耗时与失败原因。后端接通或合成样本通过不等于能力提升；历史 20 个案例尚未针对这次 SDK 接入重新评测。

验证记录见 [实现记录](implementation-status.md)，设计与实施清单见 [设计](superpowers/specs/2026-10-06-reproagent-agentscope-design.md) 和 [实施计划](superpowers/plans/2026-10-06-reproagent-agentscope.md)。
