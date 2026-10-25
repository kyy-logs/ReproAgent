# Roadmap

> Direction, not commitments. The current release is a local CLI MVP; experiments are not default features.
>
> 方向，不是承诺。当前版本是本地 CLI MVP；实验性工作不等于默认功能。

## Now / 现在

- Local Python/pytest reproduction with differential validation and sealed evidence packages.  
  本地 Python/pytest 复现，包含差分验证和封存证据包。
- Windows one-click install (Python discovery/auto-install, Claude Code / Codex entries, read-only project preflight) and macOS CLI install.  
  Windows 一键安装（Python 发现/自动安装、Claude Code / Codex 入口、只读项目预检）和 macOS 命令行安装。
- Claude Code / Codex user-level Skills and a thin launcher around the existing CLI.  
  Claude Code / Codex 用户级 Skill，以及围绕现有 CLI 的薄启动器。
- Local trace inspection and an experience library with task-scoped catalog browsing.  
  本地 trace 查看器，以及按任务浏览目录的经验库。

## In progress / experiments / 进行中

- Bounded failure-artifact hints at decision points.  
  在决策点提供有界的失败材料提示。
- Minimal history compaction (default off) and token-triggered rolling compaction.  
  最小历史压缩（默认关闭）和按 Token 触发的滚动压缩。
- Failure-driven correction: compare the previous failed attempt with the new candidate.  
  失败驱动纠偏：比较上一次失败尝试和本次候选。
- Experience draft review and explicit draft decision/fallback.  
  经验草稿审查，以及显式草稿决定和兜底。
- Current repair work around experience cards/reminders and replay signature stability.  
  当前的经验卡片/提醒修复和 replay 签名稳定性修复。

## Next validation / 下一阶段验证

- A real clean Windows machine for winget / signed-installer / UAC / network validation.  
  在真实干净 Windows 机器上验证 winget、官方签名安装包、UAC 和网络路径。
- Larger unseen holdout evaluation and official harness reruns.  
  更大规模的未调试保留集评测和官方 harness 复跑。
- Independent human review of reproduction evidence.  
  对复现证据进行独立人工审查。
- Real-model A/B evaluation of the experience library.  
  对经验库做真实模型 A/B 评测。

## Out of scope / 暂不支持

- xdist, browser automation, automatic dependency repair, database reset, external-service startup, resume and parallel candidate search.  
  xdist、浏览器自动化、自动依赖修复、数据库重置、外部服务启动、resume 和多候选并行搜索。

## Links

- [Current designs and plans](docs/superpowers/README.md)
- [SWT-Bench evaluation](docs/swt-bench.md)
- [Project status](README.md#project-status)
- [Issues and fixes](docs/reports/问题与修复索引.md)
