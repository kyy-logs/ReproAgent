# ReproAgent 经验库 MVP 验收（2026-10-08）

按 [实现计划](../superpowers/plans/2026-10-08-reproagent-experience-mvp.md) 完成五项任务。使用说明：[experience.md](../experience.md)；配置示例：[task.experience.json](../../examples/task.experience.json)。规格：[经验演化设计](../superpowers/specs/2026-10-06-reproagent-experience-evolution-design.md)。

## 交付

- 固定 framework/model/workflow 三分类和短标签；工具侧 JSON 库、旁路系统锁、原子追加与精确去重。
- 每任务固定一次快照；分析后首轮探索展示最多三条摘要，SDK 按需成功读一次详情；可见经验 JSON 合计最多 2048 字节。
- 主资源关闭、导出完成、最终 TaskResult 保存之后同步提炼；独立 SDK 客户端与上下文，工作预算 30 秒，资源关闭最多额外 1 秒。新经验仅供后续任务。
- 原版材料冻结到内部 learning/evidence.jsonl，程序生成 ID 与引用。模型选择材料 EID，程序自动验证写入，无人工核对。
- report.md/report.json 只增实际读取 ID；CLI 单列学习结果、用量和事件保存状态。
- SWT 评测支持 --experience-file，只读冻结库，检测漂移、停止后续 case 并保留分母；整体用量包含学习一次，分项不再相加。

## 验证

工具环境 Windows、Python 3.12、pytest 9.1.1、AgentScope 2.0.9。全部使用 SDK + MockTransport；没有调用付费模型。

修复后最终命令：

```text
REPROAGENT_RG_PATH=<installed rg>
.venv/Scripts/python.exe -m pytest tests/unit tests/integration -q -rs
.venv/Scripts/python.exe -m pip check
```

结果：**554 passed、5 skipped，658.36 秒；No broken requirements found。** 五个跳过项均为 Windows 链接创建权限或解释器非链接入口。完整套件包含真实 pytest、进程清理、SDK 调用、wheel 安装与独立 replay。

验证了 A 学习/B 读取、只读原版/修复版差分、当前证据否定历史建议、取消零学习请求、预算耗尽后的独立学习、锁竞争、原子写失败、库容量与 UTF-8 上限、材料隔离及封存结果稳定。

第一次完整回归出现两个旧评测 Controller 缺少可选字段的失败；已修复默认读取兼容，保留初次失败记录。审查前第二轮为 549 passed、5 skipped；最终结果含新增的五项审查回归。

## 独立审查与修复

一次全分支、全新上下文、只读 gpt-6-astra 审查，范围 dbc5782..136a672。无 Critical，无 Minor；两项 Important 均在单次修复阶段完成：

1. 低于容量上限的深层损坏 JSON 会使可选库的 RecursionError 逃逸并打断主任务。共同读取边界归一化错误；单测和真实 SDK 主任务均先失败再通过，旧库字节不变，主任务继续 DONE。
2. experience.learning 写事件失败会丢弃已计算用量、费用及写入经验 ID。分离统计和事件保存，保留结果并返回 event_recorded=false，CLI stderr 明确提示。空经验/未知费率及成功写卡/已知费率两种情况均先失败再通过。

五项定向回归：5 passed，38.81 秒。修复提交 ae9da77；之后完整套件 554/554 通过，另有 5 项明确跳过。按执行流程没有重复派审查者。

## 实现取舍与审查边界

1. 使用托管独立 worktree 和虚拟环境，保留主目录与其他工作区；代价是额外安装时间及临时磁盘占用。
2. 详情接口增加可选 max_bytes，兼容较小任务工具额度；不足时拒绝读取，代价是可能无法读取该条详情。
3. 学习 30 秒工作预算外允许最多 1 秒关客户端；代价是最多多 1 秒收尾延迟。
4. 增加 learning.event_recorded 并提供固定 stderr 诊断；代价是严格自定义 JSON 消费者需兼容新增字段。
5. 保持文档中的可信本地项目边界；恶意 OS 路径重定向和 pytest 任意写外部文件没有安全沙箱保证，代价是无法安全运行不可信项目。
6. 不声称经验提高复现率，真实模型需分离学习/评测集并冻结库做 A/B；代价是当前收益未知且可能增加调用。
7. 按用户要求推迟探索阶段约束；现有 SDK 循环与预算继续治理探索，代价是无关探索仍会消耗主预算。

无延后 Minor。MVP 只追加和精确去重，不自动维护旧卡或扩容。历史来源任务目录被删除后，旧来源无法重新审计。真实模型 A/B 尚未执行。

## 本地交付

功能和修复使用 codex/experience-mvp 分支实现，再本地合入 E:/ReproAgent，保留主分支并行的 7a87625 文档提交。未 push；未创建新 PR。完整日志及执行台账保存在 .local/experience-mvp-verification（不提交）。
