# 独立检查后的修复记录：2026-10-08

用户要求修复三个已确认问题，并拉取最新代码到本地。修复从 GitHub 合并提交 `399ddc0` 开始，在已有 worktree 的 `fix-reviewed-boundaries` 分支实现。

## 修复内容

1. **原始 Issue 进入探索上下文。** Controller 在每个阶段传入冻结的原文与哈希；SDK runtime 验证哈希、脱敏已知 API key，再把完整原文与契约计入输入容量。位于仓库外的 issue_file 也可见，契约修订不替换原始事实；超限停止，不静默截断事实。
2. **导出长路径文件不遗漏。** 在 glob/rglob 之前提升 Windows 遍历根，在实际文件读取、stat/is_file 时使用可访问的路径表示；清单和报告仍使用相对路径。回归覆盖 208–211 字符任务根，既检查“能导出”，也检查已有日志确实进入包且哈希一致。
3. **独立 replay 处理目录保留窗口。** 输出目录和安装父目录使用目录专用表示；测试验证 248/259 字符输出目录下真实 pytest 原版失败、修复版通过，probe 完整。超过可启动 cwd 长度时在安装前明确拒绝，不用其他目录代替。

文件 I/O 和进程 cwd 分开：Windows CreateProcess 计算实际传入字符串长度，给原本可启动的 255–258 字符 cwd 加上前缀反而会失败。进程使用已解析的普通绝对目录，文件 I/O 使用必要的扩展表示。新增端到端回归证明 255/258 字符 cwd 真正启动 pytest 并记录 session_start/session_finish；这不保证目标依赖自身的长路径文件操作成功。

## 测试与审查

- 原始 Issue 的四个新场景先失败，再通过；SDK runtime/backends/explorer/Controller 定点 **57 passed**。
- 导出边界四个场景先失败（两个报错、两个静默漏日志），修复后 Exporter/报告/边界定点 **31 passed**。
- replay 四个场景先失败，修复后长路径/Exporter/wheel 定点 **30 passed、2 skipped**（原有 Windows 链接条件）。
- 独立全分支审查发现 cwd 表示问题；255/258 两个新端到端场景先复现 WinError 267，再修复，replay 定点 **6 passed**。
- 审查发现的文档引用缺口由本记录及 implementation-status 修复段落补齐，没有延期的 Minor。
- 最终完整套件：**460 passed、4 skipped，392.51 秒，exit 0**；`pip check` 无冲突。4 个 skip 是原有 Windows 链接权限/解释器链接条件，无 SDK 或搜索测试因缺依赖跳过。包含审查后的 cwd 补修，不复用上一轮 458 passed 的结果。

执行命令：

```powershell
$env:REPROAGENT_RG_PATH = (Get-Command rg).Source
.venv\Scripts\python.exe -m pytest tests/unit tests/integration -q -rs
.venv\Scripts\python.exe -m pip check
```

## 执行裁定与边界

- 复用现有 worktree 和新修复分支，保留用户的本地未提交文档与原 worktree；不删除或覆盖其他代理的材料。
- 原 11 项迁移已实现，按用户确认的三个检查问题做局部修复；不重复迁移、不重写旧 native 评测记录。
- 遍历根须预先提升路径形式；只提升遍历返回的子路径会漏掉尚未枚举成功的深目录，因此补充 shared extended_path helper。风险由实际日志/清单完整性测试覆盖。
- 目标解释器继续保留 venv 链接身份；进程 cwd 绝不回退到别处；过深的 cwd 明确拒绝。目标 pytest/依赖自己的长路径限制仍可能阻止执行。
- 复用刚观察到的定点 GREEN 证据记账，代码更改后重新跑完整套件，避免无变化时重复昂贵验证。
- 本次未调用真实模型、未执行新的 Linux/多 Python CI。历史 CI 和能力评测不冒充本修复的验证结果；真实模型能力验收仍需单独冻结版本并执行。
- 最新 GitHub main 已拉取；最终验证后将本修复带回本地 main，刷新本地主环境。没有自动 push。
- 本地整合已完成：`E:\ReproAgent` 与修复分支的已提交树一致，主目录实际解释器的关键冒烟 **11 passed**（23.81 秒），再次 pip check 无冲突；可编辑安装的主依赖包含 AgentScope 2.0.9。原有未提交设计文档与拉取前备份的 SHA-256 一致，其他用户文档保留。此后收尾提交仅更新计划/记录，不改变已测试的产品代码。
