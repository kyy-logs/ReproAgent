# 自动经验库（MVP）

经验是给探索 Agent 的历史建议，不能代替本任务的源码和执行证据。无需人工核对。普通 CLI 任务省略 `experience_file` 时默认使用用户目录下的 `.reproagent/experiences` 分类目录；新目录不存在但旧 `experiences.json` 存在时继续使用旧库；显式设置 `"experience_file": null` 完全关闭读取、经验工具和提炼。程序接口和评测入口保持原有显式配置方式，不自动启用共享库。

## 配置

可用环境变量 `REPROAGENT_EXPERIENCE` 指定 CLI 默认库。环境变量中的相对路径按启动目录解析，并在加载任务配置时固定为绝对路径；任务配置中的路径优先于环境变量。

复制 [示例](../examples/task.experience.json)，按自己的项目调整解释器、仓库和输出目录。新增配置：

```json
{
  "experience_file": "../.local/experiences",
  "learn_experience": true,
  "experience_mode": "legacy"
}
```

`experience_mode` 默认 `legacy`，行为与旧版相同：探索结束时从程序材料独立提炼。实验模式 `draft_review` 会在 `write_candidate` 的可选 `lesson_draft` 中接收探索者的踩坑草稿，并在任务结果落盘后由现有的一次 learning 调用审查、整理和写库；没有可审查草稿且未开启兜底时返回 `skipped_no_draft`，不会额外调用模型补总结。`draft_review` 还接受显式决定字段 `lesson_status`，组合规则如下：

| lesson_status | lesson_draft | 记录 |
| --- | --- | --- |
| 缺失 | 完整四字段 | received |
| 缺失 | 缺失 | omitted |
| `draft` | 完整四字段 | received |
| `draft` | 缺失或无效 | invalid_decision |
| `none` | 缺失或 null | none |
| `none` | 有草稿 | invalid_decision |
| 其他坏值 | 任意 | invalid_decision |

坏值只记录受控 code 和有限元数据，不保存模型原始状态文本；所有坏决定都不阻止候选按原规则发布。legacy 模式不增加这两个字段，工具 schema 保持原样。新卡仍只在下一任务可见，历史经验仍不能作为源码证据或进入 Verifier。

配置 `experience_draft_fallback` 默认 `false`。显式设为 `true` 时，仅在 `draft_review` 下同时满足以下条件才用一次独立的 30 秒 learning 请求生成草稿候选，再交给现有 review：没有合法草稿且没有显式 `lesson_status="none"`（即 omitted 或 invalid_decision）；保存的任务结果不是 `DONE`，或任务事件中存在受控/验收失败（`action.rejected`、`exploration.tool_error`、`phase.error`，或 `verification.feedback` 的受控 decision/code）。成功且没有受控失败的 DONE 任务不会发起兜底请求；没有可用受控材料时也不会发起。兜底不读取完整聊天、fixed 代码或参考补丁，不直接写卡，失败只返回受控 code，不改变主任务结果。已有合法草稿、显式 none、legacy 模式、未启用学习或取消/坏库等既有跳过条件都不会触发兜底。

候选被 Verifier 打回不等于草稿无效：草稿仍保存在 `learning/draft.json` 并进入任务结束后的审查材料。审查可以接受或修订一条框架/流程教训，但不得声称候选已成功或恢复已验证；只有 accept/revise 才写卡，reject 不写卡。

流程踩坑不要求包含源码知识：工具/协议拒绝、路径、权限、引用、探索顺序和预算问题，只要有独立来源支持“具体条件 → 尝试的操作 → 实际结果 → 下次检查”，都可以形成草稿。无需先查清根因或验证解法；尚未执行的建议必须标为未验证。例如 Read 明确拒绝相对路径，可以支持“读取前核对冻结源码的绝对路径”，但不能推出“任意绝对路径都可读”。模型必须复制该次实际拒绝提供的来源 ID。

`none` 仍是有效选择：无来源、只有猜测、仅重复既有规则而没有有用的适用条件或观察，或无法提出具体后续检查时，不应为了写卡凑经验。报错本身不保证经验有价值；也不能仅因缺少源码事实而排除有依据的流程经验。这个口径只调整探索、工具说明、单次提醒、兜底和审查提示，不增加模型调用、不自动生成草稿、不改变来源资格或严格写库校验。

`draft_review` 的 `write_candidate` 对模型声明 `lesson_status` 必填（`draft`/`none`）；`lesson_draft` 仍可选。若模型同时漏填两者，且本任务已有实际可见、可引用的学习来源，程序最多发起一次补全逻辑请求：复用当次探索模型和上下文，最多 10 秒、768 输出 token（不超过模型配置上限），正常计入探索步骤、Token 和费用。补全开始前要求剩余步骤仍大于发布预留 3 步，且剩余任务时间至少为补全 10 秒加现有 finalize 预留（默认 5 秒）；容量、取消、时间不足、没有来源或已经补过时跳过。补全只返回 `lesson_status`/`lesson_draft`，不能改变候选 files、hypothesis 或 call ID；超时、模型错误、非法 JSON、未知来源或非法结构都保留原候选并记录为 omitted，不编造 none。补全请求与响应仍走现有观测和用量统计；`experience.lesson_completion` 只记录受控 code、call ID、原始/合并参数哈希和实际调用序号，不保存模型正文。补出的草稿继续经过既有大小、来源成员资格、时间顺序和 review 校验。

这里的上下文来自最近一次选中该候选的完整探索输入快照，保留当时的目标、契约、反馈与已配对的历史工具调用/结果，再追加补全指令和来源说明。构造补全请求时排除该轮临时的经验决定提醒、经验浏览提醒和探索预算通知，避免重复交付单次提醒或沿用过时的动作指令。不会直接读取已插入当前待执行 `write_candidate` 的实时历史，避免把尚无结果的工具调用带进补全。无法取得快照时记 `skipped_context`，不退回凭登记表猜测。来源登记表读取失败按 `skipped_no_source` 跳过补全并继续发布原候选。输入估算完成后、发请求前再次检查取消、步骤和“10 秒加 finalize 预留”的时间余量；跳过不消耗唯一一次补全资格，全局取消和硬预算停止仍按原规则传播。

草稿审查材料总共最多 8192 字节，包含草稿、输出 schema 和程序证据。先保留草稿引用的必需来源与冻结 Issue，再补可选背景；装不下的背景明确记录省略，不截断必需来源。工具来源按消息与消息内块的先后关联，重复调用 ID、自引用、未来来源以及未完整采集的必需来源均不猜测绑定；原版执行来源还必须早于草稿记录。最终选定的材料才冻结为 `learning/evidence.jsonl`。

执行材料中的 `verdict_diagnostics` 保留同一次原版运行最终判定的受控 `code`、`origin`，以及已识别的 `protocol_error`；最多三项，更多项通过 `verdict_diagnostics_omitted` 标明。未知协议码显示为 `unrecognized`，不回传任意错误文本。证据胶囊同时保留对应 verdict 的路径与哈希。`classification=null` 表示判定未成立，不等于 `NOT_REPRODUCED`；单次 `REPRODUCED` 与重放确认分开。草稿不得只凭相同失败名称就断言修改等价；缺少源码对照时，应保留“不确定”。这些信息不改变原验收结论或学习材料上限。

历史归档的审查请求不会自动补齐新字段。评估这项修复时，应由当前程序重新生成材料；直接重放旧的 `prepared-input.json` 仍会缺少原先丢失的诊断。

配置中的相对路径以 task.json 所在目录解析。库和稳定旁路锁 根目录旁的 `experiences.lock`（文件模式为 `experiences.json.lock`） 必须在目标仓库、修复仓库和任务输出之外。这里指被复现项目，不是安装 ReproAgent 的目录。多个任务可以使用同一路径。缺文件按空库读取，首次有效学习才创建库。`learn_experience: false` 可以读取已有经验，但不会提炼或写入；它不等于完全关闭经验功能。默认启用提炼可能产生额外模型调用及费用。

## 什么时候读

准备阶段只读取并冻结库，不把所有正文送入模型。有非空可用库时，探索 Agent 必须先根据当前输入选择分类并调用一次 `list_experiences(category)`；完整结果交付后，自主决定是否翻页、换分类或读取正文。短索引的 description 说明什么时候需要加载；无需先出现错误码，无关键词硬过滤。模型从目录选择适用 ID，再调用 `read_experience(id)` 读取 description/summary/detail。不新增后续探索阶段约束，不自动匹配注入摘要。

三个分类是 framework（框架/环境条件）、model（断言或生成错误）、workflow（探索与操作教训）。沿用本次已有基线：每页最多 24 条、完整 UTF-8 JSON 最多 8192 字节；更多条目通过 next_cursor 分页。每任务最多成功浏览 12 页，目录响应累计最多 16384 字节；正文最多成功读取两次，单次完整响应最多 8192 字节。重复浏览同页也计入配额，契约修订不重置。目录完整成功返回过的 ID 才有正文读取权限；超限明确返回原因，不静默截断或授权不可见 ID。

目录码 ready/empty 表示成功；invalid_category/invalid_cursor/catalog_limit/catalog_budget/response_capacity/catalog_unavailable 表示未成功加载。正文拒绝码 unknown_id/not_displayed/read_limit/response_capacity。首次浏览要求未满足时返回 EXPERIENCE_BROWSE_REQUIRED，不执行该工具。两个工具仍遵守原动作预算、取消、SDK 权限和保留发布步骤；没有额外模型预算。本地浏览和读取不发模型请求，但 Agent 选择工具的轮次和返回文本消耗模型步骤与 token。

经验为历史建议，不是原版源码证据，不能作为 revise_contract 引用；分析和 Verifier 不直接加载经验。事件 experience.catalog 记录分类、实际返回的 ID、字节和分页；experience.read 记录实际成功读正文的 ID。真实错误仍可见于 experience.triggered，但不会自动召回或注入卡片；本版不产生自动匹配的 retrieved/injected/omitted 事件。完整 trace 的工具输入、输出和下一模型请求用于核实加载链，关闭 trace 不影响加载。

`draft_review` 且启用学习时，已解除首次浏览门禁的探索在可恢复阻塞后可收到一次简短查阅提醒。生成提醒前检查调用前剩余步骤（当前至少 6 步）、实际可交付的目录页、正文额度及 SDK 权限规则；这项检查不扣配额、不授权 ID，也不自动读卡。有 deny/ask 规则时保守地省略提醒，实际工具调用仍走原权限判定。`experience.hint` 记录送达或具体抑制原因、原阻塞调用 ID 和下一模型调用序号；记录失败不改变主任务结果。

同一 `draft_review` 任务在受控失败后还会收到一次短暂、非持久的 `lesson-decision` reminder：verification feedback 的 `decision=continue`，或实际工具错误、权限拒绝、协议拒绝，都会在下一模型调用前提示模型在下次 `write_candidate` 显式提供 `lesson_draft` 或声明 `lesson_status="none"`。准备提醒不等于交付：只有最终输入仍含提醒且模型正常返回时才记为 `delivered`，此后本任务不再准备；发送前因容量移除记为 `not_delivered/context_capacity`，资格仍保留，下一调用可以重新准备；预算守卫在发送前终止记为 `not_delivered/budget_stopped` 并保留真实 `stop_reason`；模型调用抛异常且无法证明请求未发送时记为 `delivery_unconfirmed/model_call_failed`，既不虚报 delivered，也不重新准备另一份。`prepared`、`delivered`、`not_delivered`、`delivery_unconfirmed` 都写入 `experience.lesson_decision`，事件只含 phase、受控 code/reason、冻结的 `next_call_index`（需要时加 `stop_reason`），不保存提醒正文。提醒只附加到单次输入、不写入对话历史，也不改变候选发布条件；`decision=stop` 或任意反馈文本本身不会触发提醒。trace/正文采集被截断或日志写失败时，“没有看到提醒”不能反推“没有发送”；应以实际 wire 正文和该事件为准。

## 什么时候写，谁写

主探索和模型资源关闭 → 导出成功或失败 → 最终 TaskResult 落盘 → ExperienceService 独立提炼 → 程序验证并追加 → 返回原结果。

模型仅提炼 category/tags/description/summary/detail 和 1–3 个材料 EID；程序生成经验 ID、来源任务标识及哈希/行号引用，自动写库。新卡只给下一任务使用，不改当前任务判定、耗时或已发布包。DONE、BLOCKED、NEEDS_INFORMATION、EXHAUSTED、FAILED 只有存在可信完整材料才学习。取消、只读、库损坏、无可用材料均不发学习请求；模型也可返回空经验。

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

每分类正文是一个 JSON 对象 `{"schema_version":2,"category":"workflow","items":[...]}`，合并存储多条卡，不为每坑创建文件。每卡保留 id/category/tags/summary/detail/source_task_id/evidence_refs 和可选 triggers，新卡增加 description（1–160 字符）。完整卡最多 4096 字节，三个正文总计最多 1 MiB。

index.json 为可丢弃缓存，包含 schema_version=1/category/source_hash/items，items 只有 id/description。正文是唯一事实源，索引由程序生成，不由模型独立维护。缺失、过期、损坏或与正文投影不符时在内存重建，记录 experience.index；只读任务不修写缓存。正文已写入但缓存失败仍返回 written，同时记录 index_update_failed；后续任务从正文恢复。缓存总上限 1 MiB。

根旁路锁保护快照读取和追加，锁内重读、校验、去重、限额测量后原子替换分类正文。任务中的库视图固定，新卡给下一任务使用。损坏正文关闭该任务经验功能，不覆盖旧数据、不影响主复现。只追加和同 ID 精确去重，不合并、衰减、删除或更新旧卡；description 不改变现有 ID 算法。triggers 仍从引用的实际受控事实生成，作为来源元数据保留，不控制目录可见性。程序验证来源，不保证建议语义或效果。

experience_file 配置兼容旧 `.json` 单文件和新目录。旧 schema=1 可读；旧卡无 description 时从 summary 派生简短目录文字，保留原卡和 ID。只读不升级；第一次有效追加时文件模式在锁内升级为 schema=2，旧二进制不保证能读新版格式。两种路径同时存在时默认使用新目录，显式指定旧路径仍使用旧库。

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

学习请求明确要求一个 JSON 对象，兼容 `response_format=json_object` 的提示词约束。非空 experience 必须有 category、tags、description、summary、detail、evidence_ids 六个字段；正文提到 EID 不能替代 evidence_ids 数组，程序不从正文猜引用或自动补字段。HTTP 拒绝或三次网络尝试耗尽返回 `provider_error`。每个 `model.attempt` 在收到 HTTP 响应时记录 `http_status_code`，没收到响应则不虚构状态码。

校验失败单独诊断，保留主任务结果，不写入坏卡、不追加纠错请求。字段级长度错误只返回受控的 `field_errors`（field、actual_length、limit），它同时出现在 `experience.learning` 的 `LearningResult` 中；不会把 165 字符的旧输出截短后入库，也不会用第二次请求替换第一次审查。`description` 的硬上限仍是 160 个 Python 字符，提示词另外建议 `description 80-120` 留出余量，但软目标不构成新的拒绝条件：

| learning.code | 含义 |
| --- | --- |
| invalid_missing_fields | 缺少必填字段，missing_fields 列出固定 schema 内的字段名 |
| invalid_extra_fields | 多出未允许的字段，不把任意额外字段名写进日志 |
| invalid_structure | 顶层/experience 对象结构错误 |
| invalid_json | 无法解析为严格 JSON 对象 |
| invalid_fields | 分类、标签、摘要或详情类型/取值无效；description 长度错误不再折叠到这里 |
| invalid_field_length | `description` 超过 160 字符；field_errors 最多一项，只有 field/actual_length/limit，不携带字段值或模型原文。按脱敏后的 Python 字符数计算，中文不按 UTF-8 字节计算 |
| invalid_evidence_ids | 引用 ID 不存在、重复、数量或类型不合法 |
| invalid_evidence_changed | 冻结证据文件、哈希、行号或 ID 无法再验证 |
| invalid_card_size | 最终卡片超过 4096 字节；needed_bytes/limit_bytes/exceeded_bytes 给出实际大小、上限和超出量 |
| invalid_model_output | 模型回复截断、空输出或协议不兼容，具体协议码见 model.attempt |
| provider_error | HTTP/网络请求失败，状态码见 model.attempt |
| internal_error | 未归入上述校验原因的参数/运行时异常，不假称经验格式错误 |

CLI learning、experience.learning 事件及 trace 学习汇总共享 code/missing_fields；长度错误另带 field_errors。learn span 也记录最终结果码。只记录受控诊断，不保存原始异常文本或任意模型字段。

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
