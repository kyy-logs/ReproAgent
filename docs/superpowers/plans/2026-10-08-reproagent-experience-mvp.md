# ReproAgent Experience MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 自动积累可追溯的踩坑经验，后续任务按“最多3条摘要 → 按需1条详情”辅助探索，并保持原有复现验收与结果不变。

**Architecture:** AgentScope 2.0.9继续承担模型、Toolkit和任务内ReAct；App注入经验组件，Controller安排库快照、首次探索提示和主结果封存后的学习。模型提出经验，程序冻结来源、检查、去重并原子写入；Verifier不加载经验。

**Tech Stack:** 工具Python >=3.11、AgentScope ==2.0.9、httpx、pytest、标准库JSON/文件锁；目标Python/pytest及独立replay不增加依赖。

**Spec:** [经验最小版设计](../specs/2026-10-06-reproagent-experience-evolution-design.md)、[项目架构与时序](../specs/2026-10-05-reproagent-architecture-design.md)。依据本地文档提交 `a2aae15`；实现前重新检查工作区，不覆盖其他代理的代码或文档删除。

状态：2026-10-08编写，尚未执行。沿用用户选择的“当前会话逐项执行”，接口依赖按Task 1–5串行推进。本计划不新增探索阶段约束，不运行真实模型或发布GitHub修改。

## Global Constraints

- 主分类固定为 `framework / model / workflow`，每卡一个分类，不自动创建子分类；tags最多5个非空短标签。项目行为/平台限制归framework，模型理解/生成错误归model，组织方式/资源调度问题归workflow。
- TaskRequest末尾增加 `experience_file: Path | None = None`、`learn_experience: bool = True`；未配置路径时全部关闭，false表示只读评测。相对路径以任务配置文件目录为基准。
- 库及其 `<experience_file>.lock` 不得位于目标仓库、任务输出或修复仓库内，按真实解析路径检查，包含符号链接/目录联接边界。
- 库文件最多1MiB，根对象严格为schema_version=1、items；每卡最终UTF-8 canonical JSON最多2048字节，七个字段保持规格定义。未知schema、重复JSON字段、非法字段、损坏记录不得被自动覆盖。
- 每任务固定一次库快照；不存在为空且不在加载时写库。摘要最多3条，详情最多成功读取1条；全部模型可见经验JSON累计最多2048字节，按实际UTF-8序列化计算，不能截断文本。
- 原始Issue分析完成后、首次explore前才注入摘要；候选重试与契约修订不重新检索/注入。分析、契约修订请求和Verifier不自动加载经验；经验不能成为当前原版引用。
- 探索逻辑模型请求计步，工具内部不重复take_step；读取经验沿用已有最后3步的发布预留。未启用保持六工具，读取视图有效时增加一个只读工具，不产生PhaseResult。
- 主探索/模型资源关闭、导出成功或失败、最终TaskResult落盘之后，Controller.run返回之前同步学习；新经验只供下一任务。学习不修改TaskResult、主duration或已封存包。
- 学习独立30秒上下文，最多一次逻辑模型请求、3次HTTP尝试，不加格式纠正循环。SDK请求用途明确为learning、无tools；使用同一模型配置、新客户端，最后关闭。不得复活主探索AgentState或复用主预算。
- 取消、只读模式、库读取失败或证据不可用时不发学习请求。DONE/BLOCKED/NEEDS_INFORMATION/EXHAUSTED/FAILED有有效原版观察时均可提炼，没有可记录现象允许返回空。
- 学习输入最多8192字节；仅允许冻结Issue、校验过的原版引用、候选、original执行及受控错误/测量。禁止整目录/整报告汇总、fixed执行、隐藏修复、参考测试、答案、历史经验。
- 先一次性冻结内部learning/evidence.jsonl，再发学习请求；最终卡引用该不可变文件。可变events只摘取seq、受控错误码和payload摘要，不引用其整文件哈希。所有输入/卡片/事件对已知凭据脱敏。
- 去重键为规范化category、tags、summary的canonical JSON SHA256，id使用exp_加完整哈希；detail不同也不覆盖同ID。稳定旁路锁使用非阻塞系统锁，保护重新读取—检查—去重—追加—atomic_write，不删除锁文件。
- 学习结果和HTTP/token/已知费用小计/未知费用次数/耗时独立记账，整体评测成本含学习。report.md仅增加实际读取ID，不新增用户核对文档，不声称已证明收益。

## Review Focus

1. **库路径越界或中途变化**：链接指向原版/修复目录必须拒绝；库被另一任务更新也不影响本任务的固定视图。Task 1/2/4测试。
2. **可选经验挤占事实容量**：摘要不能迫使已能容纳的Issue/契约被删掉或停止；仅展示实际装入请求的ID，未知ID不能读。Task 2测试。
3. **学习泄漏修复或引用失效**：只使用original记录，参考答案/历史经验不进入提炼；追加学习事件后证据哈希仍有效。Task 3/4测试。
4. **耗尽/取消/收尾失败**：学习独立预算但共享取消信号；学习超时、损坏库、导出失败不得抹掉原结果，客户端必须关闭。Task 3/4测试。
5. **库更新及评测失真**：锁竞争、原子替换失败、重复或超容量不能丢旧数据；冻结评测不写库，成本不漏算或重复计算。Task 1/5测试。

---

## File Structure

| 文件 | 改动与职责 |
| --- | --- |
| `src/reproagent/experience.py`（新增） | 经验DTO、严格校验、库快照/检索、固定视图、非阻塞追加、学习材料与经验生命周期组件；不引入服务/数据库 |
| `src/reproagent/prompts/extract_experience.md`（新增） | 一次结构化提炼，固定分类、观察与建议分离、允许空、不输出系统ID/存储路径 |
| `core/models.py`、`cli.py` | 向后兼容配置、ExperienceSummary、首轮AgentContext提示和CLI学习汇总 |
| `app.py`、`core/controller.py` | 注入/调度经验组件；保留终态、关闭、导出、落盘、学习的顺序 |
| `adapters/agentscope/tools.py`、`explorer.py`、`runtime.py`、`middleware.py` | 第七个只读工具、有效工具白名单、固定视图和首轮摘要；不修改SDK |
| `adapters/agentscope/model_factory.py`、`gateway.py`、`core/protocol.py` | learning结构化请求用途和单独记账，保持已有响应/重试/输出限制 |
| `exporter.py`、`reporting.py` | 从封存前事件提取实际读取ID，按既有报告模板显示，不打包经验库/学习材料 |
| `evals/run.py`、`schema.py`、`swt_bench/run.py`、`swt_bench/results.py`、`swt_bench/__main__.py` | 配置只读经验评测、冻结库哈希、单列学习成本并保留既有分母和模型来源 |
| 新单元/集成测试、`docs/experience.md`、示例配置 | 验证边界、SDK真实链路、生命周期和使用说明 |

不提前修改Verifier的判断规则，不增加经验评分、语义去重、自动淘汰、向量检索、后台学习或探索阶段状态机。若experience.py出现两项可独立演进的过长职责，仅拆私有实现文件，保留本计划公共接口，不扩展产品范围。

## Shared Interfaces

以下为Task 1–4的交接契约，类字段用关键字构造；已有DTO新字段均追加在末尾并有默认值。

```text
core.models.ExperienceSummary(id: str, summary: str)                 # frozen
core.models.AgentContext.experience_summaries: tuple[ExperienceSummary, ...] = ()

experience.ExperienceCard(id, category, tags: tuple[str,...], summary, detail,
    source_task_id, evidence_refs: tuple[EvidenceRef,...])             # frozen
experience.ExperienceSnapshot(state: str, content_hash: str,
    cards: tuple[ExperienceCard,...])                                 # frozen
experience.LearningInput(payload: dict, evidence_map: dict[str,EvidenceRef],
    source_task_id: str, event_cutoff: int)                            # frozen envelope
experience.LearningResult(code: str, experience_id: str = '', duration: float = 0,
    http_attempts: int = 0, usage: dict = {}, known_cost_subtotal: float = 0,
    unknown_cost_attempts: int = 0)                                   # mutable defaults用factory

load_experience_snapshot(path: Path, *, secrets: tuple[str,...] = ()) -> ExperienceSnapshot
append_experience(path: Path, card: ExperienceCard, context: CallContext) -> str
validate_experience_path(path: Path, request: TaskRequest,
    fixed: FixValidationRequest | None) -> Path

ExperienceView(snapshot: ExperienceSnapshot, *, secrets: tuple[str,...] = ())
    .select(issue_text: str, target_modules: tuple[str,...]) -> tuple[ExperienceSummary,...]
    .restrict_summaries(ids: tuple[str,...]) -> None                   # 限实际已展示ID
    .read(identifier: str) -> dict                                   # id/summary/detail；失败ValueError
    .read_ids: tuple[str,...]

build_learning_input(store: TaskStore, result: TaskResult,
    context: CallContext, *, secrets: tuple[str,...] = ()) -> LearningInput | None
validate_experience(response: dict, material: LearningInput,
    store: TaskStore, *, secrets: tuple[str,...] = ()) -> ExperienceCard | None
extract_experience(material: LearningInput, gateway: ModelGateway,
    context: CallContext, store: TaskStore,
    *, secrets: tuple[str,...] = ()) -> ExperienceCard | None

LearningGatewayFactory: Callable[[TaskStore], ModelGateway]           # 每次新建，可aclose
ExperienceService(request: TaskRequest, store: TaskStore,
    learning_gateway_factory: LearningGatewayFactory | None,
    *, secrets: tuple[str,...] = ())
    .prepare(fixed: FixValidationRequest | None, context: CallContext) -> None
    .summaries(issue: IssueDescription) -> tuple[ExperienceSummary,...]
    .view: ExperienceView | None
    .learn(result: TaskResult, main_context: CallContext) -> LearningResult
```

snapshot.state为ready/missing/store_error；missing是可学习的空视图，store_error使本任务关闭读取和写入。LearningResult.code覆盖written/empty/duplicate/invalid/timeout/busy/store_error以及skipped_disabled/skipped_read_only/skipped_cancelled/skipped_no_evidence。数据来源限定标识使用task_id和解析任务目录哈希，不把绝对机器路径加入模型可见详情。

## Task 1: 经验契约、配置、严格读库与安全追加

**Files:** Create `src/reproagent/experience.py`, `tests/unit/test_experience_store.py`; Modify `src/reproagent/core/models.py`, `src/reproagent/cli.py`; Test `tests/unit/test_models.py`, `tests/integration/test_cli.py`。

**Interfaces:** Consumes现有parse_json/canonical_bytes/EvidenceRef/atomic_write/路径检查；Produces Shared Interfaces中的ExperienceCard、ExperienceSnapshot、LearningResult、load_experience_snapshot、append_experience、validate_experience_path，以及配置字段和ExperienceSummary。

- [x] **Step 1: 写失败测试。** `test_old_request_defaults_disable_experience`断言旧request解码得到experience_file=None、learn_experience=True；`test_relative_experience_path_uses_config_directory`断言换cwd不改变解析结果。`test_category_and_json_are_strict`覆盖三个合法分类、未知分类、未知/重复字段、NaN、bool schema_version、超过5个tags、空文本和非法引用；`test_card_utf8_limit_includes_metadata`以中文文本覆盖最终2048字节边界。短标签限制固定为最多64个Unicode字符并写入使用说明。
- [x] **Step 2: 跑RED。** `.venv\Scripts\python.exe -m pytest tests/unit/test_experience_store.py -q`；确认因缺少新行为失败，不是临时目录或解释器错误。
- [x] **Step 3: 实现数据和读库。** 追加配置字段，路径按config目录解析；有界读取最多1MiB+1字节，严格校验每条七字段卡片与ID规范。缺文件不创建文件，损坏/未知版本返回store_error而非空ready。读库时对已知凭据脱敏；发现需要改变存储卡片文本的记录不作为可用卡，不能在加载时改写库。
- [x] **Step 4: 写更新/边界失败测试。** `test_duplicate_preserves_old_detail`断言规范化空白、tag去重排序后同ID跳过且旧detail不变；`test_busy_writer_preserves_library`由独立子进程占旁路锁，断言busy且字节不变；`test_atomic_replace_failure_preserves_library`注入replace失败，旧库仍可读。`test_library_limit_checked_after_append`验证新增后超过1MiB不写。`test_library_cannot_live_in_repo_output_or_fixed`与可创建链接时的绕根场景断言拒绝，读取之前无副作用。
- [x] **Step 5: 实现追加。** Windows在稳定.lock上锁定一个初始化字节，POSIX使用非阻塞系统锁；保留锁文件，只释放句柄。锁内重新读取完整schema、去重、测量新文件容量，调用既有atomic_write；操作前检查独立学习deadline和取消。失败映射为busy/store_error/timeout，不自动重建库。
- [x] **Step 6: 验证GREEN。** 跑该新单测、序列化和CLI集成；断言旧配置行为不变、上述边界通过，链接权限不支持时明确skip。
- [x] **Step 7: 仅提交本任务文件。** 建议提交 `feat: add bounded experience records and atomic storage`，不stage其他代理的改动。

## Task 2: 固定视图、摘要匹配与SDK渐进读取

**Files:** Modify `experience.py`, `core/models.py`, `adapters/agentscope/tools.py`, `explorer.py`, `runtime.py`, `middleware.py`, `prompts/explore.md`; Create `tests/unit/test_experience_view.py`, `tests/integration/test_agentscope_experience.py`。

**Interfaces:** Consumes Task 1快照/卡片/摘要；Produces ExperienceView及可选read_experience工具。`AgentScopeExplorer`和工厂增加可选experience_view关键字；`build_toolkit(..., *, experience_view=None)`保持默认六工具；Runtime接受相同固定视图、从实际Toolkit确定本阶段允许名集合。

- [x] **Step 1: 写检索失败测试。** `test_matching_is_bounded_and_stable`用4张卡，断言最多3摘要、0分卡不返回、同分按ID排序；`test_snapshot_does_not_follow_library_updates`加载后改库，断言检索/详情仍来自旧卡。匹配算法固定为规范化tag命中与summary关键词命中的去重计数；英文数字词按正则分词，中文连续片段用相邻双字，目标模块加入查询，大小写/空白归一化。不使用向量或模型检索。
- [x] **Step 2: 写详情失败测试。** `test_only_displayed_id_can_be_read`、`test_one_successful_detail_per_task`、`test_utf8_visible_budget_is_shared`断言未知ID拒绝、成功一次后再次拒绝、摘要列表JSON与完整详情JSON合计<=2048字节；不足则拒绝，不截文本、不占成功次数。
- [x] **Step 3: 跑RED。** 运行两个新增测试文件，先验证规则缺失导致失败；复用existing SDK runtime测试中的environment/tool_reply和projects/facts构建真实SDK+MockTransport，不能手写下一步绕过SDK。
- [x] **Step 4: 实现视图。** 实现select/restrict_summaries/read和任务级read_ids；模型只看到摘要id/summary、详情id/summary/detail，不发送全库、来源任务目录或证据全文。选择少于3条以适应容量。
- [x] **Step 5: 实现SDK只读工具和白名单。** 新工具按现有ToolBase/Pydantic接口实现，输入只含id，输出ToolChunk；读工具自己检查预算/取消但不take_step。同步有效工具集、权限检查、整份响应校验、纠错提示和发布预留的reader集合；ReadExperience不继承结束型_DomainTool，不调用PhaseGate.finish。disabled/store_error仍恰好六工具，ready/missing为七工具。
- [x] **Step 6: 实现首轮输入。** AgentContext末尾追加experience_summaries；Runtime仅在首个explore消息中加入历史建议。若原Issue/契约可容纳而加经验后超32KiB，先减少/去掉可选摘要，调用restrict_summaries同步实际展示ID；不得删Issue/契约或因经验额外停止主任务。后续阶段不重新注入，SDK历史保留已读详情。
- [x] **Step 7: SDK验收。** `test_native_sdk_reads_one_experience_then_publishes`断言真实请求包含第七工具、工具结果进入下一请求、没有phase_end、每逻辑请求只计1步；`test_revised_phase_does_not_reload_experience`断言库只读一次、摘要仅首轮新增、跨阶段一次详情额度。`test_optional_summary_cannot_displace_issue`覆盖接近32KiB输入。`test_experience_is_not_source_evidence`断言经验ID/旧任务引用不能通过当前EvidenceLedger或revise_contract。原始六工具和第20步预算回归继续通过。
- [x] **Step 8: GREEN并提交。** 运行新增测试及test_agentscope_runtime/domain_tools/explorer/backends；建议提交 `feat: add progressive experience reads to SDK exploration`。

## Task 3: 冻结原版材料与独立SDK学习请求

**Files:** Modify `experience.py`, `adapters/agentscope/model_factory.py`, `gateway.py`, `core/protocol.py`; Create `prompts/extract_experience.md`, `tests/unit/test_experience_learning.py`, `tests/integration/test_agentscope_learning.py`。

**Interfaces:** Consumes Task 1卡片/追加、现有TaskStore不可变记录与ModelGateway；Produces LearningInput、build_learning_input、validate_experience、extract_experience以及SDK purpose=learning。

- [x] **Step 1: 写来源失败测试。** `test_learning_input_excludes_fixed_and_answers`在同一任务保存original/fixed运行、含唯一marker的修复版、报告和历史经验，断言提炼payload及冻结JSONL无禁止marker。只从允许Issue/契约引用、候选、original执行和事件白名单取材，不读取reference文件。`test_learning_evidence_survives_event_append`在封存后追加事件，断言E1对应的整文件哈希及行号仍有效。
- [x] **Step 2: 写提炼校验失败测试。** `test_learning_ids_are_mapped_by_program`断言模型只选1–3个唯一EID，最终来源任务/exp哈希由程序提供；未知ID、篡改文件、非当前原版来源、重复/额外JSON字段、非法分类和最终卡>2048字节返回invalid；`test_null_experience_is_empty`断言无写入。`test_learning_input_overflow_skips_instead_of_truncating`断言实际请求材料和指令/schema的用户JSON<=8192字节，完整有用片段无法保留时不请求。
- [x] **Step 3: 跑RED。** 运行新单测；错误必须定位到新行为缺失，避免把伪造fixture当产品失败。
- [x] **Step 4: 实现材料冻结。** 固定来源task_id+任务目录摘要；读取时校验来源绑定/哈希/行号，受控事件摘seq/code/payload测量，忽略未知字段和自由文本指令。将脱敏JSONL一次写入learning/evidence.jsonl并禁止覆盖，EID映射只引用该文件；写学习事件发生在它之后。先保留失败观察和必要预期/候选，非必要材料可整体不选入，不截断保留片段。若来源损坏、候选不可读、无可信完整观察则返回None。
- [x] **Step 5: 实现一次提炼和验证。** extract只调用gateway.complete一次；messages为提炼system和material.payload，response_kind=learning，无工具/探索历史。提示词使用三个分类及重叠选择示例，要求条件/观察/建议/不确定性，允许experience=null。parse_json与专用schema严格检查，不请求模型修正格式；已知凭据脱敏后测量最终卡并生成规范化ID。
- [x] **Step 6: 扩展SDK用途与保护。** PURPOSES和purpose_for显式接受learning，不把它映射为contract；保留contract/action兼容，不将未知用途静默伪装。结构化finish_reason/正文完整性、1MiB响应限制、max_output_tokens、HTTP最多3次和每HTTP记账继续生效；learning带tools立即拒绝。
- [x] **Step 7: 真实SDK离线验收。** `test_learning_request_has_no_tools_and_own_deadline`用真实gateway+MockTransport返回经验，断言用途记录learning、无tools、主步数不变；`test_learning_retry_is_not_protocol_correction`模拟两次503再成功，断言3HTTP/1logical，坏JSON只1logical且不额外纠正。`test_learning_cancellation_and_timeout_close_clients`模拟停滞请求，断言独立30秒deadline/取消生效且aclose执行。
- [x] **Step 8: GREEN并提交。** 新单测/SDK学习及既有model_factory/gateway/protocol回归通过；建议提交 `feat: extract bounded experiences from sealed original evidence`。

## Task 4: Controller统一生命周期与自动写入

**Files:** Modify `experience.py`, `app.py`, `core/controller.py`, `adapters/agentscope/explorer.py`; Create `tests/integration/test_experience_lifecycle.py`; Test `tests/unit/test_controller.py`, `tests/integration/test_agentscope_backends.py`。

**Interfaces:** Consumes Task 1–3全部公共契约；Produces ExperienceService、Controller.learning_result和可选经验注入。`create_controller(..., learning_gateway_factory=None)`在原有关键字末尾扩展；Controller末尾增加可选experience_service=None，旧直接构造仍可运行。

- [x] **Step 1: 写时序失败测试。** `test_learning_starts_only_after_main_result_is_sealed`在MockTransport收到learning请求时检查：explorer及主gateway均已close、task.json为最终状态、导出manifest存在且哈希稳定。`test_summary_load_occurs_after_analysis_before_first_explore`断言准备只读库一次、contract/verdict请求无经验marker、首次探索有匹配摘要。测试非命中/disabled默认路径不增加模型调用。
- [x] **Step 2: 写结束场景失败测试。** 参数化DONE/BLOCKED/NEEDS_INFORMATION/EXHAUSTED/FAILED的有效原版材料、CANCELLED、store_error、只读和无材料。断言取消/只读/损坏/无材料零learning HTTP，其他状态至多一次。`test_exhausted_main_budget_does_not_cancel_learning_budget`主预算超时但取消信号未置位，断言学习有独立30秒。`test_learning_failure_preserves_task_and_package`覆盖invalid/timeout/busy/store_error，原TaskResult、task.json、包字节不变。
- [x] **Step 3: 跑RED。** 运行新生命周期文件，确认缺少加载/封存后调用导致断言失败；不能仅检查方法被mock调用过。
- [x] **Step 4: 实现App注入。** 配置路径时创建ExperienceService，产品学习工厂从同一model配置与transport创建新的AgentScopeModelFactory/Gateway；可用BudgetedGateway脱敏/记逻辑调用。若自定义gateway被注入且启用学习，必须提供learning_gateway_factory，缺少时配置阶段拒绝，不意外调用真实API。未配置不改变现有factory调用参数。
- [x] **Step 5: 实现准备/首次探索时序。** Controller准备时调用service.prepare(fixed,context)校验三个保护根并固定视图；原Issue分析之后调用summaries一次，把视图交给SDK Explorer。SDK默认厂支持可选experience_view；自定义厂在启用时需支持该关键字，disabled不新增关键字。读取ID事件写在导出之前，摘要和详细内容不存入报告。
- [x] **Step 6: 实现封存后学习。** 保留现有close_explorer→export→最终state落盘顺序，在return前await service.learn。新CallContext使用BudgetLimits(task_timeout_seconds=30)和同一个cancel_event；不能复制主Budget的过期deadline。预算从收集材料前开始，贯穿模型/校验/加锁/写入，各步检查；超时/取消后不启动写入，关闭新资源。系统文件操作按有限本地收尾处理，不声称能强制中断阻塞的内核I/O。
- [x] **Step 7: 实现结果隔离和记账。** service.learn捕获预期存储/模型/校验错误并返回LearningResult，Controller额外保护学习收尾异常不覆盖主结果；任务目录不可写时以CLI诊断说明学习未记录。HTTP/token统计从purpose=learning的model.attempt计算，不再叠加model.completed；写experience.learning事件，Controller.learning_result保存本次返回汇总。不得调用state重写主duration/终态或重新export。用户在学习中取消只停止学习，保留封存记录。
- [x] **Step 8: GREEN并提交。** 生命周期、新SDK经验、原Controller/backends/取消/导出回归通过；建议提交 `feat: learn experiences after sealing reproduction results`。

## Task 5: 报告、只读评测、端到端与使用文档

**Files:** Modify `cli.py`, `exporter.py`, `reporting.py`, `evals/run.py`, `evals/schema.py`, `evals/swt_bench/run.py`, `evals/swt_bench/results.py`, `evals/swt_bench/__main__.py`, `docs/agentscope.md`, `docs/implementation-status.md`, `README.md`; Create `docs/experience.md`, `examples/task.experience.json`, `tests/integration/test_experience_end_to_end.py`, `tests/integration/test_experience_evaluation.py`; Test `tests/unit/test_reporting.py`, `tests/unit/test_eval_runner.py`, `tests/unit/test_swt_results.py`, `tests/integration/test_cli.py`, `tests/integration/test_swt_batch.py`。

**Interfaces:** Consumes Controller.learning_result、read事件和经验文件内容哈希；Produces CLI附加learning汇总、report.experience_read_ids以及可冻结的评测配置。TaskResult、已有report模板和旧评测结果可读性保持兼容。

- [x] **Step 1: 写报告/CLI失败测试。** `test_report_contains_only_actually_read_ids`断言有摘要但没读详情不显示ID，仅统计event_cutoff之前成功读的ID；Markdown展示可放现有“限制与不确定项”节，不增加新模板变量。`test_cli_reports_learning_separately`断言原status/退出码与TaskResult不变，附加learning.code/duration/usage/费用汇总，disabled可省略该字段。学习新增卡和learning/evidence.jsonl不进入已发布manifest。
- [x] **Step 2: 实现展示。** Exporter从已有事件截止提取实际读ID到report.json，reporting按模板输出ID；不复制库、旧经验来源或提炼材料。CLI只在Controller.run之后读取Controller.learning_result显示，无第二次学习调用。
- [x] **Step 3: 写只读评测失败测试。** `test_frozen_experience_round_never_writes`在run_case/run_batch配置经验文件，断言learn_experience=False、库哈希运行前后不变、无learning请求。`test_changed_library_invalidates_frozen_round`变更库后下一case不能悄悄使用新库；保留分母，报告source_comparison_status/原因。`test_total_cost_includes_learning_once`断言主/学习usage分别可取，整体cost含所有真实HTTP一次，未知费用保留unknown，不合并最佳结果。
- [x] **Step 4: 实现评测入口。** run_case末尾扩展可选experience_file=None、learn_experience=False；EvalResult末尾追加experience_library_hash、experience_read_ids、learning字典默认值，不改旧字段含义。run_batch/CLI可选传只读经验路径（CLI参数为--experience-file），拒绝库位于轮次输出/原版/修复目录，固定起始库哈希，写入configuration及其哈希并在每case前核对；使用已有分母保留机制，库变化时保存EXPERIENCE_SOURCE_CHANGED并标记该轮不可比较。默认评测无经验，学习集由普通任务开启写入产生库，评测集不自动学回。原usage/http_attempts/cost继续表示全部真实请求，新增learning汇总只用于分解，不再重复加到总数；results.py单列learning_duration/learning_http_attempts/learning_token_total，未知费用不冒充精确值。检查run_one测试注入签名的兼容性，disabled不增加kwargs。
- [x] **Step 5: 端到端失败测试。** `test_task_a_learns_and_task_b_reads_without_changing_verdict`使用真实SDK+MockTransport和真实pytest：任务A得到原版观察并写卡，A报告无新卡；新任务B命中摘要、调用一次详情、发布候选、完成原版/重复/可选修复检查，verdict请求不直接加载经验，导出包在新副本replay。`test_cancelled_task_never_teaches`与`test_advice_cannot_override_current_evidence`覆盖取消、历史误导建议/伪造引用/当前断言依据不足；不能因为经验说有效而绕过验收。
- [x] **Step 6: 验证绿色并更新使用文档。** 新端到端、评测、reporting/CLI/既有SDK链路通过；说明三分类选择规则、开启配置、读取/写入时机、总容量、额外30秒、只读评测、建议可能错误及第一版不自动维护旧卡。示例使用目标仓库之外的工具侧路径，不提交实际经验数据、API密钥或机器专属配置。
- [x] **Step 7: 最终验证。** 设置REPROAGENT_RG_PATH后运行 `.venv\Scripts\python.exe -m pytest tests/unit tests/integration -q -rs` 和 `.venv\Scripts\python.exe -m pip check`；wheel/replay测试包含在现有套件。Windows/Linux矩阵沿用现有工作流，不降低断言或因为经验未启用跳过核心链路。记录实际结果，不复用历史460/468通过数。
- [x] **Step 8: 提交并记录实现状态。** 建议提交 `feat: report experience usage and freeze experience evaluations`；实现计划逐项勾选，注明真实模型A/B尚未执行。发布/push按用户授权另行处理，保留当前工作区其他改动。

## Acceptance and Handoff

完成条件是存储、渐进读取、自动提炼、生命周期、只读评测和离线端到端均通过；不是仅新增文件或单测模型返回假经验。学习与主任务两套预算/结果、引用固定、SDK工具白名单及报告截止是必须审查的边界。

真实能力评测另行执行：用不重叠学习集产生少量经验，冻结库后对相同新任务做关闭/开启经验对照，固定模型、主预算、源码和环境，保留所有失败。记录差分有效交付、误报、重复读取、无效调用、步骤及额外成本；未经此对照不宣称提高复现率。本计划不授权真实模型费用或读取评测答案来生成经验。

## Plan Self-Review

- 规格§1–4存储/自动写入对应Task 1/3/4；§5渐进加载对应Task 2/4；§6生命周期/记账对应Task 4/5；§7边界和§8评测对应各任务的Review Focus与Task 5。
- 所有新增共享类/字段/方法已在Shared Interfaces或所属任务定义；既有接口新增参数和字段均有默认值。自定义factory开启经验时的要求明确，默认测试注入不触发真实模型。
- 五项Review Focus分别有命名行为测试；覆盖Unicode字节限制、真实跨进程锁、库漂移、可选输入挤占、隐藏材料、引用稳定、取消和成本重复统计。
- 计划按五个可独立验收的交付块拆分，保留RED→GREEN和提交步骤，不复制实现函数体。最后的真实A/B评测与代码功能验收分开。
- 执行方式沿用当前会话逐项实现；本次仅写计划，未启动产品代码实现。

## Execution verification (2026-10-08)

All five tasks implemented. Full offline suite: 549 passed, 5 skipped in 664.81s; skips concern Windows links/interpreter path. pip check: no broken requirements. Real-model A/B has not run. Whole-branch independent review follows before local integration.
