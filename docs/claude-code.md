# 在 Claude Code 中使用 ReproAgent

本项目提供项目级 Skill，当前机器也已安装用户全局 Skill，命令均为 `/reproagent`。
Claude Code 调用现有 CLI，ReproAgent 使用自己的模型配置生成和核验测试。目前默认配置为 DeepSeek。
这项接入没有增加 MCP 服务，也没有把 ReproAgent 的模型后端替换成 Claude Code。
Skill 的目录和命令形式依据 [Claude Code 官方文档](https://code.claude.com/docs/en/skills)。

## 使用

当前用户的全局入口已安装到 `C:/Users/Dell/.claude/skills/reproagent/SKILL.md`。
在任意目标项目目录启动 `claude`，即可输入 `/reproagent check` 或 `/reproagent <Bug 描述>`。
未指定目标仓库时，全局 Skill 使用当前 Claude Code 的工作项目作为目标；工具解释器、模型配置和输出目录仍指向已有 ReproAgent 安装。
这是当前 Windows 用户、当前机器的全局安装。Claude Code 已打开的会话可重启以重新发现命令。
全局入口引用本项目启动器的绝对路径；移动 ReproAgent 安装目录后需更新全局入口中的路径。
全局 Skill 中没有复制工具环境或加密凭据。个人 Skill 优先于同名项目 Skill，规则见上方官方文档。

在本项目目录启动 Claude Code：

```powershell
cd E:\ReproAgent
claude
```

先在 Claude Code 输入：

```text
/reproagent check
```

检查工具解释器和 CLI 是否可用，并报告凭据来源。检查不调用 ReproAgent 的模型接口，不能证明密钥有效。
如果已有 Claude Code 会话未发现命令，重新启动该项目会话。

在另一个项目目录工作时，也可以加载本项目的 Skill：

```powershell
claude --add-dir E:/ReproAgent
```

`--add-dir` 加载额外目录中的 Skill，依据同一份官方文档；没有修改你的全局配置。

复现 Bug 可以给任务配置，也可以描述目标项目、触发输入和正确行为：

```text
/reproagent C:/work/my-project/repro-task.json
```

```text
/reproagent 帮我复现 C:/work/my-project 中 parser.parse("...") 的错误：现在返回 X，正确应为 Y；目标环境是 C:/work/my-project/.venv/Scripts/python.exe。
```

自然语言调用会准备独立 issue/task 文件；预期或环境确实不足时会询问。目标项目需要提前准备依赖和 pytest。
模型输入包括 Bug 描述和目标原版源码；提供修复版时，仅在测试冻结后用于对照验证。
目标解释器与安装 ReproAgent 的工具解释器分别配置。

读取已生成的结果：

```text
/reproagent inspect E:/ReproAgent/repro-results/expanded-final/validators-432
```

此命令只读取原有记录，不重跑测试，也不调用 DeepSeek。
Claude Code 自身处理命令可能产生其已有模型服务的用量，与 ReproAgent 的用量分别计算。

## 安装与凭据

Skill 位于 `.claude/skills/reproagent/SKILL.md`，Windows 启动器位于同目录的 `scripts/reproagent.ps1`。
启动器从项目根 `.venv/Scripts/python.exe` 运行已安装的 ReproAgent，可用 `-ToolPython` 指定其他工具解释器。
新机器应先按 README 安装本项目。目标环境依赖不会由接入脚本安装。

运行默认读取 `examples/model.deepseek.json`。如果配置要求的环境变量已存在，优先使用它。
在当前 Windows 用户下，若 DEEPSEEK_API_KEY 未设置，可在启动器内部读取已有 `.local/deepseek.key` 加密凭据。
文件位于忽略目录，没有复制到 Skill、配置或文档中；解密值只传给子进程，运行结束后恢复启动器环境。
加密文件不能保证在其他账户或机器可用。其他用户在启动 Claude Code 的终端配置模型要求的环境变量。
不要在聊天中粘贴密钥；不要把密钥写入任务 JSON 或模型 JSON。

直接调用 Windows 启动器的示例：

```powershell
powershell.exe -NoProfile -File .claude/skills/reproagent/scripts/reproagent.ps1 -Check
powershell.exe -NoProfile -File .claude/skills/reproagent/scripts/reproagent.ps1 -TaskConfig C:/work/my-project/repro-task.json
powershell.exe -NoProfile -File .claude/skills/reproagent/scripts/reproagent.ps1 -Inspect C:/work/repro-results/task-001
```

`-TaskConfig` 可追加 `-ModelConfig`、`-FixedRepo` 和 `-FixedPython`。Linux/macOS 使用安装后的 ReproAgent CLI。
也可追加 `-ModelBackend agentscope -AgentBackend agentscope`，分别选择模型与 Agent 策略，默认均为 native；安装与边界见 [AgentScope 使用说明](agentscope.md)。本机全局入口沿用同一启动器，已有默认调用继续有效。
repo、issue_file 和 output_dir 相对路径按 task JSON 所在目录解释；source_roots 和 candidate_parent 相对目标 repo。
language.python 使用目标解释器绝对路径。启动器自身的默认路径按脚本位置解释。

## 状态与验证

ReproAgent 保留原有预算、输出目录保护和退出码。每次运行选择新的 output_dir。
DONE 表示完成当前任务；只有 DIFFERENTIAL_VALIDATED 才表示给定修复版通过。
没有修复版时，可得到重复观察的证据。BLOCKED/NEEDS_INFORMATION/EXHAUSTED/FAILED 会保留诊断，不能当作成功。
生成的回归测试在原版失败属于预期结果；查看 Probe 和完整证据，而不是只看 pytest 退出码。

当前本机 Claude Code 为 2.1.268，已验证接入前 Unknown command、接入后 `/reproagent check` 能实际调用启动器。
新增启动器回归覆盖中文/空格路径、无凭据只读 inspect、check 和 CLI 配置错误退出码。
真实 Bug 调用和完整离线回归的最终结果见文末验证记录。

文件副本隔离不是安全沙箱；目标代码和生成测试具有当前用户权限。接入没有提供自动环境修复、服务启动或业务代码修复。

## 本次验证记录（2026-10-05）

- 完整离线回归：**151 passed、1 skipped（107.92 秒）**；pip check 无依赖冲突。
- 启动器 4 项回归通过，包括 PowerShell 父子版本混用及加密序列化末尾换行；该测试仅使用临时假密钥。
- 真实 Claude Code `/reproagent check` 执行成功。
- Claude Code 通过启动器调用公开 validators-432 历史案例，原有 12 动作/240 秒预算不变。
  ReproAgent 返回 **DONE / REPEATED_OBSERVATION**，耗时 32.28 秒，导出已发布。
  此次没有向 Controller 提供修复版，因此任务证据等级没有升级为 DIFFERENTIAL_VALIDATED。
- 在本地另取全新原版和修复版副本独立重跑该导出包：原版 1 个回归失败、1 个对照通过；修复版 2 个测试通过。
  同时确认 Probe 完整、测试节点一致、源码来自各自新副本、目标代码实际执行。
- Claude Code 的元数据限定 `inspect` 调用成功，报告 DONE、REPEATED_OBSERVATION 与 published，未读取源码、测试或日志。
- 子智能体只读审查未发现 Critical/Important；说明中的相对路径歧义已修正。审查没有调用模型或读取凭据。

接入调试过程中的失败没有当成成功：首次已有配置调用被多余的环境探测耗尽 headless 回合，后续一次发现 Security 模块加载问题；已分别修正调用引导和模块路径。
成功产生复现的那次 headless Claude 会话也因频繁轮询触及 6 回合上限，但其调用的 ReproAgent 完整结束；随后在独立 Claude 会话中成功读取状态。
现在 Skill 明确给长任务足够的工具等待时间。没有据此宣称所有 Claude 会话都能在相同回合数内完成。

一次 ReproAgent 实际调用共 7 次 HTTP 尝试，输入 25,768 token、输出 4,461 token；费用未配置费率，保持 unknown。
本机 Claude Code 原有模型设置没有修改；这项检查验证的是 Claude Code 宿主调用链，不是模型能力比较。
自动审批最初拦截了可能涉及源码/日志的结果读取；本地检查确认 inspect 返回仅状态元数据后，禁用 Read 工具的限定调用获准并执行成功。
本次没有把此前 20 例评估重新计数，也不新增一般复现率结论。

全局安装验证（2026-10-05）：在本项目外的 `C:/Users/Dell/Documents/Codex/2026-10-05/reproagent-global-check` 启动 Claude Code，确认没有项目级同名 Skill 后，`/reproagent check` 两回合执行成功。全局文件与安装前准备副本的 SHA256 一致；检查未调用 ReproAgent 模型。
