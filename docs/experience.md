# 自动经验库（MVP）

经验是给探索 Agent 的历史建议，不能代替本任务的源码和执行证据。无需人工核对。普通 CLI 任务省略 `experience_file` 时默认使用用户目录下的 `.reproagent/experiences` 分类目录；新目录不存在但旧 `experiences.json` 存在时继续使用旧库；显式设置 `"experience_file": null` 完全关闭读取、经验工具和提炼。程序接口和评测入口保持原有显式配置方式，不自动启用共享库。

## 配置

可用环境变量 `REPROAGENT_EXPERIENCE` 指定 CLI 默认库。环境变量中的相对路径按启动目录解析，并在加载任务配置时固定为绝对路径；任务配置中的路径优先于环境变量。

复制 [示例](../examples/task.experience.json)，按自己的项目调整解释器、仓库和输出目录。新增配置：

```json
{
  "experience_file": "../.local/experiences",
  "learn_experience": true
}
```

配置中的相对路径以 task.json 所在目录解析。库和稳定旁路锁 根目录旁的 `experiences.lock`（文件模式为 `experiences.json.lock`） 必须在目标仓库、修复仓库和任务输出之外。这里指被复现项目，不是安装 ReproAgent 的目录。多个任务可以使用同一路径。缺文件按空库读取，首次有效学习才创建库。`learn_experience: false` 可以读取已有经验，但不会提炼或写入；它不等于完全关闭经验功能。默认启用提炼可能产生额外模型调用及费用。

## 什么时候读

准备阶段只读取并冻结库，不把所有正文送入模型。有非空可用库时，探索 Agent 必须先根据当前输入选择分类并调用一次 `list_experiences(category)`；完整结果交付后，自主决定是否翻页、换分类或读取正文。短索引的 description 说明什么时候需要加载；无需先出现错误码，无关键词硬过滤。模型从目录选择适用 ID，再调用 `read_experience(id)` 读取 name/description/summary/detail。不新增后续探索阶段约束，不自动匹配注入摘要。

三个分类是 framework（框架/环境条件）、model（断言或生成错误）、workflow（探索与操作教训）。沿用本次已有基线：每页最多 24 条、完整 UTF-8 JSON 最多 8192 字节；更多条目通过 next_cursor 分页。每任务最多成功浏览 12 页，目录响应累计最多 16384 字节；正文最多成功读取两次，单次完整响应最多 8192 字节。重复浏览同页也计入配额，契约修订不重置。目录完整成功返回过的 ID 才有正文读取权限；超限明确返回原因，不静默截断或授权不可见 ID。

目录码 ready/empty 表示成功；invalid_category/invalid_cursor/catalog_limit/catalog_budget/response_capacity/catalog_unavailable 表示未成功加载。正文拒绝码 unknown_id/not_displayed/read_limit/response_capacity。首次浏览要求未满足时返回 EXPERIENCE_BROWSE_REQUIRED，不执行该工具。两个工具仍遵守原动作预算、取消、SDK 权限和保留发布步骤；没有额外模型预算。本地浏览和读取不发模型请求，但 Agent 选择工具的轮次和返回文本消耗模型步骤与 token。

经验为历史建议，不是原版源码证据，不能作为 revise_contract 引用；分析和 Verifier 不直接加载经验。事件 experience.catalog 记录分类、实际返回的 ID、字节和分页；experience.read 记录实际成功读正文的 ID。真实错误仍可见于 experience.triggered，但不会自动召回或注入卡片；本版不产生自动匹配的 retrieved/injected/omitted 事件。完整 trace 的工具输入、输出和下一模型请求用于核实加载链，关闭 trace 不影响加载。

## 什么时候写，谁写

主探索和模型资源关闭 → 导出成功或失败 → 最终 TaskResult 落盘 → ExperienceService 独立提炼 → 程序验证并追加 → 返回原结果。

模型仅提炼 category/tags/name/description/summary/detail 和 1–3 个材料 EID；程序生成经验 ID、来源任务标识及哈希/行号引用，自动写库。新卡只给下一任务使用，不改当前任务判定、耗时或已发布包。DONE、BLOCKED、NEEDS_INFORMATION、EXHAUSTED、FAILED 只有存在可信完整材料才学习。取消、只读、库损坏、无可用材料均不发学习请求；模型也可返回空经验。

学习使用相同模型配置、全新的 SDK 客户端和独立上下文，无完整探索聊天记录、无工具。工作预算最多 30 秒，一次逻辑请求、最多 3 次 HTTP 尝试，不做格式纠正；资源关闭另允许最多 1 秒。共享取消信号；主预算耗尽不会自动耗尽学习预算。有限本地文件操作采用协作式期限检查。

## 分类与存储

分类固定三个，不增加子分类：

| category | 适用 |
| --- | --- |
| framework | pytest、依赖或中间件的使用条件、环境限制 |
| model | AI 误读语义、断言或生成测试的错误模式 |
| workflow | 查找、资源调度、操作顺序与复现过程的教训 |

每卡最多 5 个短标签，每标签最多 64 个 Unicode 字符。重叠时优先选择具体框架约束，再选择生成错误模式，其余归工作流程。

目录结构：

```text
experiences/
  framework/experiences.json
  framework/index.json
  model/experiences.json
  model/index.json
  workflow/experiences.json
  workflow/index.json
experiences.lock
```

每分类正文是一个 JSON 对象 `{"schema_version":2,"category":"workflow","items":[...]}`，合并存储多条卡，不为每坑创建文件。每卡保留 id/category/tags/summary/detail/source_task_id/evidence_refs 和可选 triggers，新卡增加 name（1–64 字符）/description（1–160 字符）。完整卡最多 4096 字节，三个正文总计最多 1 MiB。

index.json 为可丢弃缓存，包含 schema_version=1/category/source_hash/items，items 只有 id/name/description。正文是唯一事实源，索引由程序生成，不由模型独立维护。缺失、过期、损坏或与正文投影不符时在内存重建，记录 experience.index；只读任务不修写缓存。正文已写入但缓存失败仍返回 written，同时记录 index_update_failed；后续任务从正文恢复。缓存总上限 1 MiB。

根旁路锁保护快照读取和追加，锁内重读、校验、去重、限额测量后原子替换分类正文。任务中的库视图固定，新卡给下一任务使用。损坏正文关闭该任务经验功能，不覆盖旧数据、不影响主复现。只追加和同 ID 精确去重，不合并、衰减、删除或更新旧卡；name/description 不改变现有 ID 算法。triggers 仍从引用的实际受控事实生成，作为来源元数据保留，不控制目录可见性。程序验证来源，不保证建议语义或效果。

experience_file 配置兼容旧 `.json` 单文件和新目录。旧 schema=1 可读；旧卡无 name/description 时从 summary 派生简短目录文字，保留原卡和 ID。只读不升级；第一次有效追加时文件模式在锁内升级为 schema=2，旧二进制不保证能读新版格式。两种路径同时存在时默认使用新目录，显式指定旧路径仍使用旧库。

可一次性迁移到尚不存在的目录，源文件保持不变：

```text
reproagent experience migrate path/to/old.json path/to/experiences
```

迁移在同级临时目录构建并验证三个分类，校验通过后发布目标；目标已存在、源变化、坏库或锁竞争明确失败。新库中的源码引用仍指向原任务的冻结材料，迁移不复制原任务证据。

## 材料与结果

学习用户 JSON 最多 8192 字节，仅来自冻结 Issue、校验过的原版引用、候选、original 执行和受控错误/测量。不读取修复执行、隐藏答案、参考测试、旧经验或整份报告。片段完整性或来源不可信则跳过。学习不再复用 Verifier 的审阅投影；程序分别整理三类材料，在同一次独立请求中选择至多一条经验。

模型可以看到有依据的代表性踩坑经过：最后一次候选与最近一次代码或执行设置不同的尝试、实际运行 ID、错误和验收分类、是否通过原版重跑确认。相同代码反复发布时，不把新 ID 当作新的改进；没有不同尝试时可保留相邻 ID。提供执行总数和步骤/读取次数，但不声称这是完整历史，时间先后也不证明因果。

探索资源关闭时，从 SDK 已返回的消息中保留当前任务少量工具错误（Read/Grep/Glob 和三个业务工具），包括调用 ID、参数、返回错误、状态及关联是否缺失；Read 和 Grep 实际发出的源码引用一起保留。引用只允许任务首次冻结的原版快照，并核对 original 执行绑定；后续修复版本快照不入选。最多 8 条完整错误，每条最多 2048 字节，最多 16 条源码引用。只扫描最后 512 条消息、每条前 64 个块，超限明确记录。不会把 read_experience 的旧经验详情、助手自由叙述或二进制输出当作材料；不依赖 trace 开关，也不增加模型调用。

用户 JSON 包含 evidence、attempts、category_materials、omissions 与 response_schema。单个可选材料原子最多 2048 字节，最多 24 条；先保留尝试结果和代表性候选代码，再补充失败细节；大堆栈或大候选放不下时跳过该原子，其他可用材料继续入选。候选可以提供完整测试函数片段，源码可以提供精确行号的已读片段，明确标注上下文不完整。Issue 保留全文；全文加必要结构放不下则返回 skipped_input_too_large，不悄悄截断。

采集/材料状态分别可见：skipped_no_evidence（无事实）、skipped_input_too_large（必要材料超限）、skipped_invalid_evidence（来源或记录校验失败）、skipped_existing_evidence（已有冻结胶囊，不覆盖）。experience.material 事件记录字节数、材料数、尝试数及实际省略项。库写入仍由程序校验引用、大小和格式；这不证明模型提出的经验语义一定正确。

材料先一次性冻结到任务内部 learning/evidence.jsonl；最终引用固定该文件的哈希和行号。追加事件不会改变引用。原材料任务目录删掉后，这些历史来源将不能重新审计；库中的建议仍可被读取。已知模型凭据脱敏不等于通用秘密扫描。

用户继续审查原 report.md；限制部分只列实际成功读取的经验 ID，不复制库或新增审查文档。report.json 有 experience_read_ids，CLI 的 learning 字段单独说明 code、duration、HTTP、usage、已知费用小计与未知费用次数。学习失败不改主退出码；诊断不一定代表经验写入。

学习请求明确要求一个 JSON 对象，兼容 `response_format=json_object` 的提示词约束。非空 experience 必须有 category、tags、name、description、summary、detail、evidence_ids 七个字段；正文提到 EID 不能替代 evidence_ids 数组，程序不从正文猜引用或自动补字段。HTTP 拒绝或三次网络尝试耗尽返回 `provider_error`。每个 `model.attempt` 在收到 HTTP 响应时记录 `http_status_code`，没收到响应则不虚构状态码。

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
| invalid_card_size | 最终卡片超过 4096 字节；needed_bytes/limit_bytes/exceeded_bytes 给出实际大小、上限和超出量 |
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

A/B 必须检查目录工具结果和下一轮 wire request：有库不等于已浏览，浏览不等于已读取正文，实际加载也不等于经验有效。对照卡片作用时两臂都启用相同的八工具，控制臂用有效空库，均禁止学习写回，核对三个正文冻结哈希前后不变。缓存变化不改变内容指纹，任一正文变化则使该轮不可比较。至少两次只算小样本信号；同一 Issue 重跑不证明跨 Issue 泛化。

CLI learning.event_recorded=false 表示学习汇总事件未保存；已收集的 HTTP、token、费用和经验 ID 仍保留在内存返回结果中，stderr 提示保存失败，主退出码保持原值。过深或损坏的库 JSON 按 store_error 关闭本任务的经验功能，不停止主复现，也不覆盖旧库。

证据等级 NONE 表示没有被认可的复现证据，不代表没有生成候选或执行测试；判断探索进度应同时核对候选数、original 执行数和验收结果。SINGLE_OBSERVATION 表示单次复现已被认可，仍需独立重跑确认。

卡片落盘上限为 4 KiB，学习材料仍为 8192 字节；目录/正文加载配额见上文。首次浏览门禁不增加这些配额，模型总上下文和步骤上限不变。超限数字在规范化标签、受控 triggers 和证据引用全部补齐后，按最终 UTF-8 JSON 计算；非大小错误的三个字段为 null。学习失败不改变主退出码，不自动追加格式修复或重试请求。

## 首次目录浏览

启用经验且冻结库非空时，探索 Agent 在源码读取、契约修订和候选发布之前，必须先调用一次 `list_experiences(category)`，显式选择 framework/model/workflow 且不带游标。目录入口展示各类数量及用途；不会先把经验正文注入模型。

程序门禁在完整目录结果进入下一轮模型请求后解除。小目录可一次看完，大目录通过 `next_cursor` 翻页，没有“只能看前五条”的限制。所选目录为空也解除首次门禁；模型之后可换分类、翻页、读相关正文，或直接探索。门禁与目录/正文配额跨契约修订共享，不重新要求浏览。

首次调用使用现有模型和动作预算，不额外调用检索模型；索引和正文进入输入仍产生 token。模型连续两次未遵循首次浏览时，这两次工具均不执行，随后记录 `bypassed/noncompliant` 放行探索。经验关闭、空库、不可用或明确容量/权限问题同样不会卡死主任务；取消与全局预算耗尽仍按原规则终止。

观测新增 `experience.browse_gate`，区分 pending/satisfied/bypassed 和具体原因，关联目录调用与实际输入。目录交付、正文读取、经验被采用及最终复现收益是不同指标。历史经验仍为不可信建议，不能进入原版证据或 Verifier。
