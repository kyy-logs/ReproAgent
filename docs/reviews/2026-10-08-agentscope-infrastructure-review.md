# AgentScope 基础设施迁移独立检查：2026-10-08

结论：核心迁移已落地，离线回归通过；按实现计划的完整标准，仍有代码问题和未完成的能力验收。不能把本次结果表述为“全部 11 项已验收”或“已证明真实模型复现能力”。本次只检查并保存诊断材料，没有修改产品实现、同步主分支或发布 GitHub 评论。

## 检查版本和方法

- GitHub [PR #1](https://github.com/kyy-logs/ReproAgent/pull/1) 已合并；合并提交 `399ddc0c07867f87a8bace67fb2f0dcdfada2873`。
- 被检查代码来自 `E:\ReproAgent\.superpowers\worktrees\agentscope-infrastructure`，提交 `9b8be80843956029a37a576c642676079b7d9222`。`git diff 399ddc0 9b8be80` 无代码/文件差异，故检查覆盖合并后的 GitHub main 内容。
- `E:\ReproAgent` 当前 main 仍在 `9cf1305`，落后已记录的 origin/main 22 个提交。这里的旧代码不是本次迁移成果；本次没有执行 pull，以保留现有未提交文档。
- 对照用户提供的本地实现计划和最新迁移设计，读取 Controller、模型工厂、SDK runtime/middleware、受限文件工具、Verifier 和交付代码；不只看完成说明或测试数量。
- 重新运行完整离线套件，并另用离线 HTTP MockTransport、新临时仓库和真实 pytest 做边界探针。没有使用真实模型/API 密钥。

## 本次验证证据

在迁移 worktree 的工具 venv 中确认实际导入路径位于该 worktree，AgentScope 为 2.0.9。

```powershell
$env:REPROAGENT_RG_PATH = (Get-Command rg).Source
.venv\Scripts\python.exe -m pytest tests/unit tests/integration -q -rs
# 446 passed, 4 skipped in 389.47s (0:06:29), exit 0
.venv\Scripts\python.exe -m pip check
# No broken requirements found.
```

四个跳过都是 Windows 链接条件：`test_paths.py:137`、`test_workspace.py:48`、`test_windows_long_paths.py:482` 缺链接创建权限；`test_windows_long_paths.py:509` 的解释器没有通过链接到达。SDK 与 ripgrep 用例没有以缺依赖为由跳过。初次沙箱执行受临时目录 ACL 限制，独立创建文件也复现 PermissionError；上面的完整结果来自正常用户权限下重新执行，不把 ACL 问题计为产品测试失败。

实时核查的 GitHub [CI 第 9 轮](https://github.com/kyy-logs/ReproAgent/actions/runs/37718423340) 有 18/18 个成功任务：Windows/Ubuntu × 目标 Python 3.10/3.11/3.12 × pytest 7.4/8/9；包括测试和依赖检查步骤。

独立探针及回执位于：

- [探针脚本](E:/ReproAgent/.superpowers/worktrees/agentscope-infrastructure/.local/review-2026-10-08/probes.py)
- [本次回执](E:/ReproAgent/.superpowers/worktrees/agentscope-infrastructure/.local/review-2026-10-08/receipt.json)

## 已确认的代码问题

### P1：探索阶段无法核对原始 Issue

位置：[runtime.py:251](E:/ReproAgent/.superpowers/worktrees/agentscope-infrastructure/src/reproagent/adapters/agentscope/runtime.py:251)，上游为 [controller.py:436](E:/ReproAgent/.superpowers/worktrees/agentscope-infrastructure/src/reproagent/core/controller.py:436)。

`AgentContext` 和 runtime 的阶段消息只携带契约、反馈、历史和预算，不携带原始 Issue；文件工具只可读取原版注册快照。用户提供的外部 Issue 不在该快照时，探索 Agent 不能通过 Read 找回原文。契约里的 SourceRef 是路径/哈希/行范围，不能替代正文。

独立探针把原始 Issue 放在仓库之外并加入唯一标记，走实际 create_controller → SDK HTTP 格式化：分析请求含标记，探索请求不含标记。该探针直接证明信息丢失，不声称证明模型必然误解所有 Issue。

影响：分析把“返回一个错误对象”改述为“抛出异常”或漏掉触发条件时，探索阶段没有原文可以核对和纠正，可能围绕错误契约生成测试或提前请求用户补充信息。README 已披露，但披露没有修复运行行为。

建议：为阶段输入增加有哈希绑定的原始 Issue 正文或可读取的专用 Issue 证据入口；容量计算包含它，不能为了容纳而静默删除关键事实。补充 return/raise 语义和外部 issue_file 的 HTTP 输入回归。

### P2：导出器对 glob 得到的长路径文件没有提升路径表示

位置：[exporter.py:173](E:/ReproAgent/.superpowers/worktrees/agentscope-infrastructure/src/reproagent/exporter.py:173)、[exporter.py:39](E:/ReproAgent/.superpowers/worktrees/agentscope-infrastructure/src/reproagent/exporter.py:39)。同类直接 I/O 点包括 rglob 后的 `is_file/stat/read`，需一起检查。

父路径使用 workspace_path 不保证其 glob 子路径也经过提升。独立探针先通过产品 atomic_write 写入预检日志，再导出诊断包：日志以长路径形式读取存在，但导出器通过普通子路径 open 时报 FileNotFoundError，不能发布包。

本机实测这组固定目录结构：任务根长度 190 的对照导出通过；208/209 字符任务根导出失败，失败日志路径分别为 265/266 字符；210/211 的探针又能通过，因为路径表示在其他长度阈值被提升。这是路径表示的边界缺口，不能概括为“所有超过某个根长度都失败”。

额外真实 Controller 探针在 209 字符任务根也出现 export_state=failed，日志仍存在。它在导出前另遇到目标 pytest 长路径 collection failure，未产生有效复现；该案例仅证明诊断交付也会失败，不计为成功包验收。

建议：read/stat/is_file/glob 消费点统一提升实际子路径；同时补 248/259/260 附近文件、目录与原子临时名称的边界回归。目标 pytest 还可能自行对长路径做普通形式 I/O，完整支持不能仅靠产品写入成功来宣称。

### P2：独立 replay 在目录保留长度窗口内崩溃

位置：[replay.py:80](E:/ReproAgent/.superpowers/worktrees/agentscope-infrastructure/src/reproagent/resources/replay.py:80)、[replay.py:85](E:/ReproAgent/.superpowers/worktrees/agentscope-infrastructure/src/reproagent/resources/replay.py:85)。

replay 的 long_path 只在 260 字符时提升，但 Windows 普通目录 mkdir 在 248 字符就可能失败。导出包本身已由正常短路径任务生成并通过哈希检查；同一包对两个新的原版副本执行：

| replay 输出目录长度 | 结果 |
| --- | --- |
| 247 | pytest 实际执行，退出 1，probe.jsonl 存在 |
| 248 | mkdir 报 WinError 206，退出 1，probe.jsonl 不存在，pytest 未执行 |

不能只用退出码 1 区分复现与基础设施崩溃；这两个运行恰好都返回 1。自动核对还需要真实 probe/节点/阶段证据。

建议：独立脚本内嵌目录预留长度处理，并在安装文件父目录和输出目录使用；对子进程 cwd 的实际 OS 限制给出明确拒绝，不能退回另一个工作目录。

## 交付和验收缺口

1. **Task 11 的真实能力验收未完成。** [迁移评测](E:/ReproAgent/.superpowers/worktrees/agentscope-infrastructure/docs/evaluations/2026-10-07-agentscope-infrastructure.md) 明确写着：迁移后的 Sphinx-8801 全新任务、dev20 重跑、新 holdout 执行和官方 harness 全部 NOT EXECUTED。旧 native 轮次有真实模型及官方报告，但不能转写成新 SDK 架构的成绩。本次检查也未产生新付费模型成绩。
2. **用户提供的实现计划没有发布到仓库。** 本地是 `?? docs/superpowers/plans/2026-10-07-reproagent-agentscope-infrastructure.md`，`git ls-files` 无该文件；实时 GitHub 文件查询返回 404。PR 描述里的计划链接因此不可用；本地计划仍写“尚未开始”，任务全部未勾选。应按实际代码/测试证据更新状态，保留未执行验收步骤，不一次全勾。

## 对照计划的覆盖情况

| 任务 | 检查结论 |
| --- | --- |
| 1 模型工厂/协议 | 已实现；主依赖、提供方格式、输出限制与尝试记账有离线回归 |
| 2 受限快照后端 | 已实现；SDK 文件工具、注册清单与哈希/路径边界有回归 |
| 3 领域工具/阶段结果 | 已实现；六工具表面、候选服务、引用 ledger 和 PhaseGate 落地 |
| 4 SDK runtime/hooks | 已实现；阶段历史、20 步预算、多工具拒绝与结束/取消回归通过 |
| 5 紧凑语义证据 | 已实现；完整硬检查保留，容量/引用回归通过 |
| 6 Controller 自动推进 | 已实现；仍需补探索原始事实输入问题 |
| 7 状态/报告真实性 | 已实现；固定版 passed/failed/blocked 与重复观察分开 |
| 8 长路径/replay | 部分实现；上述 Windows 边界仍失败，不能标为完整通过 |
| 9 唯一基础设施入口 | 已实现；默认与旧参数均走 SDK，旧 native 产品执行路径退出 |
| 10 来源/对照/holdout | 代码已实现；冻结与对照测试通过，迁移新轮次尚未执行 |
| 11 整体与真实模型验收 | 离线测试/CI 通过；真实能力验收未完成 |

经验库没有进入这次迁移，符合计划范围；不把未实现的自动经验写入作为本次缺陷。

建议顺序：先补原始 Issue 输入及 Windows 导出/replay 边界，增加本次失败的回归；然后冻结新提交做一个真实 SDK 模型定点，再扩大评测；最后同步本地主分支与计划状态。每一步都以实际回执为准。
