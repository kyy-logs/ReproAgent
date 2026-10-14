# ReproAgent Exploration Budget Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在步骤硬上限之前，向每轮探索请求提供新鲜预算事实和软收敛指导，保留剩余探索机会及原有验收边界。

**Architecture:** 从现有任务 Budget 派生只读步骤快照，由 AgentScope middleware 在每次逻辑模型请求中追加临时提示。guard 继续负责硬准入和扣步；已有事件、OTel 本地投影与 wire_request 证明提醒时机，不新增模型请求或状态机。

**Tech Stack:** Python 3.11+、AgentScope 2.0.9、pytest、httpx.MockTransport、现有 OpenTelemetry 观测设施；不新增依赖。

**Spec:** [探索步骤预算软收敛设计](../specs/2026-10-14-exploration-budget-convergence-design.md)。状态：功能已实现，离线回归通过；浏览器点击验收受安全策略阻止，独立审查见验收记录。

## Global Constraints

- 第一版仅实现探索步骤软阈值；时间、费用、风险软阈值均不在实现范围。
- `soft_threshold=(3*agent_steps)//4`；使用扣步前 used_steps，remaining_steps 包含本次调用。
- 默认 20 步，第 16 次请求开始收敛；第 20 次可以完成，第 21 次不允许发 HTTP。
- 跨 phase/契约/候选共用原 Budget；HTTP 重试不额外扣探索步数。
- 提示最多 1024 UTF-8 字节，不写入持久历史，不复制用户/源码/工具结果到系统指令。
- 不新增 CLI 参数、BudgetLimits 字段、TaskState、模型调用或依赖；不更改 thinking 配置。
- 保留最后 3 步读工具保留策略及 ALLOWED / DENIED / RESERVED_FOR_PUBLISHING；不降低证据标准。
- 观测故障不阻塞任务；沿用现有 task-local 路径与 Windows 长路径入口，不新增旁路写文件。
- 离线验收不调用真实 provider，不消耗模型 token；真实行为实验须另获授权，验收不用 Astra。

## Review Focus

1. 同一 ReAct 阶段跨阈值：旧阶段输入不能掩盖新计数；Task 2 捕获第 15/16/20 个实际请求。
2. 协议纠错、HTTP 重试与跨 phase：纠错算新步骤，网络重试不算；Task 2 分别验证快照和不重置。
3. 极小步骤预算、取消、已完成 phase：不借提醒追加请求；Task 1/2 覆盖 L=1/2/3 与 guard 优先级。
4. 不可信历史字段或超大进度：不能提升为系统指令或生成无效 JSON；Task 1 测字段白名单、UTF-8 上限和 unknown。
5. 经验浏览与观测失败：两种临时提示共存且不重复入历史；Task 2/3 测 wire 内容、观测异常及权限码保持。

---

## 文件边界

| 文件 | 处理与职责 |
| --- | --- |
| `src/reproagent/core/exploration_progress.py` | 新增：步骤快照纯函数，不修改 Budget 的硬计费/准入行为 |
| `src/reproagent/adapters/agentscope/progress.py` | 新增：受控 phase 事实与 1024 B 提示渲染 |
| `src/reproagent/adapters/agentscope/middleware.py` | 每轮注入、扣步前快照观测、一次转换标记 |
| `src/reproagent/adapters/agentscope/runtime.py` | phase 起点更新 phase_context；保留当前历史与 gate 流程 |
| `tests/unit/test_exploration_progress.py` | 新增：阈值、事实、渲染和安全边界 |
| `tests/integration/test_agentscope_runtime.py` | 复用真实 SDK+MockTransport 环境，验证实际请求链 |
| `tests/integration/test_exploration_progress_observability.py` | 新增：trace 与事件、观测故障回归 |
| `docs/observability.md` | 记录提醒含义、字段与不能证明的事项 |
| `docs/reviews/2026-10-14-exploration-budget-convergence-acceptance.md` | 实现时新增：命令、结果与未验证范围 |

不改 Controller 业务状态、Verifier、Runner、候选/源码保护、学习调用、经验检索策略或 viewer 模板。`core/budget.py`、`core/models.py`、`model_factory.py` 保持现有准入接口，本次只用其现有能力。

### Task 1：只读步骤快照与受控提示

**Files:** Create `core/exploration_progress.py`、`adapters/agentscope/progress.py`、`tests/unit/test_exploration_progress.py`（均位于上表完整路径）。

**Interfaces:**
- frozen/slots `ExplorationStepView(mode: str, total_steps: int, used_steps: int, remaining_steps: int, soft_threshold: int, next_call_index: int | None)`，位于 core 新模块。
- `step_budget_view(total_steps: int, used_steps: int) -> ExplorationStepView`；输入正整数 total、非负整数 used，bool 不接受，非法输入 ValueError；U≥L 时 next_call_index=None。
- `render_progress(view: ExplorationStepView, phase: AgentContext | None) -> str`，位于 adapter 新模块；返回单条可解析 JSON，包含 budget、facts、guidance。normal guidance 为空字符串，converging 使用下面固定英文；exhausted guidance 为空，不发 provider。

- [x] **Step 1：写失败的纯函数测试。** `test_step_budget_boundaries` 参数化断言 `(L,U,mode,remaining,next_index)`：`(20,14,normal,6,15)`、`(20,15,converging,5,16)`、`(20,19,converging,1,20)`、`(20,20,exhausted,0,None)`、`(20,21,exhausted,0,None)`；小预算 `(1,0,converging,1,1)`、`(2,0,normal,2,1)`、`(2,1,converging,1,2)`、`(3,2,converging,1,3)`。非法负数、0 total、bool、浮点数应 ValueError。
- [x] **Step 2：运行 RED。** `.venv/Scripts/python.exe -m pytest -q tests/unit/test_exploration_progress.py --basetemp "$env:TEMP/reproagent-convergence-unit-red"`；预期因新增模块不存在而失败，不应依赖真实网络。
- [x] **Step 3：实现快照纯函数与渲染器。** 不读取时钟或修改 Budget。budget 为 asdict(view)；facts 包含 contract_version、missing_information_count、contract_grounded、latest_candidate_id、latest_candidate_result，phase=None 则 unknown。候选只来自 project.candidates 最后一条；结果只能来自 history 中同 ID 的最新 candidate 项，否则 unknown；标识须匹配 `[A-Za-z0-9_-]{1,128}`，拒绝自由文本。contract_grounded 与现有保留提示一致，为 bool(expected) 且 missing_information 为空。只有 converging 的 guidance 使用：

  ```text
  Budget convergence: focus on the current reproduction path. Use only targeted reads needed to resolve a concrete blocker while readers are allowed. Publish only when the existing contract and evidence justify it; otherwise revise from displayed evidence or request_information. Avoid restarting broad searches or retrying an unchanged refusal. The Controller owns execution and independent confirmation. Do not relax verification requirements.
  ```

- [x] **Step 4：补渲染测试并运行 GREEN。** `test_progress_uses_only_supported_phase_facts` 验证无候选/有候选无结果/有匹配结果/仅有旧候选结果；`test_progress_does_not_promote_untrusted_text` 在 feedback、缺失项、history.result_code 放入注入文本，断言自由文本不出现，未知码为 unknown；`test_progress_is_bounded_valid_utf8_json` 断言最坏合法标识下 ≤1024 B，超限按顺序去可选候选字段、可选契约字段，保留完整 budget 和指导，不直接切字符串。用同一 unit 命令换独立 basetemp，预期全部 PASS。
- [x] **Step 5：定向提交。** 仅 add 本任务三个新文件，提交 `feat: derive bounded exploration budget progress`。

### Task 2：每轮真实输入送达与硬边界兼容

**Files:** Modify `middleware.py`、`runtime.py`、`tests/integration/test_agentscope_runtime.py`。

**Interfaces:**
- `ExplorationMiddleware.phase_context: AgentContext | None` 初始 None；每次 runtime.explore 起点赋当前 context，现有 middleware.contract 保留。
- 在 on_model_call 原 experience_input/browse 处理之后追加临时 `SystemMsg(name='exploration-progress', content=render_progress(...))`。原列表复制，不改变 state.context。不得提前 take_step 或移动 `_GuardedModel` guard。
- 同一任务 middleware 已复用，phase_context 只替换，不另建 Budget。phase 已完成或 steps_used≥total 时不注入新消息，继续交原 guard 处理。
- 新增 `async _check_progress_capacity(self, agent, messages, tools) -> None`：对最终组装输入用 SDK count_tokens 做本地计数，到达现有 trigger_ratio * model.context_size 阈值时抛 BudgetStopped('NEEDS_INFORMATION')，不压缩/截断、不扣步、不调用 provider。已完成 phase、取消、任务到限和步骤耗尽优先沿原 guard 终止，不能先被该检查覆盖。

- [x] **Step 1：在现有真实 SDK 测试环境写失败用例。** `test_budget_progress_refreshes_on_each_wire_request` 在一个 phase 脚本回复 19 次 Read、最后 write_candidate；对捕获 requests 中解析出的 progress JSON 断言第 15/16/20 次依次是 `(normal,14,6)`、`(converging,15,5)`、`(converging,19,1)`，每请求恰有一份，guidance 仅收敛轮非空。保留原第 21 次不发 HTTP 断言。
- [x] **Step 2：运行 RED。** `.venv/Scripts/python.exe -m pytest -q tests/integration/test_agentscope_runtime.py -k budget_progress --basetemp "$env:TEMP/reproagent-convergence-wire-red"`；预期新提示缺失而 FAIL。
- [x] **Step 3：实现注入与 phase 更新。** 严格使用 Task 1 签名。提示从扣步前 budget.steps_used 生成；不基于阶段起点缓存计数；不把 phase.feedback 复述为系统指令。模型 guard 仍检查取消、时间与步骤后才允许请求。
- [x] **Step 4：补协议与跨阶段测试。** `test_budget_progress_http_retry_keeps_one_snapshot` 用 503/503/200 验证 3 次 HTTP、1 步、输入快照一致；`test_budget_progress_protocol_correction_spends_new_step` 在阈值前一轮返回两次工具调用，纠错请求跨入收敛；`test_budget_progress_contract_revision_does_not_reset` 使用真实 revise_contract phase 后恢复，contract_version 更新但 used 不归零，旧候选结果不能假配新 ID。
- [x] **Step 5：补历史、经验与停止回归。** `test_budget_progress_is_ephemeral_and_coexists_with_browse` 复用经验门禁测试的 toolkit 装配，验证下一真实 wire 同时有目录结果、browse 提示和进度提示，runtime.messages 不永久保存进度；`test_budget_progress_stopped_calls_do_not_reach_http` 覆盖取消、deadline、gate.finished、耗尽与 L=1/2/3。保留现有权限测试，比较保留区边界的结果码，软阈值本身不得增加拒绝。
- [x] **Step 5a：补最终输入容量回归。** `test_budget_progress_counts_final_messages_before_http` 构造旧输入略低于压缩阈值、加入临时提示后到阈值的计数结果，验证 NEEDS_INFORMATION、0 次 HTTP、0 次新增步骤，且未压缩历史；旧输入加提示仍低于阈值时正常发出。与 gate.finished/取消/耗尽组合时，原终止原因优先。计数必须消费最终 messages/tools，不能复用注入前估算。
- [x] **Step 6：修正受影响的测试定位方式并运行 GREEN。** 新临时消息会使 `requests[-1]['messages'][-1]` 不再必然是阶段 UserMsg；已知 `test_original_issue_is_kept_when_contract_changes_between_phases` 应按 user role 和包含 contract/issue 的 JSON 查找，不靠最后位置。完整运行 `tests/unit/test_exploration_progress.py tests/integration/test_agentscope_runtime.py tests/integration/test_agentscope_gateway.py`，仓库外独立 basetemp；预期全部 PASS，默认20步、max_tokens、取消、压缩和 finalization 断言不削弱。
- [x] **Step 7：定向提交。** 只 add 本任务 runtime/middleware/测试文件，提交 `feat: refresh convergence guidance in exploration model inputs`。

### Task 3：诊断链、文档和离线验收

**Files:** Modify `middleware.py`、`docs/observability.md`；Create `tests/integration/test_exploration_progress_observability.py`、`docs/reviews/2026-10-14-exploration-budget-convergence-acceptance.md`。

**Interfaces:**
- before_model_call 在原 guard 准入之前取 StepBudgetView，本次 take_step 成功后记录；沿用原 `exploration.step`，增加 budget_mode、soft_threshold、steps_used_before_call、remaining_steps_before_call、next_call_index，原 steps_remaining 仍扣步后。
- middleware 新增任务级 `_convergence_recorded=False`，仅第一个已准入 converging 调用尝试写 `budget.convergence` 事件与同名 trace 点；不随 phase.begin 重置。
- 每个已准入逻辑调用尝试 `mark('budget.progress', attributes={'budget': asdict(view)})`；转换 trace 也仅用现有白名单 budget 属性。新增观测路径隔离 I/O/值/运行时错误，不捕获吞掉 BudgetStopped/取消。无需改 trace schema/viewer。

- [x] **Step 1：写 RED 观测测试。** `test_convergence_is_recorded_once_per_task` 使用真实 SDK+MockTransport 跨两个 phase，断言只有一次转换，exploration.step 中 remaining_steps_before_call 和 steps_remaining 差 1；`test_progress_trace_correlates_with_wire_request` 验证 budget.progress trace 点与已有请求节点在同一轮因果链，wire 中的数据与点上扣步前快照一致；HTTP 重试不多转换、不多扣步。
- [x] **Step 2：运行 RED 后实现新增观测。** `.venv/Scripts/python.exe -m pytest -q tests/integration/test_exploration_progress_observability.py --basetemp "$env:TEMP/reproagent-convergence-trace-red"`；随后按本任务接口实现，不能把“记录提示”写成“模型已收到”。
- [ ] **Step 3：验证故障和展示（自动验证完成；file: 被浏览器安全策略禁止，点击验收未完成）。** `test_progress_observation_fault_does_not_change_phase_result` 注入新增观测写入异常，候选/步骤/权限结果保持一致；开启与关闭 trace 都跑。离线生成一份真实 SDK 请求的本地 trace.html，使用现有渲染入口和 workspace_path 路径机制；用浏览器确认预算详情与 wire 正文可见，明确是模拟回复而非真实模型行为。不新增 UI 功能。
- [x] **Step 4：更新说明与验收文档。** 写清默认第16轮开始、1024 B、最后3步读限制仍在、无新增模型调用但提示有少量输入 token、exhausted 与 convergence 的区别；记录全部实际运行命令/结果/异常和未进行真实效果实验。问题挑战文档 P11 标注待实现；只有验收通过后才能更新为已实现，并附本次记录。
- [x] **Step 5：运行集中回归。** `.venv/Scripts/python.exe -m pytest -q tests/unit/test_exploration_progress.py tests/unit/test_budget.py tests/integration/test_agentscope_runtime.py tests/integration/test_agentscope_gateway.py tests/integration/test_exploration_progress_observability.py tests/unit/test_trace_view_model.py tests/unit/test_trace_rendering.py --basetemp "$env:TEMP/reproagent-convergence-acceptance"`，预期 exit 0；另跑既有首次经验浏览相关测试（先用 `rg -l 'browse_gate|EXPERIENCE_BROWSE_REQUIRED' tests` 找实际文件，再定向执行）。集中回归通过且无新风险时，不重复无关全量测试。
- [x] **Step 6：审查与定向提交。** 核对软阈值未改变硬准入、来源/语义验收及无额外 provider 请求；gpt-6.1-sol 独立审查无问题，另42项定向验证通过，未用 Astra。只提交本功能文件和验收文档，保留用户既有 evaluation HTML 及其他无关改动。已提交 `feat: expose exploration budget convergence diagnostics`；未获 push/merge 请求不自行执行。

## 通过标准与后续效果实验

完成本计划表示：每轮进度已实际送进请求体，软阈值只改变指导，硬上限/权限/证据边界未退化，且观测能解释提醒发生在哪一轮。不宣称模型必然服从，也不承诺 DONE 提升。

真实效果实验后续单独授权：冻结代码、环境、经验库、模型参数及预算，对比有无软提示的多次运行。记录首次发布候选步骤、重复拒绝次数、读工具有效进展、最终证据等级和无依据候选；报告样本数与波动，不把 n=1 当有效性结论。

本计划由当前会话逐项实现，用户已授权执行。真实 A/B、合并和 push 不包含在本轮实现中；浏览器点击验收的限制详见验收记录。
