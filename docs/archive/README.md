# 历史文档归档

[返回当前文档导航](../README.md)

本次整理于 2026-10-24。原归档 77 份材料；已清理 3 份重复摘要及用户指定的 8 份归档记录；2026-10-25 再清理 6 份无当前引用的历史验收，现保留 60 份归档文档。独有教训已整理到[问题与修复索引](../reports/问题与修复索引.md)。归档不代表每个计划都已完成或每个方案都已失效；它表示这些材料退出当前操作入口，仍可用于追溯设计与证据。

旧设计可能包含 native/AgentScope 双后端、早期经验检索方式或过时的“尚未实现”状态；当前使用方式从文档导航进入。报告的样本、环境、冻结版本、失败记录和不确定性保持原样，不合并计算新成绩。

## 分类

| 分类 | 数量 | 用途 |
| --- | ---: | --- |
| [早期运行与环境记录](#legacy-guides) | 6 | 见下方文件与旧路径对照 |
| [历史设计](#specs) | 16 | 见下方文件与旧路径对照 |
| [历史实施计划](#plans) | 21 | 见下方文件与旧路径对照 |
| [审查与验收回执](#reviews) | 12 | 见下方文件与旧路径对照 |
| [历史评测](#evaluations) | 5 | 见下方文件与旧路径对照 |

## 文件与旧路径对照

早期文档里的代码块、命令和纯文本历史路径没有批量改写；遇到旧路径可按下面表格定位。这里只更正可点击链接，避免把历史执行命令改成今天的命令。

<a id="legacy-guides"></a>
### 早期运行与环境记录

| 文档 | 整理前路径 |
| --- | --- |
| [DeepSeek 首次真实模型冒烟记录](legacy-guides/deepseek-smoke.md) | `docs/deepseek-smoke.md` |
| [扩展案例修复与复测（2026-10-05）](legacy-guides/expanded-case-repairs.md) | `docs/expanded-case-repairs.md` |
| [历史案例扩展验证（2026-10-05）](legacy-guides/expanded-case-validation.md) | `docs/expanded-case-validation.md` |
| [历史 Bug 验证问题修复与复测](legacy-guides/historical-case-fixes.md) | `docs/historical-case-fixes.md` |
| [三个历史 Bug 的真实模型验证](legacy-guides/historical-case-validation.md) | `docs/historical-case-validation.md` |
| [实现与验证记录](legacy-guides/implementation-status.md) | `docs/implementation-status.md` |

<a id="specs"></a>
### 历史设计

| 文档 | 整理前路径 |
| --- | --- |
| [ReproAgent 项目工程架构](specs/2026-10-05-reproagent-architecture-design.md) | `docs/superpowers/specs/2026-10-05-reproagent-architecture-design.md` |
| [ReproAgent：Bug 复现 Agent 架构与优化路线](specs/2026-10-05-reproagent-design.md) | `docs/superpowers/specs/2026-10-05-reproagent-design.md` |
| [ReproAgent 接入 AgentScope：模型与策略可替换设计](specs/2026-10-06-reproagent-agentscope-design.md) | `docs/superpowers/specs/2026-10-06-reproagent-agentscope-design.md` |
| [ReproAgent 以 AgentScope 为基础设施](specs/2026-10-06-reproagent-agentscope-runtime-migration-design.md) | `docs/superpowers/specs/2026-10-06-reproagent-agentscope-runtime-migration-design.md` |
| [ReproAgent 评测失败修复设计](specs/2026-10-06-reproagent-evaluation-repairs-design.md) | `docs/superpowers/specs/2026-10-06-reproagent-evaluation-repairs-design.md` |
| [ReproAgent 踩坑经验最小版设计](specs/2026-10-06-reproagent-experience-evolution-design.md) | `docs/superpowers/specs/2026-10-06-reproagent-experience-evolution-design.md` |
| [ReproAgent：SWT-Bench Lite 评测接入设计](specs/2026-10-06-reproagent-swt-bench-design.md) | `docs/superpowers/specs/2026-10-06-reproagent-swt-bench-design.md` |
| [ReproAgent AgentScope 原生观测迁移设计](specs/2026-10-09-reproagent-agentscope-observability-migration-design.md) | `docs/superpowers/specs/2026-10-09-reproagent-agentscope-observability-migration-design.md` |
| [分类经验目录与按需加载设计](specs/2026-10-12-experience-catalog-design.md) | `docs/superpowers/specs/2026-10-12-experience-catalog-design.md` |
| [Refusal experience trigger coverage](specs/2026-10-12-experience-rejection-triggers-design.md) | `docs/superpowers/specs/2026-10-12-experience-rejection-triggers-design.md` |
| [经验写入与情境检索设计](specs/2026-10-12-experience-trigger-retrieval-design.md) | `docs/superpowers/specs/2026-10-12-experience-trigger-retrieval-design.md` |
| [探索前首次经验目录浏览设计](specs/2026-10-13-experience-first-browse-design.md) | `docs/superpowers/specs/2026-10-13-experience-first-browse-design.md` |
| [契约范围与批量引用反馈设计](specs/2026-10-14-contract-scope-citation-feedback-design.md) | `docs/superpowers/specs/2026-10-14-contract-scope-citation-feedback-design.md` |
| [执行诊断与重复超时停止设计](specs/2026-10-14-execution-diagnosis-recovery-design.md) | `docs/superpowers/specs/2026-10-14-execution-diagnosis-recovery-design.md` |
| [探索步骤预算软收敛设计](specs/2026-10-14-exploration-budget-convergence-design.md) | `docs/superpowers/specs/2026-10-14-exploration-budget-convergence-design.md` |
| [Verifier 失败反馈与修复决策设计](specs/2026-10-14-verifier-failure-feedback-design.md) | `docs/superpowers/specs/2026-10-14-verifier-failure-feedback-design.md` |

<a id="plans"></a>
### 历史实施计划

| 文档 | 整理前路径 |
| --- | --- |
| [ReproAgent MVP Implementation Plan](plans/2026-10-05-reproagent-mvp.md) | `docs/superpowers/plans/2026-10-05-reproagent-mvp.md` |
| [AgentScope 模型与策略接入实施计划](plans/2026-10-06-reproagent-agentscope.md) | `docs/superpowers/plans/2026-10-06-reproagent-agentscope.md` |
| [ReproAgent 真实评测失败修复 Implementation Plan](plans/2026-10-06-reproagent-evaluation-repairs.md) | `docs/superpowers/plans/2026-10-06-reproagent-evaluation-repairs.md` |
| [SWT-Bench Lite Implementation Plan](plans/2026-10-06-reproagent-swt-bench.md) | `docs/superpowers/plans/2026-10-06-reproagent-swt-bench.md` |
| [ReproAgent AgentScope Infrastructure Implementation Plan](plans/2026-10-07-reproagent-agentscope-infrastructure.md) | `docs/superpowers/plans/2026-10-07-reproagent-agentscope-infrastructure.md` |
| [ReproAgent Experience MVP Implementation Plan](plans/2026-10-08-reproagent-experience-mvp.md) | `docs/superpowers/plans/2026-10-08-reproagent-experience-mvp.md` |
| [ReproAgent Dual-Layer Observability Implementation Plan](plans/2026-10-08-reproagent-minimal-observability.md) | `docs/superpowers/plans/2026-10-08-reproagent-minimal-observability.md` |
| [Reviewed Boundary Repairs Implementation Plan](plans/2026-10-08-reviewed-boundary-repairs.md) | `docs/superpowers/plans/2026-10-08-reviewed-boundary-repairs.md` |
| [ReproAgent AgentScope Observability Migration Implementation Plan](plans/2026-10-09-reproagent-agentscope-observability-migration.md) | `docs/superpowers/plans/2026-10-09-reproagent-agentscope-observability-migration.md` |
| [ReproAgent Interactive Trace Viewer Implementation Plan](plans/2026-10-10-reproagent-interactive-trace-viewer.md) | `docs/superpowers/plans/2026-10-10-reproagent-interactive-trace-viewer.md` |
| [ReproAgent Observability Diagnostics Repair Implementation Plan](plans/2026-10-10-reproagent-observability-diagnostics-repair.md) | `docs/superpowers/plans/2026-10-10-reproagent-observability-diagnostics-repair.md` |
| [ReproAgent Pitfall Learning Input Implementation Plan](plans/2026-10-11-reproagent-pitfall-learning-input.md) | `docs/superpowers/plans/2026-10-11-reproagent-pitfall-learning-input.md` |
| [ReproAgent 分类经验目录与按需加载 Implementation Plan](plans/2026-10-12-experience-catalog.md) | `docs/superpowers/plans/2026-10-12-experience-catalog.md` |
| [Refusal Experience Triggers Implementation Plan](plans/2026-10-12-experience-rejection-triggers.md) | `docs/superpowers/plans/2026-10-12-experience-rejection-triggers.md` |
| [Experience Trigger Retrieval Implementation Plan](plans/2026-10-12-experience-trigger-retrieval.md) | `docs/superpowers/plans/2026-10-12-experience-trigger-retrieval.md` |
| [Experience First Browse Implementation Plan](plans/2026-10-13-experience-first-browse.md) | `docs/superpowers/plans/2026-10-13-experience-first-browse.md` |
| [ReproAgent Contract Scope and Citation Feedback Implementation Plan](plans/2026-10-14-contract-scope-citation-feedback.md) | `docs/superpowers/plans/2026-10-14-contract-scope-citation-feedback.md` |
| [Execution Diagnosis and Recovery Implementation Plan](plans/2026-10-14-execution-diagnosis-recovery.md) | `docs/superpowers/plans/2026-10-14-execution-diagnosis-recovery.md` |
| [ReproAgent Exploration Budget Convergence Implementation Plan](plans/2026-10-14-exploration-budget-convergence.md) | `docs/superpowers/plans/2026-10-14-exploration-budget-convergence.md` |
| [Verifier Failure Feedback Implementation Plan](plans/2026-10-14-verifier-failure-feedback.md) | `docs/superpowers/plans/2026-10-14-verifier-failure-feedback.md` |
| [Pytest-8906 / 5221 Repair Implementation Plan](plans/2026-10-15-pytest-8906-windows-temp-path.md) | `docs/superpowers/plans/2026-10-15-pytest-8906-windows-temp-path.md` |

<a id="reviews"></a>
### 审查与验收回执

| 文档 | 整理前路径 |
| --- | --- |
| [AgentScope 原生观测迁移验收回执](reviews/2026-10-09-agentscope-observability-migration-acceptance.md) | `docs/reviews/2026-10-09-agentscope-observability-migration-acceptance.md` |
| [踩坑学习材料验收](reviews/2026-10-11-pitfall-learning-acceptance.md) | `docs/reviews/2026-10-11-pitfall-learning-acceptance.md` |
| [经验卡片 4 KiB 预算修复验收](reviews/2026-10-12-experience-card-budget-acceptance.md) | `docs/reviews/2026-10-12-experience-card-budget-acceptance.md` |
| [分类经验目录与按需加载验收](reviews/2026-10-12-experience-catalog-acceptance.md) | `docs/reviews/2026-10-12-experience-catalog-acceptance.md` |
| [Refusal experience trigger acceptance](reviews/2026-10-12-experience-rejection-triggers-acceptance.md) | `docs/reviews/2026-10-12-experience-rejection-triggers-acceptance.md` |
| [经验写入与情境检索验收](reviews/2026-10-12-experience-trigger-retrieval-acceptance.md) | `docs/reviews/2026-10-12-experience-trigger-retrieval-acceptance.md` |
| [学习字段契约与诊断验收](reviews/2026-10-12-learning-validation-errors-acceptance.md) | `docs/reviews/2026-10-12-learning-validation-errors-acceptance.md` |
| [首次经验目录浏览验收](reviews/2026-10-13-experience-first-browse-acceptance.md) | `docs/reviews/2026-10-13-experience-first-browse-acceptance.md` |
| [评测修复合并验证](reviews/2026-10-14-evaluation-repairs-merge-verification.md) | `docs/reviews/2026-10-14-evaluation-repairs-merge-verification.md` |
| [执行诊断与重复超时停止：验收记录](reviews/2026-10-14-execution-diagnosis-recovery-acceptance.md) | `docs/reviews/2026-10-14-execution-diagnosis-recovery-acceptance.md` |
| [探索步骤预算软收敛：验收记录](reviews/2026-10-14-exploration-budget-convergence-acceptance.md) | `docs/reviews/2026-10-14-exploration-budget-convergence-acceptance.md` |
| [Verifier 失败反馈：实施与验收记录](reviews/2026-10-14-verifier-failure-feedback-acceptance.md) | `docs/reviews/2026-10-14-verifier-failure-feedback-acceptance.md` |

<a id="evaluations"></a>
### 历史评测

| 文档 | 整理前路径 |
| --- | --- |
| [ReproAgent 真实 Bug 评测：2026-10-06](evaluations/2026-10-06-swt-development.md) | `docs/evaluations/2026-10-06-swt-development.md` |
| [AgentScope 基础设施迁移评测：2026-10-07](evaluations/2026-10-07-agentscope-infrastructure.md) | `docs/evaluations/2026-10-07-agentscope-infrastructure.md` |
| [ReproAgent 真实 Bug 评测：2026-10-07（修复后轮次）](evaluations/2026-10-07-swt-repaired.md) | `docs/evaluations/2026-10-07-swt-repaired.md` |
| [AgentScope 单运行时架构真实验收：2026-10-08](evaluations/2026-10-08-agentscope-acceptance.md) | `docs/evaluations/2026-10-08-agentscope-acceptance.md` |
| [冻结保留集 10 例的 issue 类型标注：2026-10-09](evaluations/2026-10-09-holdout-case-types.md) | `docs/evaluations/2026-10-09-holdout-case-types.md` |

## 已删除的过时或重复摘要

设计/实施方案与包含具体根因、修复或诊断证据的记录继续保留。只清理以下三份；它们的独有问题与未决项见[问题与修复索引](../reports/问题与修复索引.md)。

| 原文件 | 删除原因 | 保留内容 |
| --- | --- | --- |
| `legacy-guides/compatibility.md` | 旧兼容矩阵和依赖说明已被使用指南、CI 排查覆盖 | 偶发 CI 失败及尚未定位的边界 |
| `legacy-guides/migration-to-e.md` | 个人机器迁移步骤、复制统计和旧路径操作 | venv/启动器重建、绝对路径与 junction 的迁移教训 |
| `reviews/2026-10-11-experience-episodes-main-integration.md` | 合并状态与补测计数重复于功能验收 | 受限执行环境下的验证边界；功能验收原文仍保留 |

2026-10-25 又清理了 6 份没有当前引用的历史验收：`experience-mvp-verification`、`dual-layer-observability-acceptance`、`interactive-trace-viewer-acceptance`、`contract-scope-citation-feedback-acceptance`、`evaluation-protocol-diagnostics-acceptance`、`pytest-temp-assertion-acceptance`。这些早期回执的独有内容已由当前文档和问题索引覆盖；原文保存在本机 `repro-results/docs-cleanup-20261025/archive/reviews/`，不作为公开仓库必备文件。

## 原有缺失材料

初版 MVP 计划曾引用 `2026-10-05-reproagent-review.md`，该文件未收录在本次工作区；相应位置已改为缺失说明，不补造审查结论。

## 撤销整理

旧新路径一一对应。需要恢复某份文件时按上表反向移动，并同时修正引用。完整整理前快照、文件哈希和链接改动清单保存在本机 `repro-results/docs-cleanup-20261024/`（本地诊断材料，不作为公开仓库必备文件）。
