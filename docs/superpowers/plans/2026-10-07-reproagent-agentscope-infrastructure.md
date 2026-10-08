# ReproAgent AgentScope Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以 AgentScope 2.0.9 统一模型、文件工具、消息历史和阶段内 ReAct 执行，保留 ReproAgent 的复现策略、业务编排、证据核验与独立交付。

**Architecture:** Controller 决定准备、理解、探索/生成、执行、核验和交付；SDK Agent 只执行探索/生成阶段。SDK Toolkit 注册受限 Glob/Grep/Read 和三个领域工具，阶段结果交回 Controller；候选随即执行、重复确认并按需进行修复版验证。

**Tech Stack:** 工具 Python >=3.11、AgentScope ==2.0.9、httpx、pytest；目标 Python/pytest 仍由任务指定，独立 replay 不依赖 SDK。

**Spec:** [基础设施迁移设计](../specs/2026-10-06-reproagent-agentscope-runtime-migration-design.md)。相关修复依据：[评测修复设计](../specs/2026-10-06-reproagent-evaluation-repairs-design.md)。经验库仅预留接入位置，见 [经验 MVP 设计](../specs/2026-10-06-reproagent-experience-evolution-design.md)。

状态：2026-10-07 编写；尚未开始本计划的产品实现。执行方式沿用用户选择：当前会话逐项执行。

## Global Constraints

- AgentScope 固定 2.0.9，终态只有一套 SDK 基础设施；不保留 native gateway 或单次 JSON 动作运行路径，不修改 SDK 源码。
- Controller、Runner、Workspace、Verifier、Exporter 继续掌握业务规则；SDK 自然语言结尾不构成成功。
- 默认 20 个探索决策尝试、单命令 60 秒、任务 900 秒；一次探索模型逻辑请求计一步，协议纠正也计步。网络尝试另外计数，单逻辑请求最多 3 次 HTTP。
- 每次探索响应最多一个工具调用；执行前检查完整响应，禁止先执行第一项再拒绝第二项。分析和语义核验不占探索步数，但计时间、HTTP、token 和费用。
- 工具响应上限 32768 字节；HTTP 响应上限 1MiB；截断明确标记；原始响应、推理正文和认证信息不进入审计事件。
- SDK 模型及 Agent 的重试配置为 0；第一版关闭压缩工具并阻止自动压缩请求。SDK 的 grace、总结或压缩不能绕过预算。
- 只注册 Glob/Grep/Read、write_candidate/revise_contract/request_information；不注册 Bash/PowerShell/通用 Write/Edit。SDK 文件工具只看到原版注册快照。
- 隐藏修复材料不进入探索上下文、工具、错误反馈或经验输入；最终语义核验不读取历史经验。
- 冻结文件、候选不可覆盖、哈希、真实 pytest 节点/阶段、完整来源检查和独立重复确认继续有效；文件副本不等于 OS 沙箱。
- Windows/Linux、Unicode/长路径和目标 venv 身份须保留；不修改注册表，不 resolve 目标解释器的符号链接。
- report.md 继续按现有模板确定性生成，不增加供用户逐一审核的文档；历史任务包和 native 评测记录不重写。
- 本次不实现经验存储、自主学习、RAG、多 Agent 协作、自动部署或 push。经验功能另有计划时才进入产品。

## Review Focus

1. SDK 工具缓存、链接或目录搜索泄露未注册材料：Task 2/3 测试缓存命中仍校验哈希，搜索不遍历修复目录，不给纯文件名结果签发行证据。
2. SDK 自动总结、grace、多工具与协议纠正突破第 20 步：Task 1/4 测试 wire HTTP 次数、步数与工具副作用，不能只检查 max_iters。
3. 阶段结束被当作用户取消或遗留不完整工具消息：Task 4/6 测试发布后零额外探索调用、反馈后继续同一任务历史、取消后完成进程清理。
4. 核验压缩掩盖错误来源或修复版失败：Task 5/7 测试完整硬检查先于模型、未发送引用不可使用，失败差分不能算有效复现。
5. Windows 长路径、Linux venv、并行代码变化和轮次污染：Task 8/10/11 测试端到端路径、冻结来源、旧记录读取和未执行官方判分的明确显示。

## 文件与职责

下表中的新增接口是实施约定，并非当前已经存在。

| 文件 | 职责 |
| --- | --- |
| `adapters/agentscope/model_factory.py`、`gateway.py` | 创建 SDK 模型；结构化领域请求；探索工具请求共用 HTTP 保护与记账 |
| `adapters/models/options.py`、`core/async_ops.py` | 提供方配置与有界异步等待；从旧 native 实现抽出必要辅助能力 |
| `adapters/agentscope/snapshot_backend.py`、`evidence.py` | SDK 后端的注册文件视图、受控 helper 执行、真实行证据 |
| `adapters/agentscope/tools.py` | SDK 原生文件工具绑定、领域 ToolBase/ParamsBase |
| `core/phase.py`、`core/candidate_service.py` | 阶段结果和候选发布，不依赖 SDK 类型 |
| `adapters/agentscope/runtime.py`、`middleware.py`、`explorer.py` | SDK 阶段运行、预算/审计 hooks、与领域探索接口连接 |
| `core/agent.py`、`core/ports.py`、`core/controller.py` | 自定义复现策略、探索端口、业务推进 |
| `core/review_context.py`、`core/verifier.py` | 完整硬检查后的紧凑语义输入 |
| `core/models.py`、`reporting.py`、`exporter.py` | 模型配置和修复版状态；沿用交付模板 |
| `paths.py`、`store.py`、`workspace.py`、`runner.py`、`resources/replay.py` | Windows 内部路径兼容，保持独立 replay |
| `app.py`、`cli.py`、`.claude/skills/reproagent/`、`evals/` | 唯一基础设施入口、兼容参数、来源与评测记录 |

上述产品路径以 `src/reproagent/` 为前缀；具体 Claude 入口文件在 Task 9 列出。保留现有文件职责，不顺便重构 pytest probe 或导出格式。

## Task 1：SDK 模型统一入口与提供方协议

**Files:** Create `src/reproagent/adapters/agentscope/model_factory.py`、`src/reproagent/adapters/models/options.py`、`src/reproagent/core/async_ops.py`、`tests/unit/test_model_options.py`；Modify `src/reproagent/adapters/agentscope/gateway.py`、`src/reproagent/core/models.py`、`src/reproagent/core/protocol.py`；Test `tests/integration/test_agentscope_gateway.py`、新增 `tests/integration/test_agentscope_model_factory.py`。

**Interfaces:** `ModelRequest.max_output_tokens: int|None=None`；`ModelConfig.thinking_mode: str|None=None`，取 None/enabled/disabled。`resolve_model_options(config: ModelConfig, request_limit: int|None) -> ProviderRequestOptions`，返回 frozen `ProviderRequestOptions(output_limit: int, extra_body: dict)`。`AgentScopeModelFactory(config: ModelConfig, store: TaskStore, *, transport: httpx.AsyncBaseTransport|None=None)` 的 `create(context: CallContext, purpose: str, *, guard: Callable[[],None]|None=None) -> ChatModelBase`，purpose 只取 exploration/contract/verdict；每个任务内实例复用。guard 在每个逻辑模型调用的 HTTP 重试循环之前执行一次，供 Task 4 注入探索阶段检查。`AgentScopeModelGateway(factory)` 继续实现 `ModelGateway.complete`，用于 contract/verdict。`bounded(awaitable, context: CallContext)` 移至 core/async_ops.py。

- [ ] 写 `test_config_limit_reaches_sdk_wire`：未指定 request_limit 且配置 8192 时 wire 为 8192；显式 1024 时为 1024；默认仍 4096；bool/零值/非法 thinking_mode 被拒绝。
- [ ] 写真实 SDK + MockTransport 的 `test_tool_roundtrip_preserves_provider_protocol`：exploration 接受 tool_calls；contract/verdict 拒绝 tool_calls、length、空正文；enabled 模式的必要 reasoning_content 在下一轮按 SDK/提供方格式传递，不写日志。不支持的格式明确报错，不能静默剥离。
- [ ] 写 `test_retry_usage_and_response_limits`：429 后成功只有 2 次 HTTP/记账；网络最多 3 次；length 不按相同参数重试；1MiB 超限/重定向/非 identity 编码被拒绝；缺用量仍记 unknown，响应丢失也留下尝试记录。
- [ ] RED：`.venv\Scripts\python.exe -m pytest tests/unit/test_model_options.py tests/integration/test_agentscope_gateway.py tests/integration/test_agentscope_model_factory.py -q`；确认失败来自缺少接口/协议支持。
- [ ] 实现选项和 SDK 工厂，沿用已有原始 HTTP finish_reason、解码前字节限制、脱敏与费用检查。唯一重试层负责暂时网络错误；`ModelOutputError(message, code='INVALID_PROTOCOL', retryable=True)` 使用受控闭集代码，协议有限纠正与 HTTP 重试分开。
- [ ] GREEN：重复上述命令并确认取消、usage 与 structured JSON 回归通过；提交本任务文件，`feat: unify guarded AgentScope model infrastructure`。

## Task 2：SDK 文件工具的受限快照后端

**Files:** Create `src/reproagent/adapters/agentscope/snapshot_backend.py`、`src/reproagent/adapters/agentscope/evidence.py`、`tests/integration/test_agentscope_snapshot_tools.py`；Test `tests/unit/test_workspace.py`。

**Interfaces:** `SnapshotBackend(project: ProjectView, store: TaskStore, context: CallContext)` 实现 SDK `BackendBase`；公开 `validate_registered(path: str) -> FileEntry`，其 I/O 方法保持 SDK 2.0.9 签名。`EvidenceLedger(project: ProjectView, store: TaskStore)` 的 `record_visible(path: str, start: int, end: int) -> EvidenceRef`、`contains(ref: EvidenceRef) -> bool`；ledger 只接受重新校验哈希的原版真实行范围。

- [ ] 写 `test_real_sdk_glob_grep_read_use_registered_snapshot`：通过真实 SDK 三个工具获得中文文件名、regex 命中和行内容；未注册文件、候选目录及隐藏 fixed 根均不出现；CRLF 与 UTF-8 字节哈希保持原始值。
- [ ] 写 `test_traversal_symlink_and_cache_cannot_bypass_hash`：`../`、跨盘绝对路径、越界链接被拒绝；一次 Read 后更改文件，再次缓存读取仍被拒绝；缺失/binary/非 UTF-8 返回受控错误且不产生引用。
- [ ] 写 `test_helper_is_bounded_and_cannot_run_arbitrary_commands`：只允许已核对的 SDK rg/Glob helper 调用；argv 中伪装路径/选项不能扩展可读范围；取消/超时无存活子进程，缺 rg 明确阻塞而非退回旧 Python 搜索。
- [ ] RED：`.venv\Scripts\python.exe -m pytest tests/integration/test_agentscope_snapshot_tools.py -q`。
- [ ] 实现 BackendBase 必要文件系统查询的受限覆盖；写入/删除一律拒绝。rg 只搜索经过注册/哈希检查的显式文件，Glob 枚举结果限制在注册清单；不直接执行任意 backend argv，不扫描后再过滤秘密。保留 SDK 的搜索/读取逻辑，不复制旧 search_code。
- [ ] GREEN：执行上述测试及 workspace 回归；提交 `feat: bind SDK file tools to verified source snapshots`。

## Task 3：领域工具与明确阶段结果

**Files:** Create `src/reproagent/core/phase.py`、`src/reproagent/core/candidate_service.py`、`src/reproagent/adapters/agentscope/tools.py`、`tests/unit/test_phase.py`、`tests/integration/test_agentscope_domain_tools.py`。

**Interfaces:** frozen `PhaseResult(kind: str, candidate_id: str='', source_refs: tuple[EvidenceRef,...]=(), reason: str='', question: str='')`，kind 取 candidate/revise_contract/request_information/no_candidate，严格检查各分支字段。`PhaseGate` 提供 `begin() -> None`、`finish(result: PhaseResult) -> None`、`result: PhaseResult|None`、`event: asyncio.Event`，同一轮只接受首次结果。`CandidateService(project: ProjectView, workspace: Workspace, context: CallContext)` 的 `bind_contract(contract: IssueContract) -> None`、`publish(draft: CandidateDraft) -> Candidate`、`project: ProjectView`；每阶段绑定当前契约，publish 再校验绑定一致性。`build_toolkit(backend: SnapshotBackend, ledger: EvidenceLedger, candidates: CandidateService, gate: PhaseGate, context: CallContext) -> Toolkit`。

- [ ] 写 `test_candidate_tool_returns_actual_immutable_id`：工具生成的 ID 在 TaskStore 中存在；错误目录/角色/重复路径/覆盖与无充分契约均拒绝；发布失败不结束阶段。
- [ ] 写 `test_revision_requires_displayed_original_lines`：纯 Glob/文件名 Grep 不授权行引用；Read 被截断的长行不能作为完整行证据；revision 只能使用 ledger 已显示的完整原版范围；伪造 hash/范围被拒绝。
- [ ] 写 `test_first_phase_result_wins_and_toolkit_is_exact`：领域工具成功后仅一个 PhaseResult；Toolkit 只有规定的六个工具，无 run/submit/Shell/Write/Edit。工具响应及证据 sidecar 合计 ≤32768 字节，裁剪时只给完整已显示行签发引用。
- [ ] RED：`.venv\Scripts\python.exe -m pytest tests/unit/test_phase.py tests/integration/test_agentscope_domain_tools.py -q`。
- [ ] 实现 ToolBase/ParamsBase 与文件工具 middleware；候选通过现有 Workspace.publish/TaskStore 落盘，工具仅回传受控 ID/摘要。工具描述或系统约束明确“仅注册原版快照”，覆盖 SDK 默认“可读全机”描述造成的误导；不改 SDK 源码。
- [ ] GREEN：重复命令并提交 `feat: register evidence-aware SDK domain tools`。

## Task 4：有预算、可结束、保留消息历史的 SDK 阶段执行器

**Files:** Create `src/reproagent/adapters/agentscope/runtime.py`、`src/reproagent/adapters/agentscope/middleware.py`、`tests/integration/test_agentscope_runtime.py`；Modify `src/reproagent/core/ports.py`；Test `tests/unit/test_budget.py`。

**Interfaces:** 领域 `ExplorationRuntime` 端口定义 `async explore(context: AgentContext) -> PhaseResult`、`async aclose() -> None`。`AgentScopeRuntime(model: ChatModelBase, toolkit: Toolkit, gate: PhaseGate, context: CallContext, *, system_prompt: str)` 实现它，每任务一个 SDK Agent/AgentState。`ExplorationMiddleware(context: CallContext, gate: PhaseGate, store: TaskStore)` 在公开 middleware hooks 上接入，另提供同步 `before_model_call() -> None` 作为 Task 1 factory 的 guard：先检查阶段已结束/取消/截止，再执行一次 take_step，避免 hooks 重复计数。

- [ ] 写 `test_twentieth_decision_may_finish_but_twenty_first_never_hits_http`：20 次探索 wire 请求之后 zero extra；第 20 次合法候选仍能返回；协议纠正消耗步数，HTTP 网络重试不重复扣逻辑步。
- [ ] 写 `test_multiple_or_unknown_tools_have_zero_side_effects`：两个调用、非法工具/JSON 在 SDK 开始 acting 前整体拒绝，最多三次协议尝试且受剩余步数约束；未知工具没有外部执行委派。完整否定/正常无工具结束不进入纠正循环。
- [ ] 写 `test_sdk_grace_summary_and_compression_cannot_add_calls`：触发 SDK 额外总结/结构化 grace/压缩时守卫阻止探索 HTTP；自动压缩明确停止，不丢失源引用或静默继续。
- [ ] 写 `test_phase_finish_resumes_history_without_user_cancel`：write_candidate 完成后 zero extra exploration；用户 cancel_event 未设置；下一轮看到前轮工具结果和原版核验反馈；两个任务历史不串；运行终止时无未等待的 task。
- [ ] RED：`.venv\Scripts\python.exe -m pytest tests/integration/test_agentscope_runtime.py tests/unit/test_budget.py -q`。
- [ ] 实现一次响应完整校验后才 acting；SDK 模型/Agent retries=0。监听 gate 的阶段结束，允许完成工具结果写入后结束并 await SDK task，必要时受控取消内部 asyncio task；不调用不存在的 SDK stop/interrupt，也不把正常结束映射为用户 CANCELLED。普通无工具结束返回 no_candidate；Controller 进入 NEEDS_INFORMATION、stop_reason=NO_CANDIDATE 并交付诊断，不能无限重新 reply。预算耗尽且没有领域结果时保持 EXHAUSTED。
- [ ] 接入 on_check_permission：通过产品路径/来源/schema 检查的六个工具自动允许，其余拒绝；不引入 SDK 的人工批准或外部工具委派。压缩工具关闭且 on_compress_context 阻止模型压缩与 fallback truncation，容量不足返回明确诊断，源证据不能被静默删去。
- [ ] GREEN：运行上述测试；事件记录 phase、受控 action/result_code、参数 canonical hash、steps_remaining、实际组件版本；不记录 raw response；提交 `feat: run bounded SDK exploration phases with task history`。

## Task 5：先修复语义核验的证据容量问题

**Files:** Create `src/reproagent/core/review_context.py`、`tests/unit/test_review_context.py`、`tests/integration/test_large_review_context.py`；Modify `src/reproagent/core/verifier.py`、`src/reproagent/prompts/review_evidence.md`；Test `tests/unit/test_verifier.py`。

**Interfaces:** frozen `ReviewContext(payload: dict, available_refs: tuple[EvidenceRef,...], input_bytes: int, output_bytes: int, omitted_fields: tuple[str,...])`。`build_review_context(contract: IssueContract, candidate: Candidate, run: ExecutionResult, *, read_ref, read_file, max_bytes: int, protocol_error: str='') -> ReviewContext`；read_ref 校验全文并返回 str，read_file 校验候选后返回 bytes；核心证据无法容纳抛 `ReviewContextTooLarge`。

- [ ] 写 `test_132_verified_origins_fit_32768_without_mutating_execution`：完整硬检查后投影 ≤32768 字节，原 132 条来源记录不变，完整候选关键断言和预期/失败行段存在。
- [ ] 写 `test_wrong_origin_or_changed_failure_prevents_model_call`、`test_unsent_or_truncated_citation_is_rejected`、`test_core_evidence_overflow_stays_uncertain`：错误来源/篡改/核心超限不得升级为 REPRODUCED；未展示范围不可被模型引用。
- [ ] RED：`.venv\Scripts\python.exe -m pytest tests/unit/test_review_context.py tests/unit/test_verifier.py -q`。
- [ ] 保留 Verifier 完整来源、绑定、probe、节点/阶段、清理与保护检查；之后才建立摘要、真实行段和候选正文投影。每次协议纠正重算整体 JSON 字节数；最多三次，语义否定不纠正为肯定。模型仍通过 Task 1 SDK gateway。
- [ ] GREEN：再跑上述测试及 `tests/integration/test_large_review_context.py` 的真实 pytest + SDK MockTransport 差分用例；提交 `fix: compact SDK review inputs without weakening evidence`。

## Task 6：保留产品策略，由 Controller 自动推进候选

**Files:** Modify `src/reproagent/core/agent.py`、`src/reproagent/core/ports.py`、`src/reproagent/core/controller.py`、`src/reproagent/adapters/agentscope/explorer.py`、`src/reproagent/prompts/explore.md`、`src/reproagent/prompts/analyze_issue.md`；Test `tests/unit/test_agent.py`、`tests/unit/test_controller.py`、`tests/unit/test_action_correction.py`、`tests/unit/test_model_protocol_regressions.py`、`tests/integration/test_review_regressions.py`、`tests/integration/test_agentscope_explorer.py`。

**Interfaces:** `ReproAgent.analyze(description: IssueDescription, evidence: EvidenceContext) -> IssueContract` 保留；`exploration_prompt(context: AgentContext) -> str` 定义探索目标、契约约束、候选父目录及 source 引用规则。Explorer 端口将 next_action 替换为 `async explore(context: AgentContext) -> PhaseResult` 和 `async aclose() -> None`。`AgentScopeExplorer(gateway: ModelGateway, context: CallContext, candidate_parent: str, *, project: ProjectView, workspace: Workspace, model_factory: AgentScopeModelFactory)` 组合产品策略与 SDK runtime；Controller 在 freeze/prepare 后构造，每任务关闭一次。

- [ ] 写 `test_candidate_is_run_and_confirmed_without_run_submit_decisions`：阶段返回候选后原版执行、SDK 语义核验、独立重复均发生；探索步数只统计模型逻辑请求，Controller 的 execute/confirm 不占探索步，但仍受 deadline/取消约束。
- [ ] 写 `test_revision_invalidates_old_candidate_and_requires_read_sources`：修订调用 analyze、版本递增、旧证据清空；旧候选不能被接受。接口提案名称只作待核对信息，未充分 grounded 不发布候选。
- [ ] 写 `test_plain_success_text_and_missing_information_do_not_publish_success`：纯“成功”结尾、no_candidate、原版通过、语义否定、环境错误分别有明确诊断；固定版状态/路径不回传探索。原版非环境失败可以继续下一轮生成新候选。
- [ ] 写 `test_cancel_waits_for_runner_cleanup_and_explorer_close`：保留现有 cleanup failed 的失败优先级；SDK phase 关闭与 Runner 子进程清理都被 await，用户取消有诊断包。
- [ ] RED：`.venv\Scripts\python.exe -m pytest tests/unit/test_agent.py tests/unit/test_controller.py tests/integration/test_agentscope_explorer.py -q`。
- [ ] 将现有 run/submit 分支提取为 Controller 内部业务方法并在 candidate 结果后直接调用；接入 Task 3/4 的领域服务、ledger 与 phase。每阶段先 bind_contract；领域工具以当前契约和 snapshot 构造 CandidateDraft，模型不能指定版本/源码身份；Controller 从 TaskStore 取实际 ID 的候选，下一阶段使用更新后的 ProjectView。保留 source 验证、独立重复、固定版隔离及导出事务；删除 next_action/DecisionGateway 的通用动作循环，经验提示默认空。
- [ ] GREEN：更新脚本测试到新阶段端口，单元 fixture 显式注入符合新签名的 fake Explorer；SDK 集成直接组装 runtime/Controller，默认 app 切换由 Task 9 完成。旧 action_correction/model_protocol/review_regressions 的参数错误、候选 ID 不存在、literal/regex 区分和低预算语义转为新 SDK 工具/阶段测试，不能直接删除覆盖；原“2 动作只能单次观察”测试改成 deadline 中断。运行本节六组测试；提交 `refactor: keep reproduction workflow above SDK exploration`。

## Task 7：修复版状态和用户报告保持真实

**Files:** Modify `src/reproagent/core/models.py`、`src/reproagent/core/controller.py`、`src/reproagent/exporter.py`、`src/reproagent/reporting.py`、`src/reproagent/resources/report.md.template`、`evals/schema.py`、`evals/run.py`、`evals/swt_bench/results.py`；Test `tests/unit/test_reporting.py`、`tests/unit/test_swt_results.py`、`tests/unit/test_controller.py`。

**Interfaces:** `TaskResult.fix_validation_status: str='not_provided'`，取 not_provided/passed/failed/blocked；`EvalResult` 追加同名默认字段。report.json/report.md/summary 传递真实结果；backend 事件/报告新增 infrastructure='agentscope'、实际 agentscope_version、strategy='reproagent'、strategy_version='1'；旧字段可保留兼容，但新 model_backend/agent_backend 均记录实际 agentscope。

- [ ] 写 `test_failed_or_blocked_fix_is_not_differential_success`：重复原版失败仍可交付其证据；固定版 failed/blocked 则 DIFFERENTIAL_VALIDATED 为假，summary 有效差分数为 0，report.md 主结论明确差分未成立。
- [ ] 写 `test_no_fix_and_legacy_record_keep_truthful_defaults`：无固定版为 not_provided；旧报告/评测无新增字段仍可读取；历史 native 元数据不被迁移覆盖。
- [ ] RED：`.venv\Scripts\python.exe -m pytest tests/unit/test_reporting.py tests/unit/test_swt_results.py tests/unit/test_controller.py -q`。
- [ ] 实现状态传递和模板字段；不另加用户核对文件、不增加生成报告的模型调用；只提供方错误正文不能成为报告字段名。
- [ ] GREEN：重复命令并提交 `fix: distinguish repeated reproduction from validated differential results`。

## Task 8：Windows 长路径与 SDK helper 清理回归

**Files:** Create `src/reproagent/paths.py`、`tests/unit/test_paths.py`、`tests/integration/test_windows_long_paths.py`；Modify `src/reproagent/store.py`、`src/reproagent/workspace.py`、`src/reproagent/runner.py`、`src/reproagent/adapters/runtimes/local.py`、`src/reproagent/adapters/agentscope/snapshot_backend.py`、`src/reproagent/exporter.py`、`src/reproagent/resources/replay.py` 的必要路径点。

**Interfaces:** `workspace_path(path: Path) -> Path` 仅统一 Windows 内部工作区绝对长路径；`display_path(path: Path) -> str` 用于展示。目标解释器不经过这两个转换；保留已有 absolute_python 的 venv 身份。

- [ ] 写 `test_unicode_long_path_sdk_execution_export_and_replay`：用户传普通路径，原子临时文件路径超过 260 字符；SDK 文件工具、freeze、pytest、保护检查、导出及新副本独立 replay 全通过。
- [ ] 写 `test_linux_venv_interpreter_keeps_own_site_packages` 与越界链接回归；Windows 专属用例 Linux 可跳过，venv/边界用例不可跳过。
- [ ] RED：`.venv\Scripts\python.exe -m pytest tests/unit/test_paths.py tests/integration/test_windows_long_paths.py tests/integration/test_pytest_probe.py tests/integration/test_runner.py -q`；解释缺失接口或长路径失败，不把无权限临时目录当产品失败。
- [ ] 实现内部路径一致化与相同 SDK helper 超时/取消清理策略，保留相对证据身份；独立 replay 内嵌必要路径逻辑，不 import reproagent/agentscope。
- [ ] GREEN：Windows 上跑完整长路径链路，Linux CI 跑普通路径/venv；提交 `fix: preserve workspace and interpreter identity across platforms`。

## Task 9：切换唯一基础设施入口并删除旧执行路径

**Files:** Modify `src/reproagent/app.py`、`src/reproagent/cli.py`、`src/reproagent/core/models.py`、`pyproject.toml`、`.github/workflows/test.yml`、`evals/run.py`、`evals/swt_bench/run.py`、`evals/swt_bench/__main__.py`、`.claude/skills/reproagent/SKILL.md`、`.claude/skills/reproagent/scripts/reproagent.ps1`；Modify `tests/integration/test_agentscope_backends.py`、`tests/integration/test_cli.py`、`tests/integration/test_claude_code_launcher.py`、`tests/integration/test_installed_package.py`、`tests/unit/test_model_gateway.py`、`tests/integration/test_review_regressions.py`；Remove `src/reproagent/adapters/models/provider.py`、`src/reproagent/core/tools.py` 的旧运行实现。

**Interfaces:** `create_controller(request, model, gateway=None, *, model_backend: str|None=None, agent_backend: str|None=None, explorer_factory=None, transport=None)` 默认只创建 SDK；gateway/explorer_factory 是测试和自定义领域策略注入点，不提供产品 native 实现。旧 native/agentscope flag 都映射到 SDK 并发弃用提示，未知值拒绝；自定义 Explorer 须符合 Task 6 新端口，实际 model infrastructure 仍按注入情况记录。

- [ ] 写 `test_default_and_legacy_flags_select_same_sdk_infrastructure`：默认与旧参数全部走 SDK 真实 toolkit/model；无 SDK 返回安装错误，绝不 native fallback；CLI、Claude launcher、SWT 入口元数据一致。
- [ ] 写 `test_required_sdk_is_packaged_but_target_replay_is_independent`：wheel 安装依赖包含 agentscope==2.0.9，不装旧 extra 也能运行 SDK；目标隔离 venv 无 SDK，原版/修复版 replay 返回 1/0。
- [ ] RED：执行 CLI/Claude/SDK backend 集成；原四组合测试改为默认/旧 flag 的兼容行为，不保留四套运行实现或取消硬核验断言。
- [ ] app.py 组装单工厂、gateway、Controller；SDK 移入主依赖，过渡 optional extra `agentscope=[]` 保留一个弃用周期，旧安装命令不报错；CI 用 `.[dev]`。迁移 bounded 的所有 import 后删除 native provider；候选职责迁至 CandidateService 后删除旧 Tools/SCHEMAS/validate_action 及无消费者 AgentAction。
- [ ] GREEN：`.venv\Scripts\python.exe -m pytest tests/integration/test_agentscope_backends.py tests/integration/test_cli.py tests/integration/test_claude_code_launcher.py tests/integration/test_installed_package.py tests/unit/test_model_gateway.py tests/integration/test_review_regressions.py -q`；wheel/依赖/无 SDK 目标测试通过；`rg -n 'ChatCompletionGateway|DecisionGateway|def next_action|tool_schemas|validate_action|models.provider' src tests` 预期无旧执行路径引用（Verdict.next_action 的领域提示字段不属于旧探索协议）；提交 `refactor: make AgentScope the sole product infrastructure`。

## Task 10：迁移评测入口、来源冻结与真实参考对照

**Files:** Create `evals/swt_bench/provenance.py`、`evals/swt_bench/control.py`、`tests/unit/test_swt_provenance.py`、`tests/integration/test_swt_controls.py`；Modify `evals/swt_bench/run.py`、`evals/swt_bench/results.py`、`evals/swt_bench/__main__.py`、`evals/datasets/swt_bench.py`；Test `tests/integration/test_swt_batch.py`、`tests/unit/test_swt_dataset.py`、`tests/unit/test_swt_results.py`。

**Interfaces:** `capture_tool_source(root: Path) -> dict`、`assert_tool_source(root: Path, receipt: dict) -> None`，变化抛 `ToolSourceChanged`；round 的 source_comparison_status 取 verified/changed/not_recorded。`run_reference_control(row: dict, binding: dict, output: Path) -> dict` 与 CLI `control --snapshot --manifest --bindings --output`；只评测端读取隐藏参考材料。`select_holdout(catalog: dict, excluded_ids: set[str], *, repos: tuple[str,...], count: int=10, seed: str='reproagent-swt-holdout-v1') -> dict` 返回现有 sealed manifest。

- [ ] 写 `test_source_change_stops_later_model_calls_without_dropping_cases`：修改受指纹保护文件后停止新任务，本轮 changed、所有样本留记录。指纹含 src 代码/提示词/资源、evals Python、pyproject 与工具依赖版本；不读 key 文件。
- [ ] 写 `test_control_requires_actual_buggy_failure_and_fixed_pass`：collection/setup/skipped/no-tests 不合格；patch 返回 0 但目标字节没改变为 patch_error；原版确有 call failure 且固定版同节点全部 call pass 才是有效对照；生成 repo 从不修改。
- [ ] 写 `test_holdout_is_frozen_and_not_selected_by_hidden_results`：固定 seed 可重现、排除 dev20/已调试样本；选择只读公开 catalog，不看参考对照、gold patch 或生成是否成功；旧轮与待官方判分状态仍可读。
- [ ] RED：执行新增 provenance/controls 与 dataset/results 测试。
- [ ] 实现冻结指纹、唯一 SDK 评测入口和阶段/HTTP/token 明细；参考测试仅在独立副本运行并保存源码、补丁、节点/阶段、解释器/依赖哈希。继续固定 dev20 分母 20；环境未准备不丢例，不算模型实际执行。
- [ ] GREEN：`.venv\Scripts\python.exe -m pytest tests/unit/test_swt_provenance.py tests/integration/test_swt_controls.py tests/integration/test_swt_batch.py tests/unit/test_swt_dataset.py tests/unit/test_swt_results.py -q`；提交 `feat: freeze SDK evaluation provenance and executable controls`。

## Task 11：整链路验收、真实模型定点与文档更新

**Files:** Modify `tests/integration/test_agentscope_backends.py`、`docs/agentscope.md`、`docs/claude-code.md`、`docs/compatibility.md`、`docs/swt-bench.md`、`docs/implementation-status.md`、`README.md`；Create `examples/model.deepseek.non-thinking.json` 和真实执行日期的 `docs/evaluations/<date>-agentscope-infrastructure.md`。已有模型配置保持原样。

**Interfaces:** 不引入新业务接口；真实评测复用 Task 9/10 入口，组件版本、配置、预算与环境回执随轮次冻结。

- [x] 写整链路真实 SDK + MockTransport 测试：Glob → Grep → Read → write_candidate，接着 Controller 自动原版执行/SDK 核验/原版重复/固定版执行/导出；新副本 replay 1/0。断言探索 wire 使用 tools，verifier 使用 structured 请求，没有额外 run/submit 决策、隐藏输入或成功文字捷径。
- [x] 离线验收：`.venv\Scripts\python.exe -m pytest tests/unit tests/integration -q --basetemp=.tmp/agentscope-infra-final-01`；`.venv\Scripts\python.exe -m pip check`；确认必需 SDK 测试不再 importorskip。临时目录已存在时换唯一后缀，不递归删除其他任务目录。Windows/Linux CI 保留目标 Python/pytest 矩阵。
- [x] 已知诊断候选只作确定性回归；然后用 Sphinx-8801 原始 Issue 发起全新 SDK/DeepSeek 任务，预算 20/60/900，新配置显式 thinking disabled、输出 4096，独立 1/0。失败保留完整原因，不用旧诊断候选抵扣成功，不马上花整轮预算。**任务已真实执行，独立 1/0 未达成**：修复版对照失败，原因定位在候选断言与官方修复无关（见 `repro-results/sdk-fixedpoint-8801-002/`）。
- [x] 定点链路成立后冻结实现提交、导入路径、依赖和配置；重跑原 dev20，每例有记录；已交付包独立验证。再以 Task 10 固定策略冻结 10 个未调试样本，同预算执行，环境阻塞仍在分母。**dev20 重跑与保留集执行已完成；"已交付包独立验证"未执行**，与迁移前同名轮次同一状态。
- [x] 官方 Linux/Docker harness 在资源可用时执行固定版本；资源不可用则明确“未执行”，保留本地实测并列出阻塞，不把缺失官方结果写成官方 0% 或成功。源改变的轮次不可用于版本比较；未知费用不写 0。**保留集已判分；dev20 刻意未判分**（20 条预测里 18 条是空补丁，没有可比基线）。
- [x] 更新文档描述最终实际结构，公布原版重复、有效差分、交付重放、环境阻塞、HTTP/token 与官方状态；人工评价未提供则 pending，不作为运行必需步骤。不把 MockTransport 成功当模型能力或保证更高复现率。
- [x] 提交 `docs: document and evaluate AgentScope infrastructure migration`；明确“迁移代码验证通过，能力验收未通过”，不要勾选未执行的评测步骤。**首轮按此结论提交；真实模型验收在 2026-10-08 单独执行，结论以 `docs/evaluations/2026-10-08-agentscope-acceptance.md` 为准。**

## 依赖、交付与旧计划衔接

实施顺序：Task 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11。Task 1–8 可先测试新组件，Task 9 完成产品入口切换；这是内部迁移步骤，最终不交付双基础设施产品。执行前读取 worktree 技能，隔离实现工作区和 venv，并带上本计划与最新设计文档；共享工作区中的其他未提交文档和 CI 修改不得覆盖。

| 批次 | 交付门槛 |
| --- | --- |
| 基础设施组件（1–4） | SDK 模型/工具真实执行；受限来源、证据、预算、阶段结束和历史保留通过 |
| 业务集成（5–9） | 候选自动执行/核验/重复；容量问题修复；报告真实；跨平台 replay；唯一 SDK 产品路径 |
| 评测验收（10–11） | 来源和对照可信；离线整链路通过；真实定点/冻结轮次/新样本据实记录 |

[旧评测修复计划](2026-10-06-reproagent-evaluation-repairs.md) 的工作不丢弃，但不再照搬双后端接口：旧 Task 1 对应本 Task 4/10；旧 Task 2→5；旧 Task 3→1；旧 Task 4→6（自动执行不再占探索步）；旧 Task 5→7；旧 Task 6→8；旧 Task 7→10；旧 Task 8→11。若实施中发现某项已有独立修改完成，先核对代码和测试证据，只做缺失部分，不能仅按旧文档状态重复实现。

历史 Requests/SymPy 的 Python/依赖环境、pytest 源码构建产物和官方 Docker 资源作为后续环境专项，不承诺本迁移让所有仓库自动跑起来。经验自动写入与渐进加载另行实现，本计划不产生空壳经验服务。

## 计划自审

- 设计章节 1/2/4 对应 Task 3/4/6；章节 3→2/3；章节 5→1/9；章节 6→1/4/5/8；章节 7→7/9；章节 8→衔接表；章节 9 的七项验收→Task 2–11。
- 端口和阶段结果统一：Explorer.explore / ExplorationRuntime.explore 均返回 PhaseResult；候选 ID 只来自 CandidateService.publish；SDK类型只在 adapters 和装配层使用。
- 经验 MVP 未混入迁移；无两套 gateway 对照任务；没有用自动总结或原始正文日志绕过预算/脱敏。
- 五项 Review Focus 均已加入对应任务的失败测试；本文写的是实施步骤，不代表这些测试已运行或任务已完成。
