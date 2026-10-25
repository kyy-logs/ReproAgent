# Experience Draft Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 探索者随候选提交有适用条件和来源的踩坑草稿，复用现有学习调用独立审查并由程序写库；在可恢复阻塞后最多提示查一次历史。

**Architecture:** 保留 ExperienceService、目录快照、材料投影、卡片校验与分类存储。仅增加任务内草稿状态、审查信封和一次提示状态；新模式复用一条 learning 请求，不新增总结调用或常驻 Agent。原有 legacy 模式继续默认启用。

**Tech Stack:** Python >=3.11、AgentScope 2.0.9、现有 dataclasses/Pydantic/TaskStore、pytest/httpx MockTransport；无新运行依赖。

**Spec:** [轻量踩坑草稿、审查与按需读取设计](../specs/2026-10-25-experience-draft-review-design.md)。执行者须先通读该设计，尤其 §§4–9 与字段分工 §6.1。

## Global Constraints

- `TaskRequest.experience_mode` 精确为 `legacy` 或 `draft_review`，默认 `legacy`；无效值在模型调用和输出创建前失败。
- 不改变 Verifier 判定、候选源码/哈希、契约、fixed 隔离、源码引用资格和主预算硬上限。
- `lesson_draft` 四字段为 situation/observation/lesson/source_ids，完整 UTF-8 JSON 最多 1024 字节，来源 1–3 个不同的当前任务真实 ID。
- 审查总输入最多 8192 字节，最终卡最多 4096 字节；沿用一次逻辑 learning 请求、最多三次 HTTP 尝试、30 秒独立工作期限与已有资源关闭期限。不增加格式修复调用。
- 分类仍为 framework/model/workflow；索引仍由正文派生 id/name/description，目录响应包含 category。不新增检索模型或按错误码筛卡。
- 目录每页最多 24 条/8192 字节，每任务最多 12 次/16384 字节；正文最多成功读取两次。新卡下一任务可见。
- 运行中提醒每任务至多实际交付一次，完整提醒 JSON 不超过 256 字节。按下一模型调用前余量检查 `remaining_steps >= RESERVED_FOR_PUBLISHING + 3`，当前为 6。禁止在发布预留、读权限拒绝、配额不足、取消或终态催读。
- 经验关闭时读写全停；学习只读时仍可读取，但不收集草稿、不审查、不写库。失败任务可以写，没草稿则跳过，不补总结调用。
- 不读取真实用户经验库、密钥或宿主认证，不安装目标依赖，不运行付费模型，不 push/merge。提交仅在执行会话明确授权后逐文件进行，禁止 git add -A。
- 当前工作区已有大量未提交的接入实现、修复、文档整理和 eval 改动，均为输入；不得 reset、覆盖、顺带提交或恢复已删除文档。

## Review Focus

1. SDK 外层参数校验先拒绝坏草稿，导致合法候选无法提交：Task 1 必须用真实 Toolkit 覆盖任意类型/超限/多字段草稿，而非只测内部 parser。
2. 同一 SDK call ID 多次出现、引用未来运行、草稿引用自身：Task 2 必须按实际记录顺序消歧，不能靠字符串命中给 EID。
3. 模型给出存在的 EID，却只指向草稿或 agent_hypothesis：Task 3 必须程序拒绝“只有主张、没有独立事实”的引用组合。
4. `take_step()` 造成提醒预算偏一、容量不足吞掉关键反馈：Task 4 测调用前 5/6 步边界与最终真实输入，容量不够只舍弃提示。
5. 导出失败、取消、坏库、写卡成功但索引失败：Task 3/5 验证主结果不变、学习和费用不重复、库与索引保留现有恢复语义。

## 执行环境与文件地图

核对基线为 `E:/ReproAgent`，`feat/ai-tool-integration`，HEAD `cef94f1` 加工作区；不是要求 checkout/reset 到该提交。执行前保存 status 和目标文件哈希，并确认 `reproagent.__file__`。

如选择独立 worktree，须按 worktree 技能处理，显式带入经确认的本 spec/plan 和本任务所需未提交基线；不能从裸 HEAD 开始后假设已有接入修复或本文档。

| 文件 | 责任 |
| --- | --- |
| core/models.py、app.py | 模式字段校验、产品策略组装；不改验收规则 |
| experience_drafts.py（新增） | 有界草稿、单任务状态、旁路保存与受控诊断 |
| adapters/agentscope/tools.py、middleware.py、explorer.py、runtime.py | 实际调用关联、可选草稿接收、同任务状态传递 |
| learning_material.py | 复用现有采集和投影，关联草稿来源、优先装入事实并一次冻结 |
| experience_review.py（新增）、prompts/review_experience.md（新增） | 审查信封/schema/一次模型调用，复用既有卡校验 |
| experience.py | 模式分发、原学习时序/写库、审查结果诊断 |
| adapters/agentscope/experience_hint.py（新增） | 有界的一次提示状态；无模型或存储层 |
| core/agent.py、prompts/explore.md | 仅新模式提示草稿、旧模式提示保持原状 |
| tests/unit、tests/integration | 各任务新测与既有回归；全部临时库和假模型 |
| docs/experience.md、examples/task.experience.json | 新实验模式、字段分工、索引与使用边界 |

不重做 experience_storage.py、experience_catalog.py、experience_triggers.py；只在复用行为需要证明时加回归测试。现有 JSON 缓存、卡 ID 算法、目录迁移与触发码限制保持不变。

### 测试约定

使用实际导入当前源码的工具 Python。示例命令中的 `python` 指该解释器；当前机器通常为 `E:/ReproAgent/.venv/Scripts/python.exe`，必须现场确认。

所有命令附加仓库外、每次唯一的 `--basetemp`；先创建其父目录。Windows 当前可使用 `C:/Users/Dell/AppData/Local/Temp/reproagent-draft-review/<唯一运行名>`。不得全局设置 PYTHONPATH。Grep 集成测试显式配置已存在的 `REPROAGENT_RG_PATH`，不能把缺工具的 skip 算通过。

每任务都执行 RED → 最小实现 → GREEN；保存失败与通过输出，先排除测试准备错误。下面的预期通过是验收条件，不是本次已运行结果。

## Task 1: 实验配置与旁路草稿接收

**Files**
- Create: `src/reproagent/experience_drafts.py`。
- Modify: `src/reproagent/core/models.py`、`app.py`、`experience.py`、`core/controller.py`（仅 explorer 可选参数组装）、`core/agent.py`、`prompts/explore.md`。
- Modify: `src/reproagent/adapters/agentscope/tools.py`、`middleware.py`、`explorer.py`、`runtime.py`。
- Test: 新增 `tests/unit/test_experience_drafts.py`、`tests/integration/test_agentscope_experience_drafts.py`；扩展 `tests/unit/test_models.py`。

**Interfaces**
- `TaskRequest.experience_mode: str = 'legacy'`；`__post_init__` 只验证这个新枚举，不顺带增加旧字段校验。既有 decode_record/encode_record 默认字段兼容，不新建配置解析器。
- `LessonDraft`：冻结 dataclass，三个非空字符串 situation/observation/lesson，`source_ids: tuple[str, ...]`。
- `CapturedDraft`：冻结 dataclass，`draft: LessonDraft`、`call_id: str`、`event_seq: int`、`arguments_hash: str`。系统元数据不允许模型填写。
- `parse_lesson_draft(raw: object, *, secrets: tuple[str, ...] = ()) -> LessonDraft`：四字段精确匹配、类型与来源数量检查，脱敏并在规范化前后检查 1024 字节，不截断。
- `ExperienceDraftSession(store, *, secrets=())`，`latest: CapturedDraft | None`，`offer(raw: object, *, call_id: str, arguments_hash: str) -> str`；状态码 received/invalid/omitted/store_error。事件 seq 由 TaskStore 分配；最新合法草稿通过 paths.workspace_path/既有 atomic_write 保存到 learning/draft.json，失败不替换内存或磁盘旧草稿。
- `ExperienceService` 新增 `mode`、`draft_session`；仅 draft_review、开启学习、库未损坏时创建 session。
- 产品 explorer/factory/runtime 新增可选 `experience_mode='legacy', draft_session=None`；旧参数调用不变。Controller 只在新模式提供新 kwargs，legacy 下不改变自定义 explorer_factory 的调用形状。
- `build_toolkit` 与 WriteCandidate 新增可选 keyword `experience_mode='legacy'`，原位置参数不变；middleware 新增可选 keyword `draft_session=None`。仅新模式选择带宽松草稿字段的 `_WriteCandidateWithLessonParams`，保留旧 `_WriteCandidateParams` 和 legacy 工具 schema 原文。

- [ ] **1. 写失败测试。** 下表每项为独立命名用例，使用真实 TaskStore 与临时目录。

| 测试 | 必须断言 |
| --- | --- |
| test_request_experience_mode_is_opt_in | 旧 JSON 解码得到 legacy；合法值往返；未知字符串/布尔/null 均拒绝，CLI 不创建输出、不调用模型 |
| test_draft_size_and_schema_are_bounded | 完整规范化 JSON 1024 字节可接收，1025 拒绝；缺字段/额外字段/空白内容/重复或超过三条来源拒绝；无真实秘密出现在存储/事件 |
| test_bad_optional_draft_does_not_reject_candidate | 经真实 SDK Toolkit 调用 write_candidate，lesson_draft 为字符串/数组/坏对象/超限时，合法候选仍注册，PhaseResult 与未带草稿时一致 |
| test_draft_recording_is_independent_of_grounding_rejection | 实际工具执行后返回 GROUNDED_CONTRACT_REQUIRED，合法前序草稿可保存；候选仍拒绝；权限 DENIED、未进入工具不保存 |
| test_draft_failure_preserves_previous_and_candidate | 旁路 atomic_write/诊断失败不改变发布结果、不覆盖旧草稿；最近合法草稿替换，后续坏草稿不抹掉它 |
| test_legacy_and_read_only_do_not_collect_drafts | legacy/经验关闭/只读/坏库不添加草稿指引或收集；legacy提示词与工具schema逐字节不变；不新增工具名或候选内容字段 |

- [ ] **2. 运行 RED。** `python -m pytest tests/unit/test_experience_drafts.py tests/integration/test_agentscope_experience_drafts.py tests/unit/test_models.py -q`。新接口缺失或行为未实现才是预期失败。
- [ ] **3. 实现配置与 session。** 复用现有请求序列化和程序组装；草稿仍为不可信建议，事件只存受控状态、大小、ID和参数哈希。不要将草稿写进 CandidateDraft/Candidate。
- [ ] **4. 接入真实调用。** 仅新模式使用 `_WriteCandidateWithLessonParams.lesson_draft: Any | None`，独立严格解析，避免 SDK 在主工具执行前因可选元数据损坏拒绝整个动作。旧 `_WriteCandidateParams` 不增加字段，候选基础参数仍严格校验。
  - WriteCandidate 在基础参数合法且真正进入业务执行后，返回的 ToolChunk.metadata 标记 `reproagent_draft_eligible=true`；候选发布成功及已有 grounded 拒绝都能携带，legacy 不添加。
  - `ExplorationMiddleware.on_acting` 在实际 ToolResponse 上看到此程序标记时，用真实 call.id 和 canonical_hash(call.input 对象)调用 session.offer；不从模型自述推定执行过，不重复消费流式 chunk。
  - 仅在新模式增加草稿指引：situation 必须说明未来何时加载；新候选尚未运行不能声称恢复成功。沿用六/八工具集合，未注册经验工具时不虚构它们。
- [ ] **5. 运行 GREEN 与旧边界。** 上述命令通过后，追加 `tests/integration/test_agentscope_domain_tools.py`、`tests/unit/test_experience_first_browse.py`；记录实际通过、失败和跳过。按授权范围保存任务 diff，不提交其他会话文件。

## Task 2: 草稿来源关联与 8 KiB 审查材料

**Files**
- Modify: `src/reproagent/learning_material.py`、`adapters/agentscope/explorer.py`（关闭时采集接口）。
- Create: `src/reproagent/experience_review.py`，本任务仅建立 `review_schema()`，Task 3 再扩展审查与请求逻辑。
- Test: 新增 `tests/unit/test_learning_review_material.py`；扩展 `tests/unit/test_learning_capture.py`、`test_learning_material.py`。

**Interfaces**
- Consumes Task 1 的 `CapturedDraft`；不信任草稿 sidecar 自带的任意路径或 hash。
- `capture_exploration(messages, source_refs, *, secrets=(), draft: CapturedDraft | None = None) -> dict`：保留旧返回键和限额；仅有草稿时增加程序解析的 draft_origin 关联信息。
- `prepare_review_input(store, result, context, *, draft: CapturedDraft, exploration: dict, secrets=()) -> LearningBuildResult`：复用 `_prepare` 的来源检查、预算测量和冻结胶囊；不把已冻结胶囊再编辑一遍。旧 prepare_learning_input 的输入输出不变。
- `experience_review.review_schema() -> dict` 在本任务建立：复用 core.protocol.learning_schema 的 experience 子结构，加外层精确三字段 decision/reason_code/experience；枚举与配对沿用 spec §6。Task 3 消费此接口，Task 2 不依赖尚未完成的 Task 3。
- 新 payload 增加 `draft`（四字段）和 `draft_evidence_ids`（程序关联的 EID 列表）；response_schema 用本任务定义的 review_schema。LearningInput/evidence_map 沿用现有结构。

- [ ] **1. 写来源与容量失败测试。**

| 测试 | 必须断言 |
| --- | --- |
| test_draft_sources_bind_to_prior_facts | 工具 ID 关联到现有采集中的完整、配对工具错误，run ID 关联到 original 执行；进入胶囊并得到真实 EID |
| test_reused_or_future_source_id_is_not_guessed | 重复/歧义 ID、晚于草稿的运行、草稿自身调用 ID、跨任务和无法匹配的来源拒绝；不因附近出现相同文本而关联 |
| test_partial_capture_does_not_invent_draft_origin | 超出最后512消息/每条64块/8错误/16引用窗口时明确 skipped_draft_evidence_unavailable，不静默扩大采集 |
| test_required_draft_evidence_is_not_shed | 8 KiB 包含草稿和全部必需来源；先去掉无关可选原子；必需来源或完整 Issue 放不下就跳过，不丢某个引用后仍假称完整 |
| test_review_material_excludes_fixed_and_old_experience | fixed 路径/内容 canary、参考补丁、旧经验正文、完整聊天不出现；候选假设明确保持 unverified |
| test_review_capsule_is_frozen_once | 事件追加不改变 evidence.jsonl hash/行号；已存在胶囊按现有规则跳过；长路径仍走 paths 工具 |

- [ ] **2. 运行 RED。** `python -m pytest tests/unit/test_learning_review_material.py tests/unit/test_learning_capture.py -q`。
- [ ] **3. 实现确定性来源关联。** 复用现有 SDK 消息的真实调用/结果配对和 message_offset/block_index；草稿调用通过 ID＋实际参数哈希定位。重复 ID 仍有歧义就拒绝，不猜顺序。run 来源必须有本任务记录、original 角色和早于草稿 event_seq 的程序事件。ID 同时命中多个命名空间也拒绝。
  - 只允许现有受控采集可完整提供的工具事实；成功 Read/Grep 只有引用而无可靠调用关联时，不从 trace 全文补齐，明确跳过。
  - draft 自身不分配为“事实证据”供引用，agent_hypothesis 不升级为观察；程序形成的 draft_evidence_ids 必须至少包含一个独立事实原子。
- [ ] **4. 实现投影与一次冻结。** 引用的事实优先、代表性尝试其次、无关补充最后；保留原 ATOM_BYTES=2048/MAX_ENTRIES=24 等限额。所有 source_ids 都须有可核验对应事实，不能只保留其中一个再让草稿引用其他不存在的事实。
- [ ] **5. 运行 GREEN 与 legacy 投影回归。** `python -m pytest tests/unit/test_learning_review_material.py tests/unit/test_learning_capture.py tests/unit/test_learning_material.py tests/unit/test_experience_learning.py -q`。材料 ready 不代表已写入或效果有效。

## Task 3: 一次学习调用完成审查、整理与写库

**Files**
- Extend: Task 2 创建的 `src/reproagent/experience_review.py`；Create: `src/reproagent/prompts/review_experience.md`。
- Modify: `src/reproagent/experience.py`；不改旧 extract_experience.md 的 legacy 行为。
- Test: 新增 `tests/unit/test_experience_review.py`；扩展 `tests/integration/test_agentscope_learning.py`，新建生命周期测试文件 `tests/integration/test_experience_draft_review_lifecycle.py`。

**Interfaces**
- `ReviewDecision` 冻结 dataclass：`decision: str`、`reason_code: str`、`card: ExperienceCard | None`。
- `review_schema() -> dict`：复用 core.protocol.learning_schema 的 experience 子结构，外层精确三字段 decision/reason_code/experience。禁止额外键。
- `validate_review(response: dict, material: LearningInput, store, *, secrets=()) -> ReviewDecision`：严格校验合法 decision/reason 配对，再委托 `validate_experience({'experience': ...}, ...)` 生成卡；保持无环导入（校验器复用在函数内局部导入）。
- `review_experience(material, gateway, context, store, *, secrets=()) -> ReviewDecision`：仅一次 gateway.complete，response_kind 仍为 learning，无 tools，明确 JSON 提示；不增加新 model purpose。
- `LearningResult` 新增可选默认空串 `review_decision`/`review_reason_code`，legacy 及旧 trace 缺字段继续兼容；旧 HTTP/usage/cost 字段不改口径。

- [ ] **1. 写审查校验失败测试。**

| 测试 | 必须断言 |
| --- | --- |
| test_review_decision_pairs_are_closed | accept/SUPPORTED、revise/SCOPE_NARROWED 非空卡；reject/UNSUPPORTED或CONTRADICTED或NO_REUSABLE_LESSON 必须 null；所有交叉组合、缺字段、多字段拒绝 |
| test_review_requires_independent_support | 未知/重复 EID、只引用 agent_hypothesis、未包含任何 draft_evidence_ids 对应事实均拒绝；不让草稿引用自身通过 |
| test_system_fields_are_not_model_writable | 模型自填 id/triggers/source_task_id/hash 拒绝；合法卡由既有程序补齐，framework 无 trigger 仍合法 |
| test_review_is_one_request_and_has_no_tools | 实际 MockTransport 收到独立 system+材料，无探索历史；HTTP 503/503/200 仍最多3尝试，非法 JSON 仅1次、无纠错请求 |
| test_review_modes_preserve_main_outcome | DONE 与已有草稿的 EXHAUSTED 都能 written；reject不写；取消/只读/关闭/坏库/无草稿不发审查请求 |
| test_learning_errors_and_index_failure_are_isolated | 超时/HTTP/坏schema/证据变更/写库失败不改原结果或退出码；正文写成功而索引失败仍 written，后续可重建 |

- [ ] **2. 运行 RED。** `python -m pytest tests/unit/test_experience_review.py tests/integration/test_agentscope_learning.py -q`。
- [ ] **3. 实现审查 prompt 与信封。** prompt 覆盖五项审查，明确“任务失败可学”“新候选未执行”“只有记录存在不等于支持归因”；情况未知则收窄/拒绝。最终 description 必须将有据的 situation 条件转成“何时加载”，不让模型编触发码。
  - 接受/收窄卡的引用至少含一个草稿关联的独立事实，全部 EID 必须来自当前 material。程序检查来源类别与关联，语义支持由模型审查，不能宣传为因果已被程序证明。
  - 沿用现有 LearningValidationError 诊断，无效审查信封归 invalid_structure/invalid_fields；大小、证据、provider 错继续精确区分。
- [ ] **4. 接入 ExperienceService。** legacy 沿用旧提炼路径；draft_review 先检查开关、取消、只读、库状态和 session.latest，再调用 Task 2 材料及本任务 reviewer。无草稿返回 skipped_no_draft，收到过但没有合法草稿返回 skipped_invalid_draft；来源失败返回 skipped_draft_evidence_unavailable；reject 返回 review_rejected。
  - accepted/revised 均沿用 append_experience 和目录索引生成，不新增存储格式、卡 ID 算法、源码引用权限或白名单检索。
  - 沿用 `_learning_started` 防重复、30秒独立预算、共享取消、最终结果落盘后的 learn 时机与 gateway 关闭。复用原 model.attempt 费用统计，审查不再次叠加总费用。
  - 增加 experience.review 受控事件并在现有 learning 汇总携带 decision/reason_code；事件保存失败沿用 event_recorded，不把写卡成功伪装成失败。无自由解释字段或新增审查文档。
- [ ] **5. 运行 GREEN 与既有学习回归。** `python -m pytest tests/unit/test_experience_review.py tests/unit/test_learning_validation_errors.py tests/integration/test_agentscope_learning.py tests/integration/test_experience_lifecycle.py tests/integration/test_experience_card_budget_diagnostics.py -q`。保存默认 legacy 没有新增 learning 调用的证据。

## Task 4: 可继续阻塞后的单次查阅提示

**Files**
- Create: `src/reproagent/adapters/agentscope/experience_hint.py`。
- Modify: `src/reproagent/adapters/agentscope/runtime.py`、`middleware.py`；复用现有 feedback，不改 Controller 的重试/终态决策。
- Test: 新增 `tests/unit/test_experience_runtime_hint.py`、`tests/integration/test_agentscope_experience_hint.py`。

**Interfaces**
- `ExperienceHintState(enabled: bool)`：同任务一个实例，契约修订不重置。
- `observe(fact: dict) -> None`：只记录程序已知的 tool_error/permission 或本轮继续探索的 verification 反馈；原始正文不拿来猜错误码或定位卡。
- `prepare(*, remaining_before_call: int, reserved_steps: int, allowed_tools: tuple[str, ...], view: ExperienceCatalogView | None, browse_state: str, deadline_ok: bool, cancelled: bool, phase_finished: bool) -> dict | None`：条件满足返回完整不超过256字节的 `{'message': 固定中文提醒}`；仅准备一次，不填卡ID/标题或正文。
- `finish(*, response_received: bool) -> None`：模型返回即记录 delivered（包括之后协议校验拒绝的响应）；模型未发出/失败只记录 omitted/unknown，不虚报交付；已经准备过也不循环重试提示。
- Runtime 在 phase.feedback 的既有受控 JSON 中识别 decision=continue 和未通过的执行/验收分类；门禁 pending、成功 verdict、任意自由文本和 terminal feedback 不触发。

- [ ] **1. 写预算、权限与交付失败测试。**

| 测试 | 必须断言 |
| --- | --- |
| test_hint_respects_pre_call_step_accounting | RESERVED=3 时调用前5步不提示、6步可提示；真实 SDK 中提示后仍能依权限完成目录和正文两次调用，不修改 take_step/reserve |
| test_hint_requires_available_history_and_readers | 空库、坏库、缺工具、目录/正文配额用尽、读规则DENIED、RESERVED_FOR_PUBLISHING、pending门禁、取消/终态均不提示 |
| test_hint_is_once_across_phases | 同失败重复、换失败、契约修订均最多准备/交付一次；未发模型请求不伪称 delivered |
| test_hint_wire_is_paired_and_preserves_evidence | 下一次真实 HTTP messages 可见提醒；原 tool_call/result 仍配对，原错误、source sidecar、反馈证据不变；没有隐藏读取或伪造工具结果 |
| test_hint_capacity_failure_drops_only_hint | 加入提示会越过原反馈/最终输入容量时，仅舍弃提示，原请求逐字保持，主任务不停；中文按UTF-8量，不按字符数截断 |

- [ ] **2. 运行 RED。** `python -m pytest tests/unit/test_experience_runtime_hint.py tests/integration/test_agentscope_experience_hint.py -q`。
- [ ] **3. 实现单次状态和观察入口。** 从当前 middleware 权限/工具结果取得已知事实；从已结构化反馈识别“仍会再探索”，不新增错误分类器或恢复路径。读取权限按 SDK 原规则验证，不通过手工猜“工具已注册就是允许”。仅 draft_review 模式启用。
- [ ] **4. 将提示附在下一请求的原反馈/原错误 ToolResult 的非源码区域。** 只复制该次 request 的消息，不回写 AgentState 形成每轮注入；不伪造结果，不修改原源码 TextBlock/sidecar。经过最终输入容量检查后才调用模型；容量或采集出错退回未提示的原输入。实际模型响应收到后、check_response 前确认交付，避免协议错响应被漏记。
- [ ] **5. 运行 GREEN 与老门禁回归。** `python -m pytest tests/unit/test_experience_runtime_hint.py tests/integration/test_agentscope_experience_hint.py tests/unit/test_experience_first_browse.py tests/integration/test_agentscope_experience_catalog.py tests/integration/test_agentscope_experience_triggers.py -q`。catalog/read 的授权和次数完全沿用现有对象，不因提示清零或增额。

## Task 5: 端到端、索引与交付验收

**Files**
- Complete: `tests/integration/test_experience_draft_review_lifecycle.py`。
- Extend as needed: `tests/unit/test_experience_catalog_metadata.py`、`tests/integration/test_experience_catalog_lifecycle.py`、`tests/unit/test_experience_catalog_observability.py`。
- Modify: `docs/experience.md`、`examples/task.experience.json`；trace 默认通用事件视图可展示则不改 viewer。仅若现有读取器有闭合字段校验阻断新 learning 字段，最小兼容修改 trace_rendering.py 并补测试，不新增专用页面。

- [ ] **1. 写真实 SDK＋MockTransport＋本地 pytest 的完整链路测试。** 复用现有 projects/facts、run_sdk_task 和 export_setup 的结构，不另造生产 runner。脚本化一次工具失败 → 后续 write_candidate 带草稿 → Controller 执行与验收 → 独立 learning 审查 → 写分类正文和索引。

| 测试 | 必须断言 |
| --- | --- |
| test_failed_task_can_write_reviewed_lesson | 用真实有限步骤结束为 EXHAUSTED，已有草稿仍审查写卡；source存在且早于草稿；主结果保持失败，不以写卡改成DONE |
| test_done_task_reviews_only_after_result_is_saved | 原版/重跑/fixed按现有路径；学习发生在探索关闭与最终结果保存后；review输入没有fixed内容；learning只调用一次 |
| test_description_reaches_next_task_catalog | 审查后的 name/description 出现在真实 list_experiences 响应及下一模型请求；相关ID读回正确detail，而不是只断言 index.json存在 |
| test_new_card_is_not_visible_in_current_snapshot | 当前视图指纹/目录无新卡；新任务可加载；经验正文不授予源码引用、不进入Verifier |
| test_index_and_legacy_card_compatibility | 新卡索引派生正确；索引缺失/过期/损坏内存恢复；旧七字段schema1卡能浏览；只读不迁移，不自动补trigger |
| test_export_failure_and_learning_failure_are_separate | 注入导出失败后仍按有效材料决定审查；审查失败不会覆盖EXPORT_FAILED；无可用原事实跳过 |
| test_trace_disabled_still_collects_and_reviews | trace开关只影响观测；无trace草稿关联与结果相同；trace打开可看到draft/review/hint/catalog/read和真实输入输出关联 |

- [ ] **2. 运行 RED，再完成最小接线。** `python -m pytest tests/integration/test_experience_draft_review_lifecycle.py -q`。修接线而不放宽原版证据/验收口径；无候选/无草稿明确 skip，不改成自动总结。
- [ ] **3. 校对文档和示例。** 示例明确 `experience_mode: 'draft_review'` 是实验选择、额外费用只来自既有一次审查及新增token，恢复方式未验证要标明。模型写 category/name/description/tags/summary/detail/evidence_ids；程序补id/来源/triggers，index由程序生成。勿把默认legacy改掉或迁移用户真实库。
- [ ] **4. 跑相关总回归。** 先用 `python -m pytest tests/unit --collect-only -q` 和 integration 同命令确认新增测试都被收集，再按显式文件列表运行所有 experience*/learning* 新旧测试、agentscope_domain_tools、verification_feedback_delivery、models、CLI与trace相关测试。禁止把未被shell展开的通配符当作已跑完整集合。全部为离线/假凭据。
- [ ] **5. 跑完整离线回归。** 明确设置可用 rg，unit 与 integration 分组顺序运行，保留首次失败；不要并行压测导致期限波动。记录实际导入、HEAD、工作区补丁哈希、每组结果和skip原因，不能复用其他分支的通过数量。
- [ ] **6. 交付并独立复核。** 在任务独立验证目录保存 VERIFICATION.md、JUnit、脱敏wire样例、schema/索引样例和覆盖矩阵。审查尤其核对本文 Review Focus 的五项。未经用户明确授权不提交、push或合并；即使离线全过，也不宣称真实语义质量、复现率或成本有收益。

## 真实验证的后续门槛（本计划不执行付费调用）

机制验证通过后再单独授权费用和案例。先检查真实运行确实产生合法草稿、关联事实、review决策与目录可见description，再谈效果；没有触发的运行单列。比较写卡方式时固定输入事实与预算，比较提示时固定冻结库并单独开/关提示；核对实际wire、经验正文加载、重复失败和最终差分成功，不以written或read次数代替有效性。

## 执行方式

建议当前会话逐项执行：五项依赖同一草稿和材料接口，顺序实施可避免接口漂移，完成后再做一次独立整体审查。若用户选择其他代理执行，交接本 spec 与本 plan；不默认让执行者读取整段聊天。

当前仅完成设计和实施计划。下一步由用户审阅计划并选择/确认执行方式，之后才改产品代码；不因旧会话曾授权其他任务实施而跳过本次计划审阅。
