# 自动经验库（MVP）

经验是给探索 Agent 的历史建议，不能代替本任务的源码和执行证据。无需人工核对。普通 CLI 任务省略 `experience_file` 时默认使用用户目录下的 `.reproagent/experiences.json`；显式设置 `"experience_file": null` 完全关闭读取、经验工具和提炼。程序接口和评测入口保持原有显式配置方式，不自动启用共享库。

## 配置

可用环境变量 `REPROAGENT_EXPERIENCE` 指定 CLI 默认库。环境变量中的相对路径按启动目录解析，并在加载任务配置时固定为绝对路径；任务配置中的路径优先于环境变量。

复制 [示例](../examples/task.experience.json)，按自己的项目调整解释器、仓库和输出目录。新增配置：

```json
{
  "experience_file": "../.local/experiences.json",
  "learn_experience": true
}
```

配置中的相对路径以 task.json 所在目录解析。库和稳定旁路锁 `experiences.json.lock` 必须在目标仓库、修复仓库和任务输出之外。这里指被复现项目，不是安装 ReproAgent 的目录。多个任务可以使用同一路径。缺文件按空库读取，首次有效学习才创建库。`learn_experience: false` 可以读取已有经验，但不会提炼或写入；它不等于完全关闭经验功能。默认启用提炼可能产生额外模型调用及费用。

## 什么时候读

准备阶段校验路径并读取一次库，固定任务视图。完成原始 Issue 分析后，无结构化触发条件的卡按 Issue 与 target_modules 匹配：标签命中和摘要关键词命中计分，同分按 ID 排序，零分不返回。英文按词匹配，中文使用相邻双字。有 triggers 的过程卡不参与这一检索，不因为工具可用就提前加载。

探索中，真实工具错误、最终权限拒绝或协议纠错触发从同一冻结库按需检索；条件 kind/tool/code 全部一致才匹配。摘要在下一次已有模型调用前作为历史建议送入，原始工具结果保持完整。初版支持 revise_contract 的 SOURCE_RANGE_NOT_DISPLAYED/SOURCE_HASH_MISMATCH，六工具的 DENIED，Read/Grep/Glob 的 RESERVED_FOR_PUBLISHING，以及 MULTIPLE_TOOL_CALLS/UNKNOWN_TOOL/INVALID_TOOL_INPUT。未知错误文本不猜测为上述条件。

首轮及探索中累计最多展示 3 条独立 id/summary；Agent 通过 AgentScope 只读工具 `read_experience` 按需读取一条详情。整个任务只能成功读一次，契约修订和候选重试不重读库、不重置配额、不重复展示已注入卡。累计摘要列表与已读详情的 UTF-8 JSON 合计最多 2048 字节；容量不足跳过经验，不截断正文、不挤掉原始 Issue/契约/工具结果。动态摘要实际进入上下文后才开放对应详情；上下文容量按完整 SDK 消息投影加新消息核对。分析和 Verifier 不直接加载经验，经验引用不能申请当前原版契约修订。

events.jsonl 和 trace 的 experience.triggered/retrieved/injected/omitted 分别说明实际情境、候选及匹配结果、实际加入上下文和省略原因。context_capacity 表示消息字节空间不足，context_threshold 表示加入后会越过 SDK token 阈值；这两种情况都省略经验。阈值通过 SDK 本地 count_tokens 估算并计入工具 schema，不调用 provider。trace 只增加受控 experience_trigger/experience_ids 元数据；不增加模型调用。整个功能不依赖 trace 开关。

## 什么时候写，谁写

主探索和模型资源关闭 → 导出成功或失败 → 最终 TaskResult 落盘 → ExperienceService 独立提炼 → 程序验证并追加 → 返回原结果。

模型仅提炼 category/tags/summary/detail 和 1–3 个材料 EID；程序生成经验 ID、来源任务标识及哈希/行号引用，自动写库。新卡只给下一任务使用，不改当前任务判定、耗时或已发布包。DONE、BLOCKED、NEEDS_INFORMATION、EXHAUSTED、FAILED 只有存在可信完整材料才学习。取消、只读、库损坏、无可用材料均不发学习请求；模型也可返回空经验。

学习使用相同模型配置、全新的 SDK 客户端和独立上下文，无完整探索聊天记录、无工具。工作预算最多 30 秒，一次逻辑请求、最多 3 次 HTTP 尝试，不做格式纠正；资源关闭另允许最多 1 秒。共享取消信号；主预算耗尽不会自动耗尽学习预算。有限本地文件操作采用协作式期限检查。

## 分类与存储

分类固定三个，不增加子分类：

| category | 适用 |
| --- | --- |
| framework | pytest、依赖或中间件的使用条件、环境限制 |
| model | AI 误读语义、断言或生成测试的错误模式 |
| workflow | 查找、资源调度、操作顺序与复现过程的教训 |

每卡最多 5 个短标签，每标签最多 64 个 Unicode 字符。重叠时优先选择具体框架约束，再选择生成错误模式，其余归工作流程。

单个 JSON 库根字段为 schema_version=1 和 items；每卡包含 id、category、tags、summary、detail、source_task_id、evidence_refs，可选 triggers 为最多 3 个受控 kind/tool/code 条件。最终 UTF-8 JSON 最多 2048 字节，全库最多 1 MiB。旧卡缺 triggers 按空列表读取，空条件 ID 算法保持规范化 category/tags/summary 的 SHA256；非空条件也参与 ID，避免不同适用情境被误去重。同 ID 跳过，旧 detail 不覆盖。读取不改写旧库、不自动给旧卡补造条件。

写入和检索共用同一套受控事件定义。模型仍只输出五字段，summary/detail 写明适用条件、实际观察和条件建议。程序核对所引用 EID 的冻结原子，从这些原子中的实际错误 metadata 或小型受控事件提取 triggers；不从摘要猜条件，不扫描本次所有工具，也不因任务 EXHAUSTED 就声称某个建议已有效。tool:/error: 标签由程序生成，模型的同名命名空间标签被去除，其余名额保留描述标签，总数仍最多 5 个。这只验证条件的事实来源，不证明建议语义或效果。

写入时用稳定旁路文件做非阻塞系统锁，锁内重新读取、校验、去重和测量，再原子替换。竞争返回 busy；损坏或容量已满返回 store_error；旧库保留。MVP 只追加和精确去重，不自动合并、衰减、删卡、更新旧卡或扩容。

## 材料与结果

学习用户 JSON 最多 8192 字节，仅来自冻结 Issue、校验过的原版引用、候选、original 执行和受控错误/测量。不读取修复执行、隐藏答案、参考测试、旧经验或整份报告。片段完整性或来源不可信则跳过。学习不再复用 Verifier 的审阅投影；程序分别整理三类材料，在同一次独立请求中选择至多一条经验。

模型可以看到有依据的代表性踩坑经过：最后一次候选与最近一次代码或执行设置不同的尝试、实际运行 ID、错误和验收分类、是否通过原版重跑确认。相同代码反复发布时，不把新 ID 当作新的改进；没有不同尝试时可保留相邻 ID。提供执行总数和步骤/读取次数，但不声称这是完整历史，时间先后也不证明因果。

探索资源关闭时，从 SDK 已返回的消息中保留当前任务少量工具错误（Read/Grep/Glob 和三个业务工具），包括调用 ID、参数、返回错误、状态及关联是否缺失；Read 和 Grep 实际发出的源码引用一起保留。引用只允许任务首次冻结的原版快照，并核对 original 执行绑定；后续修复版本快照不入选。最多 8 条完整错误，每条最多 2048 字节，最多 16 条源码引用。只扫描最后 512 条消息、每条前 64 个块，超限明确记录。不会把 read_experience 的旧经验详情、助手自由叙述或二进制输出当作材料；不依赖 trace 开关，也不增加模型调用。

用户 JSON 包含 evidence、attempts、category_materials、omissions 与 response_schema。单个可选材料原子最多 2048 字节，最多 24 条；先保留尝试结果和代表性候选代码，再补充失败细节；大堆栈或大候选放不下时跳过该原子，其他可用材料继续入选。候选可以提供完整测试函数片段，源码可以提供精确行号的已读片段，明确标注上下文不完整。Issue 保留全文；全文加必要结构放不下则返回 skipped_input_too_large，不悄悄截断。

采集/材料状态分别可见：skipped_no_evidence（无事实）、skipped_input_too_large（必要材料超限）、skipped_invalid_evidence（来源或记录校验失败）、skipped_existing_evidence（已有冻结胶囊，不覆盖）。experience.material 事件记录字节数、材料数、尝试数及实际省略项。库写入仍由程序校验引用、大小和格式；这不证明模型提出的经验语义一定正确。

材料先一次性冻结到任务内部 learning/evidence.jsonl；最终引用固定该文件的哈希和行号。追加事件不会改变引用。原材料任务目录删掉后，这些历史来源将不能重新审计；库中的建议仍可被读取。已知模型凭据脱敏不等于通用秘密扫描。

用户继续审查原 report.md；限制部分只列实际成功读取的经验 ID，不复制库或新增审查文档。report.json 有 experience_read_ids，CLI 的 learning 字段单独说明 code、duration、HTTP、usage、已知费用小计与未知费用次数。学习失败不改主退出码；诊断不一定代表经验写入。

学习请求明确要求一个 JSON 对象，兼容 `response_format=json_object` 的提示词约束。非空 experience 必须有 category、tags、summary、detail、evidence_ids 五个字段；正文提到 EID 不能替代 evidence_ids 数组，程序不从正文猜引用或自动补字段。HTTP 拒绝或三次网络尝试耗尽返回 `provider_error`。每个 `model.attempt` 在收到 HTTP 响应时记录 `http_status_code`，没收到响应则不虚构状态码。

校验失败单独诊断，保留主任务结果，不写入坏卡、不追加纠错请求：

| learning.code | 含义 |
| --- | --- |
| invalid_missing_fields | 缺少必填字段，missing_fields 列出固定 schema 内的字段名 |
| invalid_extra_fields | 多出未允许的字段，不把任意额外字段名写进日志 |
| invalid_structure | 顶层/experience 对象结构错误 |
| invalid_json | 无法解析为严格 JSON 对象 |
| invalid_fields | 分类、标签、摘要或详情类型/取值无效 |
| invalid_evidence_ids | 引用 ID 不存在、重复、数量或类型不合法 |
| invalid_evidence_changed | 冻结证据文件、哈希、行号或 ID 无法再验证 |
| invalid_card_size | 最终卡片超过 2048 字节 |
| invalid_model_output | 模型回复截断、空输出或协议不兼容，具体协议码见 model.attempt |
| provider_error | HTTP/网络请求失败，状态码见 model.attempt |
| internal_error | 未归入上述校验原因的参数/运行时异常，不假称经验格式错误 |

CLI learning、experience.learning 事件及 trace 学习汇总共享 code/missing_fields；learn span 也记录最终结果码。只记录受控诊断，不保存原始异常文本或任意模型字段。

启用 trace 时，HTTP span 附带状态码，并关联 `provider_error` 错误摘要：只选择 code/type/message，已知模型密钥先脱敏，每字段最多 512 字符，省略时标记 truncated。不复制响应头或额外 debug 字段；非 JSON 错误正文仅保存有限脱敏预览。关闭 trace 不保留错误正文，事件中始终只有受控元数据，不把这些错误预览喂回学习模型。此采集不增加模型请求。

`learning.usage` 是已报告 token 的小计；`unknown_usage_attempts` 表示缺少任一 prompt/completion/total token 计数的尝试数。该字段大于 0 时，小计不能代表完整消耗，即使小计为 0 也不能断言免费。费用缺失仍通过 `unknown_cost_attempts` 表达。

## 冻结评测

学习集先用普通任务生成库，评测集不学回。SWT 批量入口支持：

```text
python -m evals.swt_bench run ... --experience-file path/to/experiences.json
```

其他 run 参数见 [SWT 使用说明](swt-bench.md)。入口固定起始库哈希并传 learn_experience=False，每 case 前及轮次结束再次核对。变更时停止启动后续 case，保留分母、记录 EXPERIENCE_SOURCE_CHANGED、标记该轮不可比较。库不得位于轮次输出、原版或修复目录中。程序接口 evals.run.run_case 同样支持 experience_file，学习默认 false。

HTTP/token/费用总计已经包含真实学习尝试一次，learning 分项只用于拆解，不再叠加。缺计价或缺 usage 的费用仍为 unknown。

离线测试验证真实 SDK + MockTransport + pytest 的调用、时序、引用和交付。提高复现率的结论必须来自固定模型/预算/环境、学习集与评测集分离、冻结库的关闭/开启对照；当前未执行真实模型 A/B。

A/B 的有效性不能只看库的 ready/count：必须检查开启臂实际模型请求包含非空 experience_summaries，关闭臂未注入经验，并核对每次运行前后冻结库哈希不变。experience.matched/experience.retrieved 表示选中，不保证随后通过上下文容量检查而进入模型；experience.injected 表示运行时实际加入，wire request 才是最终核对依据；read_experience 表示是否进一步读详情。有条件卡首轮零匹配不意味着后来无法检索，需要等对应真实事件发生后判断。若完整任务没有实际注入，停止把后续结果当作卡片效果，不强行补条件。每臂至少两次只能作为小样本试验；同一 Issue 的学习后重跑不能证明跨 Issue 泛化。

开启经验功能，即使匹配结果为空，也可能增加 read_experience 工具。因此零命中不能推出两个运行的全部输入相同。若要测卡片内容的作用，可让控制臂读取有效空库、两臂都禁止写回并保留相同工具表；另外检查实际 wire request 的工具集一致。完全禁用经验与开启经验的对照则衡量整个功能开关，不能把差异单独归因于卡片内容。

CLI learning.event_recorded=false 表示学习汇总事件未保存；已收集的 HTTP、token、费用和经验 ID 仍保留在内存返回结果中，stderr 提示保存失败，主退出码保持原值。过深或损坏的库 JSON 按 store_error 关闭本任务的经验功能，不停止主复现，也不覆盖旧库。
