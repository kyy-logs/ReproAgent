# ReproAgent Contract Scope and Citation Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in the current session. Steps use checkbox (`- [x]`) syntax for tracking. 如用户另外选择委派，再使用对应执行技能。

**Goal:** 避免非必要提案阻塞有依据的复现目标，并一次反馈引用请求中的全部受控错误与已展示可用引用。

**Architecture:** 契约分离发生在现有分析/修订模型请求中，沿用现有字段与发布闸门；三个提示词统一范围解释。ReviseContract 汇总现有受控拒绝，程序从已签发且重新验证的引用生成建议；独立纯渲染器按工具字节预算输出诊断，不改变引用授权。

**Tech Stack:** 现有 Python、AgentScope 2.0.9、pytest、httpx.MockTransport；不新增依赖。

**Spec:** [契约范围与批量引用反馈设计](../specs/2026-10-14-contract-scope-citation-feedback-design.md)。状态：待执行。本地 main 基线 `1d14e76`；执行前重新核对。

## Global Constraints

- 沿用 IssueContract/response_schema；未确认的核心语义继续保留 missing_information，禁止程序按关键词自动移除阻塞。
- 8906 目标为错误指引缺陷；不得以验证已正常的模块级 skip 取代这个目标。不猜最终整句文案，不把可选新 API 视为已存在接口。
- 原 Issue、来源哈希、契约版本、新证据要求、所有 Verifier 程序检查保持。assumptions 不构成预期证据。
- 精确签发范围、当前哈希、快照边界、截断行限制不变；有效引用建议不能自动替换提交。
- 顶层 code 和 ERROR_METADATA 使用第一个既有受控错误码；权限 ALLOWED / DENIED / RESERVED_FOR_PUBLISHING 不变。
- 每项最多 3 个已展示建议；总正文不超过 tool_response_bytes，默认 32768 B；UTF-8 计费，JSON 不截断，省略必须标明。
- 不新增模型请求、工具、预算、CLI 参数或状态；保持已实现软收敛和 experience browse gate。
- 离线验收不访问 provider、不使用密钥、不写默认经验库、不用 Astra。真实语义效果另获付费运行授权。
- basetemp 放在仓库外系统临时目录；保护用户未跟踪评测 HTML 和其他会话改动。文档链接使用 task-local 路径/现有长路径能力。

## Review Focus

1. “提案不阻塞”被误用为抹掉未知核心接口：Task 1 保留仅提案/缺核心行为的发布拒绝反例。
2. 为了产出红测试改变原问题：Task 1 校验分析/探索/审阅指令一致，Task 3 独立检查 8906 候选目标；离线假模型不证明语义识别。
3. 批量诊断无意授权子范围、截断行或过期引用：Task 2 补完整安全边界测试。
4. 大量引用/中文导致 JSON 切断或预算突破：Task 2 纯渲染器测限额、建议/错误省略和极小容量。
5. 反馈记录了却没进入下一轮模型输入，或每个错误多计一次动作：Task 3 捕获真实 wire/tool_call_id 并断言动作与事件数量。

---

## 文件边界

| 文件 | 工作 |
| --- | --- |
| src/reproagent/prompts/analyze_issue.md | 替换冲突规则、增加独立目标与核心阻塞反例 |
| src/reproagent/prompts/explore.md | 明确非阻塞备注和复现范围，批量引用失败如何一次修正 |
| src/reproagent/prompts/review_evidence.md | 提案与目标分离、禁止接受仅验证已正常功能的候选 |
| src/reproagent/adapters/agentscope/tools.py | _CitationRefusal 的建议载荷、ReviseContract 的逐项检查与反馈装配 |
| src/reproagent/adapters/agentscope/citation_feedback.py | 新增：纯函数，渲染受限反馈并明确省略 |
| tests/unit/test_model_protocol_regressions.py | 当前请求中的分离规则、反例、已有协议及来源不变 |
| tests/unit/test_citation_feedback.py | 新增：JSON、字节上限、稳定省略和极小预算 |
| tests/integration/test_agentscope_domain_tools.py | 真实 Toolkit 多错误、建议有效性和拒绝边界 |
| tests/integration/test_agentscope_runtime.py | 真实 SDK + MockTransport 的下一请求送达、一次修订结束与兼容 |
| docs/reviews/2026-10-14-contract-scope-citation-feedback-acceptance.md | 实现后新增：命令、结果、基线、局限和原始证据位置 |

不改 core/models.py、protocol.py、agent.py、candidate_service.py、Controller、Verifier Python 程序检查、EvidenceLedger/EvidenceViews 的 contains、snapshot_backend、经验检索或 trace 模板。若实现发现必须改变这些接口，停止并重新说明范围，不能顺手扩展。

### Task 1：统一现有契约范围规则

**Files:** 修改上述三个 prompts 和 tests/unit/test_model_protocol_regressions.py。

**Interfaces:** 保留 ReproAgent.analyze(description, evidence)、IssueContract 和 contract_schema；只是原请求的 system 文本变化。复用 Responses/ProposalModel 和 analyze 测试 helper；不新增模型调用。

- [x] **Step 1：补有意义的离线回归。** 新增 test_contract_scope_rules_reach_analysis_request：构造“错误提示遗漏已有参数 + 可选新 API”描述，捕获实际 ModelRequest，确认原 Issue/来源完整保留、system 含“只有必要未知项阻塞”的精确新规则；验证返回的 expected/checks/非阻塞备注不被程序搬移。新增 test_core_missing_information_is_never_cleared，假模型返回缺核心预期的契约，断言缺失项原样保留；保留 test_interface_proposal_is_missing_information_not_an_expectation 的发布拒绝反例。明确这些假模型测试只证明输入/程序行为，不证明真实模型识别。
- [x] **Step 2：运行 RED。** `.venv/Scripts/python.exe -u -m pytest -q tests/unit/test_model_protocol_regressions.py --basetemp "$env:TEMP/reproagent-contract-scope-red"`。新规则未送达的断言应失败；不修改原有拒绝断言来掩盖失败。
- [x] **Step 3：修改分析提示。** 替换“任何未确认提案都进入 missing_information”为条件规则：先定有来源目标，只有依赖提案且未确认时阻塞，否则以明确未确认备注保留。加入 8906 类正例与“只有新 API/核心输出未知”的反例；不删除结构校验、纠错上限和新来源规则。
- [x] **Step 4：同步探索与语义审阅。** 明确历史契约错误要引用原始来源修订；不能用备注充当证据，不能把目标换成已有正常功能；提示内容判断可依据明确语义，不要求来源未定义的整句文字。增加 test_contract_scope_prompts_share_behavior_not_proposal_rule 检查三处关键规则及旧接口提案防护仍存在，不要求整篇提示逐字相同。
- [x] **Step 5：运行 GREEN 并检查协议。** 重跑 Task 1 命令，并运行 tests/unit/test_phase.py 与 tests/integration/test_agentscope_domain_tools.py 中候选发布相关回归；预期 exit 0，核心缺失仍禁止发布。记录“模型能力未验证”。
- [x] **Step 6：定向提交。** 只提交三个提示词及对应测试，提交信息 `fix: separate reproduction blockers from optional proposals`；不提交用户评测 HTML。

### Task 2：批量、受限且不扩大权限的引用反馈

**Files:** tools.py、新 citation_feedback.py、新 test_citation_feedback.py、test_agentscope_domain_tools.py。

**Interfaces:**

- `_CitationRefusal(code, message, *, available_refs: tuple[dict, ...] = (), available_refs_truncated: bool = False)`：保留既有 code/message 行为，增加由程序生成的建议和是否因 3 条上限省略其他建议的标记。
- `ReviseContract._displayed_alternatives(self, item: _SourceRefParams, canonical: EvidenceRef) -> tuple[tuple[dict, ...], bool]`：从 views.issued_refs 过滤同 canonical.path / 当前哈希、去重并重新 views.contains；规范化成 snapshot-relative 的可提交对象。返回按设计顺序最多 3 个建议，以及是否存在未返回的其他有效建议；建议不足不造新引用。
- `render_citation_feedback(*, code: str, errors: list[dict], byte_limit: int, available_refs_truncated: bool = False) -> str` 位于新 helper：输入仅程序已验证的结构化错误；输出 UTF-8 上限内正文。错误条目字段精确为 index/code/submitted/available_refs；总对象精确为 code/message/error_count/errors/omitted_errors/available_refs_truncated。
- `ReviseContract._cite` 的成功返回和接受条件不变；call 逐项收集两种 _CitationRefusal，将任意一项建议截断标记传给 renderer，失败统一 ToolResultState.ERROR，metadata 使用首错误码，phase 保持打开。其他拒绝/参数异常保持旧路径。

- [x] **Step 1：写批量失败与授权反例。** 新增 test_revision_feedback_contains_all_controlled_errors：真实 Toolkit Read 两个原版文件，提交至少三个同时无效的引用（混合错误哈希与已显示范围的子范围），断言一次响应包含全部 index/错误码、首错误码 metadata、有效已签发建议、gate.result is None。旧代码只有第一句错误，RED 应失败。
- [x] **Step 2：写纯渲染边界。** test_feedback_fits_utf8_budget 参数化中文路径和 byte_limit=32768/1024/256/64/2/1/0；所有输出字节不超限。容量允许最小对象时 json.loads 成功；初始建议先被减少，随后错误被省略，error_count 不变、omitted_errors 精确、标记明确；不足最小对象则固定 ASCII 短文切到上限，不包含半个 JSON。新增 test_feedback_order_is_stable，相同输入产生相同正文。
- [x] **Step 3：运行 RED。** `.venv/Scripts/python.exe -u -m pytest -q tests/unit/test_citation_feedback.py tests/integration/test_agentscope_domain_tools.py --basetemp "$env:TEMP/reproagent-citation-feedback-red"`。允许新增 helper 不存在或新批量断言失败；区分既有失败，先记录不掩盖。
- [x] **Step 4：实现 helper 与受控汇总。** renderer 使用 compact JSON/UTF-8 长度，先删建议，后从尾部删错误；临界长度用重新序列化实际计数。固定首错误码，失败不 finish。不要修改全局 _capped，不包住 BudgetStopped/未知异常，不在校验中执行新工具或读模型。建议校验只使用既有冻结文件/ledger，未显示来源不能输出建议。
- [x] **Step 5：补安全边界。** test_revision_feedback_never_suggests_unissued_or_stale_refs、test_revision_batch_does_not_partially_finish、test_available_reference_suggestions_are_snapshot_relative_and_copyable：覆盖 Grep 文件名模式/Glob、截断行、源文件变更、外部路径、子范围拒绝；只有全有效请求才原样 revision_requested。若期望消息从字符串变成 JSON，只更新正文表达断言，原来 ERROR、未结束和拒绝权限的断言必须保留。
- [x] **Step 6：运行 GREEN。** 重跑 Task 2 命令，再跑 tests/integration/test_experience_rejection_triggers.py 和 tests/unit/test_experience_triggers.py；预期 exit 0，metadata 错误码保持，拒绝计数没有因 errors 条目增倍。
- [x] **Step 7：定向提交。** 仅提交本任务 helper、tools 和测试，提交信息 `fix: return bounded batch feedback for source citations`。

### Task 3：证明反馈送达、核对 8906、完成验收

**Files:** test_agentscope_runtime.py、三个提示词中探索引用指导、验收记录。

**Interfaces:** 使用 Task 2 原有工具调用接口和反馈正文；复用 runtime 测试 environment/handler/tool_reply 与真实 formatter/model factory。除测试外不改变 middleware/runtime 的业务接口。

- [ ] **Step 1：写实际 wire 回归。** test_batch_citation_feedback_reaches_next_wire_request：安排真实 SDK Read、一次含多个无效引用的 revise_contract、一次复制正确已签发引用的修订。捕获 HTTP JSON；断言拒绝 tool_call_id 与下一请求的 tool 消息对应，error_count/errors/可用引用完整送达，程序未自动扩大范围，最后 phase.result 为 revise_contract。这些都是 MockTransport 返回，不能声称模型自己学会修正。
- [ ] **Step 2：补预算与状态兼容。** test_batch_feedback_does_not_add_actions：每个 revise 请求仍只花一个逻辑步骤、一次调用/一个受控错误事件；已实现软收敛消息在后续请求保留；最后三步 Read 仍 RESERVED_FOR_PUBLISHING；有经验工具时 browse gate 状态不重置。无需为 errors 每项添加观测事件。
- [ ] **Step 3：运行 RED/GREEN。** `.venv/Scripts/python.exe -u -m pytest -q tests/integration/test_agentscope_runtime.py tests/integration/test_exploration_progress_observability.py tests/integration/test_agentscope_first_browse.py --basetemp "$env:TEMP/reproagent-contract-citation-wire"`。根据真实失败修测试或本任务范围内代码，不绕过容量、权限或保护规则。探索提示明确按全部错误一次复制建议；建议不是新的 Issue 证据。
- [ ] **Step 4：8906 只读核对。** 读取 repro-results/batch-pre-pytest-8906/observability/trace.json 中 sdk_tool_input/sdk_tool_result，提取真实显示范围与四次修订。依据冻结清单/文件哈希在独立测试输出重建 EvidenceViews，确认 607–615/124–144/56–65/1331–1360/66–75 的子范围仍拒绝，而完整已签发范围可用。用第一次五引用请求验证一次反馈全部受控错误，输出小报告到 `.local/contract-citation-audit-20261014/`；不修改原任务、快照、候选、经验库或结论，不加载旧 API key，不请求 provider。不要直接往 ledger._issued 塞假引用替代验证。
- [ ] **Step 5：合理范围回归。** 运行 Task 1/2/3 相关文件，加 tests/unit/test_review_context.py、tests/unit/test_verifier.py、tests/unit/test_controller_execution_recovery.py、tests/unit/test_exploration_progress.py、tests/unit/test_budget.py、tests/integration/test_experience_rejection_triggers.py。使用仓库外独立 basetemp，保存准确命令、exit code、通过/跳过/失败数；未结束或中止不能算通过。只有新增问题或仓库要求才扩大测试范围。
- [ ] **Step 6：自审并写验收。** 对照五条 Review Focus、代码 diff 和旧引用拒绝反例。验收分开列提示送达、反馈机制、预算/权限兼容、8906 离线核对；注明没有真实模型语义验证、没有完整真实 DONE 重跑。记录没有改哪些产品接口。
- [ ] **Step 7：定向提交验收。** 提交本任务测试/提示与验收记录；不自动 push/合并，不覆盖其他会话工作。按用户后续指令集成。

## 真实效果验证（不属于离线通过条件）

实现与离线验收后，若获得真实模型运行授权，用同一原版 8906 快照和可运行环境重跑，保持模型/thinking/20 步/60 秒命令上限。检查初始与修订契约的目标和缺失项、候选断言是否对准错误提示、引用拒绝次数、真实执行与 Verifier 结果。案例没有 DONE 时分别说明契约问题、引用问题、执行障碍或语义拒绝，不把它们折成“效果不好”；一次成功也不证明所有提案都能正确分类。

## 完成条件

离线机制通过上述检查、所有已发现失败解决或明确记录，且没有扩权/改题/额外模型请求。完成记录须分别说明“提示规则已实现”“引用反馈已实现”“真实模型效果未验证”；不得把假模型指定的正确回答写成真实能力提升。
