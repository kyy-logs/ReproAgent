# ReproAgent 文档导航

先按要完成的任务找文档。日常使用指南放在本目录，近期设计与实验计划放在 `superpowers/`，早期设计、验收和评测材料统一放在 [archive/](archive/README.md)。

当前文档是功能入口，不是最新分数公告；各报告中的版本、样本、测试数量和能力结论只对其记录的运行成立。

## 使用与理解

| 想了解什么 | 文档 |
| --- | --- |
| 安装、任务配置和 CLI 快速开始 | [中文 README](../README_zh.md) / [English README](../README.md) |
| Roadmap 与更新日志 | [ROADMAP](../ROADMAP.md) · [NEWS](NEWS.md) |
| 一键接入 Claude Code / Codex | [AI 工具接入](ai-tool-integration.md)（当前接入分支指南，验收状态以执行代理报告为准） |
| Claude Code 调用方式与原有项目级入口 | [Claude Code 使用说明](claude-code.md) |
| 架构、AgentScope 与业务 Controller 的职责 | [AgentScope 基础设施](agentscope.md) |
| report.md、证据等级、交付包与 replay | [技术亮点与输出](technical-highlights-and-output.md) |
| trace 的输入输出、工具调用与诊断链 | [可观测性](observability.md) |
| 经验什么时候读写、分类与存储 | [经验库](experience.md) |
| 数据集准备、运行和官方评测口径 | [SWT-Bench](swt-bench.md) |

## 设计、问题与案例

- [当前设计与计划入口](superpowers/README.md)：仍保留的上下文、失败纠偏和经验草稿实验方案。计划存在不等于已实现，实验记录也不等于默认启用。
- [问题与修复索引](reports/问题与修复索引.md)：按具体问题查找根因、修复和验证边界。
- [问题与挑战记录](reports/reproagent-问题与挑战记录.md)：项目遇到的问题、约束和取舍，继续在原文维护。
- [validators-432 报告示例](reports/validators-432-reproduction.md)：阅读复现证据与交付报告的历史案例，不代表一般成功率。

## 历史记录

统一从 [归档索引](archive/README.md) 查找，索引保留了旧路径与新位置的对照：

| 分类 | 内容 |
| --- | --- |
| `archive/specs/` | 早期架构、基础设施、经验与反馈设计 |
| `archive/plans/` | 早期实施计划与阶段修复任务 |
| `archive/reviews/` | 独立审查、验收回执与诊断依据 |
| `archive/evaluations/` | 冻结版本的历史评测、样本和结果 |
| `archive/legacy-guides/` | 旧实现进度、机器迁移、兼容性与早期冒烟/复测记录 |

以下材料因本地仍在修改或尚未提交而保留原路径，不将它们当作最新验收：

- [SymPy-15678 第四轮官方结果](reviews/2026-10-15-sympy-15678-round-004-official-result.md)

## 后续文档如何放

1. 使用方法更新现有主题指南，避免再写一份同义入口。
2. 尚在执行的 spec/plan 放在 `superpowers/specs`、`superpowers/plans`，明确状态与配套文档。
3. 已退出当前工作范围的阶段设计/计划、验收和评测移到归档对应分类，保留版本与证据边界，并更新此入口和归档索引。
4. 不把个人机器路径、旧测试总数或单次案例成绩写成产品的长期默认承诺。
5. 保留具体问题、根因、解决过程和未决项；重复进度汇报、过时机器步骤可删除，独有踩坑先合并到问题索引。已完成且不再被引用的设计/计划可以从当前目录清理；仍在执行或实验中的方案保留。
