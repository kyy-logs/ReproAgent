# 当前设计与计划

[返回文档导航](../README.md)

这里只保留近期使用的文档路径。早期设计与实施计划已移至[历史归档](../archive/README.md)；旧链接或文件名可以通过归档索引定位。

已完成的一键接入使用说明见 [Windows 安装](../windows-install.md) 和 [AI 工具接入](../ai-tool-integration.md)；对应设计与计划文档已随 2026-10-25 清理移出。

## 上下文与失败纠偏（实验/待核对）

- [轻量踩坑草稿、审查与按需读取设计](specs/2026-10-25-experience-draft-review-design.md)及[实施计划](plans/2026-10-25-experience-draft-review.md)：探索者随候选提交草稿，现有学习调用审查，任务结束后程序写库；包含首次目录浏览与阻塞后的单次查阅提示。方案已讨论确认，计划待审阅，尚未实施。

| 文档 | 阅读注意 |
| --- | --- |
| [失败材料按需读取提示](plans/2026-10-18-failure-artifact-hints.md) | 决策点提示实验，不代表提示已证明有效 |
| [历史清理与摘要最小设计](specs/2026-10-19-history-compaction-minimal-design.md) | 早期压缩实验设计，应结合后续滚动压缩方案阅读 |
| [按 Token 触发的滚动压缩计划](plans/2026-10-21-token-triggered-rolling-compaction.md) | 滚动机制与真实模型收益应分别验收 |
| [失败后的纠偏计划](plans/2026-10-23-failure-driven-correction.md) | 以失败事实与修正行为为目标，不能用更多行动代替差分成功 |

以上文档目前在本地保留且未提交；本次不改写其任务状态、不据此宣布功能默认开启。跨分支执行前须核对实际版本和工作区。
