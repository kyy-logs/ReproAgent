# 自动经验库（MVP）

经验是给探索 Agent 的历史建议，不能代替本任务的源码和执行证据。第一版无需人工核对，默认不启用；没有配置经验路径时，原流程和六个工具保持不变。

## 开启

复制 [示例](../examples/task.experience.json)，按自己的项目调整解释器、仓库和输出目录。新增配置：

```json
{
  "experience_file": "../.local/experiences.json",
  "learn_experience": true
}
```

相对路径以 task.json 所在目录解析。库和稳定旁路锁 `experiences.json.lock` 必须在目标仓库、修复仓库和任务输出之外。这里指被复现项目，不是安装 ReproAgent 的目录。多个任务可以使用同一路径。缺文件按空库读取，首次有效学习才创建库。设为 false 可以读取已有经验，但不会提炼或写入。

## 什么时候读

准备阶段校验路径并读取一次库，固定任务视图。完成原始 Issue 分析后，按 Issue 与 target_modules 匹配经验：标签命中和摘要关键词命中计分，同分按 ID 排序，零分不返回。英文按词匹配，中文使用相邻双字。

首轮探索最多展示 3 条 id/summary；Agent 通过 AgentScope 只读工具 `read_experience` 按需读取一条详情。整个任务只能成功读一次，契约修订和候选重试不重新加载。摘要列表与详情的 UTF-8 JSON 合计最多 2048 字节；容量不足时拒绝详情或减少摘要，不截断正文，不挤掉原始 Issue/契约。分析和 Verifier 不直接加载经验，经验引用不能申请当前原版契约修订。

## 什么时候写，谁写

主探索和模型资源关闭 → 导出成功或失败 → 最终 TaskResult 落盘 → ExperienceService 独立提炼 → 程序验证并追加 → 返回原结果。

模型仅提炼 category/tags/summary/detail 和 1–3 个材料 EID；程序生成经验 ID、来源任务标识及哈希/行号引用，自动写库。新卡只给下一任务使用，不改当前任务判定、耗时或已发布包。DONE、BLOCKED、NEEDS_INFORMATION、EXHAUSTED、FAILED 只有存在可信完整材料才学习。取消、只读、库损坏、无可用材料均不发学习请求；模型也可返回空经验。

学习使用相同模型配置、全新的 SDK 客户端和独立上下文，无探索历史、无工具。工作预算最多 30 秒，一次逻辑请求、最多 3 次 HTTP 尝试，不做格式纠正；资源关闭另允许最多 1 秒。共享取消信号；主预算耗尽不会自动耗尽学习预算。有限本地文件操作采用协作式期限检查。

## 分类与存储

分类固定三个，不增加子分类：

| category | 适用 |
| --- | --- |
| framework | pytest、依赖或中间件的使用条件、环境限制 |
| model | AI 误读语义、断言或生成测试的错误模式 |
| workflow | 查找、资源调度、操作顺序与复现过程的教训 |

每卡最多 5 个短标签，每标签最多 64 个 Unicode 字符。重叠时优先选择具体框架约束，再选择生成错误模式，其余归工作流程。

单个 JSON 库根字段为 schema_version=1 和 items；每卡包含 id、category、tags、summary、detail、source_task_id、evidence_refs，最终 UTF-8 JSON 最多 2048 字节，全库最多 1 MiB。ID 来自规范化 category/tags/summary 的 SHA256；同 ID 跳过，旧 detail 不覆盖。

写入时用稳定旁路文件做非阻塞系统锁，锁内重新读取、校验、去重和测量，再原子替换。竞争返回 busy；损坏或容量已满返回 store_error；旧库保留。MVP 只追加和精确去重，不自动合并、衰减、删卡、更新旧卡或扩容。

## 材料与结果

学习用户 JSON 最多 8192 字节，仅来自冻结 Issue、校验过的原版引用、候选、original 执行和受控错误/测量。不读取修复执行、隐藏答案、参考测试、旧经验或整份报告。片段完整性或来源不可信则跳过。

材料先一次性冻结到任务内部 learning/evidence.jsonl；最终引用固定该文件的哈希和行号。追加事件不会改变引用。原材料任务目录删掉后，这些历史来源将不能重新审计；库中的建议仍可被读取。已知模型凭据脱敏不等于通用秘密扫描。

用户继续审查原 report.md；限制部分只列实际成功读取的经验 ID，不复制库或新增审查文档。report.json 有 experience_read_ids，CLI 的 learning 字段单独说明 code、duration、HTTP、usage、已知费用小计与未知费用次数。学习失败不改主退出码；诊断不一定代表经验写入。

## 冻结评测

学习集先用普通任务生成库，评测集不学回。SWT 批量入口支持：

```text
python -m evals.swt_bench run ... --experience-file path/to/experiences.json
```

其他 run 参数见 [SWT 使用说明](swt-bench.md)。入口固定起始库哈希并传 learn_experience=False，每 case 前及轮次结束再次核对。变更时停止启动后续 case，保留分母、记录 EXPERIENCE_SOURCE_CHANGED、标记该轮不可比较。库不得位于轮次输出、原版或修复目录中。程序接口 evals.run.run_case 同样支持 experience_file，学习默认 false。

HTTP/token/费用总计已经包含真实学习尝试一次，learning 分项只用于拆解，不再叠加。缺计价或缺 usage 的费用仍为 unknown。

离线测试验证真实 SDK + MockTransport + pytest 的调用、时序、引用和交付。提高复现率的结论必须来自固定模型/预算/环境、学习集与评测集分离、冻结库的关闭/开启对照；当前未执行真实模型 A/B。

CLI learning.event_recorded=false 表示学习汇总事件未保存；已收集的 HTTP、token、费用和经验 ID 仍保留在内存返回结果中，stderr 提示保存失败，主退出码保持原值。过深或损坏的库 JSON 按 store_error 关闭本任务的经验功能，不停止主复现，也不覆盖旧库。
