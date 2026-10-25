# Failure-Driven Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本次仅编写计划；尚未授权开始实现、付费模型评测、提交、push 或合并。

**Goal:** 在现有验收诊断上关联“上次失败尝试 → 本次候选实际变化 → 本次执行结果”，帮助探索减少无效重复；最终以差分成功、重复失败和总成本评估，不把“反馈送达”当成能力提升。

**Architecture:** 复用 Controller、不可变 Candidate/ExecutionResult/Verdict、现有有界反馈和 AgentScope 阶段消息。增加一个纯程序的尝试比较模块，按实际文件与执行条件比较候选，只在新失败后的下一探索阶段添加短关联；不增加模型调用或每轮 STATE。默认不注入，已有拒绝与停止策略保持原样。

**Tech Stack:** Python、现有 dataclasses/pytest/httpx.MockTransport、AgentScope 基础设施、TaskStore 事件、现有 Trace；不新增第三方依赖。

**Spec:** 本文 §2–5 固化本次对话已认可的增量设计；已有机制以 [Verifier 失败反馈设计](../../archive/specs/2026-10-14-verifier-failure-feedback-design.md) 和 [执行诊断与恢复设计](../../archive/specs/2026-10-14-execution-diagnosis-recovery-design.md) 为基础。

**Status:** 计划已编写，所有实施任务尚未执行。

## 1. 基线与现有能力

本次直接核对的源码位于 `E:/ReproAgent`，当前分支 `fix/cited-evidence-text`，HEAD `317342d7ea5d8567cad998b2c713e98a058f1659`。工作区已有其他任务的修改，包括评测汇总、测试和文档；本计划不包含这些改动。此前 `codex/task-state-context` 的实现与当前检出不完全相同，执行时不能混用两个目录的源码和依赖来源。

实施前先确定并记录目标 checkout、实际 HEAD、产品 diff 和未跟踪产品文件哈希。若在另一个已集成的分支执行，应重新核对下面的接口，不直接套行号，也不为实施本计划自动切换、reset 或合并工作区。

已经存在，不能重复实现：

- `core/controller.py::candidate_feedback`：生成有界反馈，经 `AgentContext.feedback` 交给下一探索阶段。
- `core/verification_feedback.py::render_verification_feedback`：保留 Verdict、受控错误码、失败检查、建议和省略计数，预算为 `min(8192, tool_response_bytes)`。当前 E 盘版本还带经过引用校验的 `evidence_text`。
- `adapters/agentscope/runtime.py::_message`：把本阶段契约、feedback、阶段历史送进 SDK 的真实消息；后续工具轮次保留普通历史。
- `Controller.confirm_candidate`：仅针对程序已证实的 `REVIEW_CONTEXT_TOO_LARGE`，缓存完整执行条件下的相同候选，返回 `DUPLICATE_CANDIDATE`；带 preconditions 的候选不复用该缓存。
- `Controller.execution_outcome`：同快照、环境、执行角色连续两次命令超时会受控停止；修改契约不应清掉此计数。

本版缺口是跨候选的事实关联：新候选到底改了代码、参数或前置条件，还是只换了 ID/hypothesis；相邻失败的诊断码是否相同。这些可以程序计算，不需要诊断 Agent。

## Global Constraints

- 原版源码只读；候选发布、引用资格、Verifier 成功门禁和 fixed 隔离不变。
- 不新增模型调用、诊断模型、自动重试额度、自动安装依赖或工具权限。
- 第一版只关联原版首次执行后产生的、已保存的可继续失败；独立重跑失败沿用原反馈，fixed 结果不进入探索关联。
- 普通断言失败、语义未确认和命令超时，不因“看起来重复”新增拦截。现有超限去重与连续超时停止机制保持原样。
- 模型提出的相似根因、修改理由不参与程序分支。相同诊断码不等于相同根因；文件变化不等于修正有效。
- 不启用每轮 STATE、材料提醒、历史清理或摘要；不改变它们的配置与默认值。
- 新关联正文最大 **1024 UTF-8 字节**，包含 JSON 包装；整体仍受现有反馈与最终模型请求容量守卫约束。
- 新增关联放不下时退回原反馈，不能挤掉原本可保留的诊断正文，不能新增一次容量停止。
- `failure_recovery_feedback: bool = False`；开关只控制新增关联与对应条件提示是否进入模型，不控制原诊断或原停止策略。
- 不记录或打印密钥。路径读取、证据解析、落盘复用现有安全路径与脱敏入口。
- 测试从实际目标 checkout 导入产品模块；pytest 的 `--basetemp` 必须在仓库外。验收不用 Astra。

## Review Focus

- 换 ID/hypothesis、文件排序等元数据变化不能冒充实际候选修正；真实文件改动也不能被说成已解决失败。由 Task 1 的比较测试覆盖。
- 契约、环境或执行策略边界不同，不能关联成“相同尝试”；契约修订不能重置原有超时计数。由 Task 1、2 覆盖。
- 相同测试可能有偶发行为或外部前置条件，不能套用证据超限缓存来拒绝普通重跑。由 Task 2 覆盖。
- 反馈容量、中文脱敏、缺记录或损坏记录不能让新增建议挤掉原诊断、伪造证据或改变主结果。由 Task 3、4 覆盖。
- 旧 feedback 留在消息历史不等于重新注入；独立重跑、fixed 和强制验证不能被误算成新的探索尝试。由 Task 4、5 覆盖。

## 2. 范围与交付边界

本版交付三件事：

1. 程序比较当前候选与同一比较边界内最近一次失败候选，保留可核对关联。
2. 新失败后给现有 feedback 增加可选的短 `attempt` 对象，明确修改维度与诊断是否重复。
3. 从已保存记录统计重复失败、失败后差分成功和成本，支持后续 A/B。

不做 AST 语义去重、跨任务检索、成功策略记忆、自动规划下一步、要求模型先填反思表，或强制“必须读材料才能再写测试”。修改后的候选仍由 Runner/Verifier 检查，比较模块不参与成功判定。

## 3. 尝试比较契约

新增 `core/attempt_feedback.py`，只处理可信记录和确定性比较，不读模型猜测文本。

### 3.1 内部类型与接口

- `AttemptSnapshot`：冻结 dataclass，字段 `candidate_id: str`、`manifest_hash: str`、`boundary_hash: str`、`dimensions: tuple[tuple[str, str], ...]`。
- `FailedAttempt`：冻结 dataclass，字段 `attempt: AttemptSnapshot`、`verdict: Verdict`、`result_code: str`。仅保存当前任务最近一次适用失败，不复制完整执行日志。
- `AttemptComparison`：冻结 dataclass，字段 `relation: str`、`previous_candidate_id: str | None`、`previous_run_ids: tuple[str, ...]`、`changed_dimensions: tuple[str, ...]`。
- `capture_attempt(candidate: Candidate, contract: IssueContract, environment: EnvironmentSnapshot, limits: BudgetLimits) -> AttemptSnapshot`。
- `compare_attempts(previous: FailedAttempt | None, current: AttemptSnapshot) -> AttemptComparison`。
- `project_attempt(comparison: AttemptComparison, current_verdict: Verdict, previous: FailedAttempt | None) -> dict`：返回封闭形状的程序数据，不修改或补写 Verdict。

`boundary_hash` 包括 snapshot、完整契约、环境快照，以及影响执行/取证的配置：`command_timeout_seconds`、`cleanup_timeout_seconds`、`log_bytes`、`tool_response_bytes`。不包含随时间自然递减的剩余步数、剩余秒数，也不把 task deadline 变化伪装成候选修改。

`dimensions` 固定按以下顺序存储 canonical hash：

| 维度 | 输入 |
| --- | --- |
| files | 按 path、role 排序后的 path/role/content_hash；使用经 Workspace 校验的完整文件清单 |
| language_id | Candidate.language_id |
| selectors | Candidate.selectors，保留执行顺序 |
| run_options | Candidate.run_options，保留参数顺序 |
| expectation_sources | 完整 SourceRef 元组 |
| fixture_refs | 完整 SourceRef 元组 |
| preconditions | 完整前置条件元组 |

不包含 candidate ID、storage_root、manifest ID、hypothesis、parent_id。路径或注释发生变化仍算文件变化；本版不声称能识别语义等价测试。hash 只用于事实比较，不自动成为源码引用或执行结果缓存键。

`relation` 的封闭取值：

- `first_attempt`：没有上一份适用失败，previous 字段为空。
- `unchanged`：边界一致，全部比较维度一致。
- `changed`：边界一致，至少一个维度不同，列出完整变更维度。
- `not_comparable`：上一份记录存在，但边界不同；不输出“相同失败”结论。

`project_attempt` 从两份已验证 Verdict 的 `(code, check, origin)` 集合计算 `diagnostic_relation: same_codes | different_codes | unknown`，只有可比较边界、两份均有 issues 时才比较。不得用自由文本 reason/堆栈字符串猜根因。

### 3.2 模型可见示例

当前候选、执行与契约绑定已经在现有 `verdict` 中，新对象只补关联，不重复整份状态：

```json
{
  "attempt": {
    "relation": "unchanged",
    "previous_candidate_id": "candidate-3",
    "previous_run_ids": ["run-3"],
    "changed_dimensions": [],
    "diagnostic_relation": "same_codes"
  }
}
```

结合当前 `COMMAND_TIMEOUT` 诊断，条件提示为：“与上次失败相比，测试文件和执行条件均未改变。两次报告相同诊断码，这不证明根因相同。根据执行阶段检查候选范围和初始化条件；仅修改假设文案没有改变执行内容。”

第一次失败仍使用现有主诊断、证据、未满足检查和 recovery；不凭空增加一条模型反思结论。

## 4. 写入、比较与提示时机

顺序：

```text
已发布候选
  → 校验候选和既有拒绝条件
  → 捕获当前 AttemptSnapshot，并与最近失败比较
  → 原样交给 Runner / Verifier
  → 原版 Verdict 保存成功
  → 若可继续失败：生成本次关联，再更新最近失败
  → 下一探索阶段使用现有 feedback 发送一次
```

- 在 `Lifecycle` 末尾增加 `last_failed_attempt: FailedAttempt | None = None`，不新增全局经验库或独立状态机。
- 保存 Verdict 之前，不把尝试写成“已判定失败”。原先会抛出的执行/模型异常没有合法 Verdict 时，不伪造 FailedAttempt。
- 原版首次执行通过验收并准备独立重跑时，清掉最近失败指针；重跑失败不套上上一个候选的失败关联。
- 成功修订契约后清掉该指针；修订被拒绝时保留。现有 `command_timeout_streaks` 不受影响。
- 现有超限缓存命中时没有新的实际执行：仍返回原 `DUPLICATE_CANDIDATE`，可记录 comparison，但不制造 run、不增加实际失败次数、不替换最近一次实际失败。
- `decision=stop` 沿用既有终止语义，不为发送建议额外请求模型。
- 后续 ReAct 轮次可以看到历史反馈，但本功能不在每次 `on_model_call` 重复插入，也不删除工具配对历史。
- 紧凑关联随现有 `verification.feedback` 事件保存；新增 `candidate.compared` 事件保存每次比较身份、边界、维度和实际是否执行，开启/关闭提示均记录，以便比较 A/B。复用 `mark` 接入 Trace；事件没有正文也能计数。
- 比较/观测的可选处理失败应降级为无新增关联，事件标明不可用；不能吞掉原有执行、校验、预算或取消异常。不能用宽泛异常捕获绕过原 Workspace/Verifier 检查。

## 5. 配置与兼容

`TaskRequest` 末尾追加 `failure_recovery_feedback: bool = False`，校验严格布尔类型。已有 JSON 配置可以显式设为 true；旧配置省略时行为不变，不增加必填参数。

沿 `app.create_controller → Controller` 传入只控制模型关联的同名开关。评测侧 `run_case`、`run_batch` 增加同名 keyword-only 默认参数；SWT `run` 仅增加一个可选 `--failure-recovery-feedback`，并把实际值写入轮次配置/来源哈希。不得在比较实验时绕过产品入口临时替换提示词。

不修改 `Candidate`、`Verdict` 或 `AgentContext` 的持久化 schema，不新增工具参数。内存中的 FailedAttempt 不要求实现断点恢复；旧任务缺少 comparison 事件应统计为 unknown，而不是“没有重复”。

## 6. 文件职责

| 文件 | 操作与职责 |
| --- | --- |
| `src/reproagent/core/attempt_feedback.py` | 新增：冻结类型、比较、模型小投影 |
| `src/reproagent/core/controller.py` | 修改：最近失败生命周期、比较事件、条件反馈接入；保持原验收和拒绝分支 |
| `src/reproagent/core/verification_feedback.py` | 修改：可选 attempt 参数及容量降级，保留现有引用正文与省略策略 |
| `src/reproagent/core/models.py` | 修改：TaskRequest 开关；不改 Candidate/Verdict/AgentContext |
| `src/reproagent/app.py` | 修改：把 TaskRequest 开关传给 Controller |
| `src/reproagent/prompts/explore.md` | 修改：解释 attempt 的数据身份、变化不代表修复、相同码不代表相同根因 |
| `evals/run.py`、`evals/swt_bench/run.py`、`evals/swt_bench/__main__.py` | 修改：单开关贯通及来源记录 |
| `evals/failure_recovery.py` | 新增：只读已有任务产物的诊断汇总，不调用模型 |
| `tests/unit/test_attempt_feedback.py` | 新增：纯比较、边界与投影测试 |
| `tests/unit/test_controller_attempt_feedback.py` | 新增：生命周期、事件、现有停止策略不变 |
| `tests/unit/test_verification_feedback.py` | 修改：容量、脱敏、引用正文回归 |
| `tests/integration/test_attempt_feedback_delivery.py` | 新增：SDK 实际请求中的一次反馈与后续纠偏 |
| `tests/unit/test_failure_recovery_metrics.py` | 新增：指标口径、缺记录、重跑/fixed 排除 |
| `docs/reviews/2026-10-23-failure-driven-correction-acceptance.md` | 实施时新增：基线、测试、证据与未覆盖项 |

不修改独立 Verifier 的判定规则、Runner 进程执行逻辑、工具权限、经验学习/检索、STATE/压缩模块、HTML 布局或当前其他任务的评测修复。

## 7. 实施任务

### Task 1: 确定性尝试比较

**Files:** Create `core/attempt_feedback.py` 和 `tests/unit/test_attempt_feedback.py`（路径根为上述 src/tests）。

**Interfaces:** 产出 §3.1 的三个类型和三个函数；不依赖 Controller 或模型。

- [ ] 写失败测试：`test_identity_and_hypothesis_changes_are_unchanged`，只换 ID、路径根、hypothesis，relation=unchanged。
- [ ] 写参数化失败测试：每个比较维度单独变化恰好报告该维度；文件清单重排不算变化，selector/参数顺序变化要算。
- [ ] 写失败测试：契约/环境/timeout/log 配置变化为 not_comparable；空 issues 为 unknown；相同 code 但不同 origin 不判为 same_codes；输入不可变。
- [ ] 运行 `python -m pytest tests/unit/test_attempt_feedback.py -q --basetemp <外部临时目录>/attempt-unit`，确认因缺实现失败；按 §3 实现并重跑至全部通过。
- [ ] 只提交该任务文件：`feat: compare candidate attempts using recorded execution inputs`。

### Task 2: 关联 Controller 的原版失败生命周期

**Files:** Modify `core/controller.py`；Create `tests/unit/test_controller_attempt_feedback.py`。

**Interfaces:** 消费 Task 1；仅记录最近一份适用 FailedAttempt。此任务先提供可信比较与事件，Task 3 再把关联放进模型反馈；现有 feedback 在此任务保持不变。

- [ ] 写失败测试：先失败 c1，再失败 c2；保存的比较事件可以关联 c2 与 c1 的实际变化，两个真实 run/verdict 都可核对，现有反馈仍可进入下一阶段。
- [ ] 写失败测试：仅换 hypothesis 仍允许普通再次执行；代码有变化仍可能重复诊断；缺 Verdict 不冒充失败；原版验收成功、重跑失败、契约成功修订与跨任务均不串关联。
- [ ] 写保护测试：原超限缓存仍只拒绝相同且无 preconditions 的候选；普通语义失败/超时不新增去重；契约修订不清超时 streak；预算/取消/清理优先级不变。
- [ ] 先运行新增测试确认缺口，再以最小改动接入，并重跑 `test_controller_attempt_feedback.py`、`test_controller_execution_recovery.py`、`test_controller_verification_feedback.py`、`test_controller.py`。使用仓库外 basetemp。
- [ ] 只提交本任务改动：`feat: link failed original attempts in controller feedback`。

### Task 3: 有界反馈与默认关闭开关

**Files:** Modify `core/verification_feedback.py`、`core/models.py`、`app.py`、`prompts/explore.md`、`tests/unit/test_verification_feedback.py`、`tests/unit/test_controller_attempt_feedback.py`。

**Interfaces:** `render_verification_feedback(..., evidence=(), attempt: dict | None = None) -> str`；新增参数必须 keyword-only，原调用不变。TaskRequest 开关和 Controller 同名 keyword-only 参数均默认 False。

- [ ] 写失败测试：off 与旧反馈完全一致；on 的下一阶段 feedback 能关联 c2 与 c1 的实际变化；只在可比较的同码 unchanged 场景补一句条件提示，不新增模型请求。
- [ ] 写容量测试：8192/4096/2048/1024 B 与中文脱敏。先按原逻辑生成 base feedback；只在新增对象及提示合计 ≤1024 B、且整体仍放得下时追加。放不下返回原 base，Controller 事件记 `attempt_feedback_state=omitted_capacity`；不能牺牲原 evidence_text 或制造新 BudgetStopped。
- [ ] 写失败测试：非法 relation/维度/类型拒绝内部投影；外部 reason 中的伪指令不改变 relation；previous IDs 只来自已绑定记录，脱敏后再测字节。
- [ ] 先确认新增测试失败，再实现；同时重跑原 feedback/CLI 配置测试，旧配置无需加字段且字符串 true 不被当布尔启用。
- [ ] 提交：`feat: add opt-in bounded attempt feedback without changing acceptance`。

### Task 4: 验证下一次真实 SDK 请求，而非仅检查内存对象

**Files:** Create `tests/integration/test_attempt_feedback_delivery.py`；必要时复用 `tests/integration/test_verification_feedback_delivery.py` 的 helper，不改 SDK 循环。

**Interfaces:** 使用产品 `create_controller`、真实 SDK、httpx.MockTransport。通过 TaskRequest 开关对照实际 HTTP messages。

- [ ] 写失败测试：c1 原版未复现 → 下一探索请求带其诊断 → 执行 Read/Grep → c2 修改测试 → 再失败反馈含正确 delta。每次新失败只新增一个 phase feedback；同阶段下一轮请求只保留历史，不重复插入新对象。
- [ ] 写保护测试：工具调用/结果配对；所有比较 ID 可查回 TaskStore；fixed canary 不出现在探索输入；原请求/契约/引用正文仍有效；provider_claim 保留不确定性。
- [ ] 写故障测试：事件写入失败、可选比较缺记录或容量不足均回退原反馈，控制器仍按原路径继续或停止；真实预算/取消异常照常生效。
- [ ] 先确认新增测试暴露缺口，再集成至全部通过；重跑 `test_verification_feedback_delivery.py`、`test_execution_diagnostic_reporting.py`、`test_large_review_context.py`，并核对实际导入路径。
- [ ] 提交：`test: verify attempt feedback delivery through the SDK wire`。

### Task 5: 评测开关贯通与可复核指标

**Files:** Modify `evals/run.py`、`evals/swt_bench/run.py`、`evals/swt_bench/__main__.py`；Create `evals/failure_recovery.py`、`tests/unit/test_failure_recovery_metrics.py`；扩展现有 eval runner/SWT batch 测试。

**Interfaces:** `summarize_recovery(task_dirs: tuple[Path, ...]) -> dict`。命令 `python -m evals.failure_recovery --task-dir <task> [--task-dir <task2>] --output <目录>`；读取 TaskStore/任务产物，不访问模型、网络或原版/修复版源码。

- [ ] 写失败测试：开关完整传到 TaskRequest，默认 off；轮次记录包含实际值；A/B 两臂均保留比较事件，不能只给 B 记数据。
- [ ] 写指标测试：把 Controller 独立重跑、fixed、缓存拒绝与新的候选原版执行分开；旧记录无比较字段为 unknown，不能补零；未提供 fixed 不计差分成功。
- [ ] 指标精确定义：`unchanged_same_diagnostic_failures`=相邻可比较、内容/条件均 unchanged、实际执行且同码的失败次数；`failed_tasks_then_differential_success`=至少一次实际原版候选失败后，最终达到 DIFFERENTIAL_VALIDATED 且 fix_validation passed 的任务数，同时输出符合条件任务总数。
- [ ] 其他输出包括：实际候选数、原版实际执行数、重复失败占可比较重试比例、失败后读取/修订/候选发布的动作计数、总模型 token/耗时、unknown/missing 数量。动作变化只是诊断，不能当作有效纠偏；同码变化也不是已知根因变化。
- [ ] 实现并运行新增指标测试、`tests/unit/test_eval_runner.py`、SWT batch/来源相关测试，确认旧报告可读；提交 `feat: measure failure recovery from recorded task evidence`。

### Task 6: 离线验收与真实实验预注册

**Files:** Create `docs/reviews/2026-10-23-failure-driven-correction-acceptance.md`；实验预注册/清单/结果存仓库忽略的独立输出目录。

- [ ] 先运行上述受影响模块，再运行当前目标基线的 unit/integration 全量回归。使用独立解释器来源核对、仓库外 basetemp、可发现的 ripgrep；大组顺序执行，保留首次失败，不能只报重跑结果。
- [ ] 离线必过清单：正确尝试关联；主诊断/证据不被挤掉；无额外模型调用；默认 off；同阶段不反复注入；普通重跑不被阻断；超限/超时旧策略不变；fixed 和引用权限隔离；反馈正文和事件链可核对。
- [ ] 生成验收文档，逐项记录命令、实际计数、源码/配置哈希、失败与修复、未覆盖项。没有真实模型实验时，只能写“机制通过”，不能写“减少重复失败/提高成功率”。
- [ ] 在获得真实调用授权后，先做最多 2 个任务的触发预检，确认存在合法失败后继续的路径、fixed 环境可用、实际请求中开关生效。预检任务算开发样本，不混入正式保留集；不能反复试到出现想要的轨迹。
- [ ] 正式探索性试验：冻结 4 个已做环境和参考差分检查的任务，每任务 A/B 各 3 次，共 24 次；A=原反馈，B=原反馈+新增关联。随机/交错调度，模型、预算、经验库、STATE/压缩开关全部一致，提供 fixed；不强制两臂执行同样的未来动作。
- [ ] 付费运行前登记实际模型、费率、每任务 20 步/900 秒/命令 60 秒，以及累计费用硬上限和提前停止条件。用现有 `model_cost_limit` 和已验证费率；费用 unknown 且无可执行总上限时不启动，不能边跑边扩样本。
- [ ] 报告同时给出全样本 ITT 和失败触发覆盖率；不因 B 没触发、任务变难或某次路径不同而剔除。案例选择、异常样本和预检偏差完整保留。
- [ ] 默认启用的判断：本轮 24 次只提供探索信号。若 B 出现证据泄漏/误拦截/新停止即修复机制；若成功数下降或总 token 上升超过 10%，保持关闭并调查。只有差分成功不降、可比较重试中的重复失败率相对下降至少 20%、成本未明显恶化时，才进入另一个未调试清单扩大验证；这些门槛不是统计显著性证明，不能据此直接默认开启。
- [ ] 有官方 harness 回执时另报官方结果；本地 DIFFERENTIAL_VALIDATED 不替代官方 resolved。实施验收提交与付费实验结果分别记录，不自动 push 或合并。

## 8. 计划自审与交付

- 与已有 2026-10-18 Failure Artifact Hints 计划区分：该计划针对“省略的失败正文是否可读”；本计划针对“相邻尝试实际改变了什么”。本版不顺带启用材料提示或扩大材料存储。
- 第一版不扩大硬拒绝白名单，减少重复执行的实际效果依赖模型使用反馈；不能以“程序强行少跑”冒充纠偏成功。
- 本计划正文没有授权执行代码、付费调用、提交、push 或合并。实施时按 Task 1–6 顺序执行并在验收记录中附实际基线；无需重新设计另一套 STATE。
- 最终用户交付：变更文件清单、离线验收报告、默认 off 的配置方法，以及单独标注的真实评测证据或“尚未验证收益”。
