# Execution Diagnosis and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 沿用当前会话逐项执行方式，完成后做一次独立审查，不使用 Astra。

**Goal:** 减少探针开销，让执行故障可诊断，并阻止连续超时导致候选重写耗尽。

**Architecture:** 探针仅缓存会话内来源解析；Verifier 产生受控 ExecutionDiagnostic；Controller 根据程序码运行两次连续超时上限。沿用原完整性和语义验收链，不额外调用模型或提高预算。

**Tech Stack:** Python 3.11+ 主程序、兼容数据集 Python 3.9 的独立 pytest 探针、AgentScope、pytest、已有 trace/report。

**Spec:** `docs/superpowers/specs/2026-10-14-execution-diagnosis-recovery-design.md`

## Global Constraints

- 不增加模型、命令时间、任务时间或上下文预算，不关闭探针，不跳过来源和保护检查。
- 缓存最多 4096 条，每个 pytest 会话清空；最后的来源检查必须 fresh。
- classification=None 表示没有可用完整判定，不能据此进入 DONE。
- 两次连续 original COMMAND_TIMEOUT 后 BLOCKED / REPEATED_COMMAND_TIMEOUT；不因候选变化或修订契约清零。
- 取消、全局耗尽、清理失败保持原优先级；fixed/replay 不增加重试。
- 不修改经验加载、经验写入或模型 thinking；不创建新的阶段强制探索流程。
- 不调用真实模型，不使用 Astra。现有未跟踪 `docs/evaluations/2026-10-13-pitfall-experience-ab.html` 不动、不纳入提交。

## Review Focus

- 缓存掩盖运行中来源变化：Task 1 保留 fresh 最终解析及保护回归。
- timeout 的保护占位 false 被当作源码修改：Task 2 测未检查与失败的区别。
- 合成理由换成另一个合成码：Task 2 测具体 unmet_checks 及最后观测阶段。
- 换候选/修订契约绕过连续超时上限：Task 3 用真实 Controller 生命周期测试。
- 独立重跑或 fixed 超时却被认作成功：Task 3 覆盖确认和固定版本分支。

## 文件职责

| 文件 | 本次职责 |
| --- | --- |
| `src/reproagent/adapters/languages/python_pytest/probe/reproagent_pytest_probe.py` | 来源缓存与会话边界 |
| `src/reproagent/core/models.py` | ExecutionDiagnostic 和 Verdict 可选字段 |
| `src/reproagent/core/execution_diagnostics.py`（新） | 纯诊断构造、受控码、阶段摘要 |
| `src/reproagent/core/verifier.py` | 使用具体诊断，保持验收顺序 |
| `src/reproagent/core/controller.py` | 连续超时计数、明确反馈、终止 |
| `src/reproagent/reporting.py`、现有 report 模板 | 显示具体执行阻碍 |
| `src/reproagent/prompts/explore.md` | 区分执行障碍与候选内容失败 |
| 对应 tests 与 docs/reviews | 单元、集成、离线 SymPy 重放和验收记录 |

运行后端/Runner 不改变超时执行行为；仅在证明缺少必要事实时做最小补充，并记录原因。Task 4 真实重放证实包内候选来源被误算成原版来源，增加 Runner 的候选/原版来源与调用分离及四个边界回归，详见 Spec 和验收记录。

### Task 1：限制探针来源解析成本

**Files:** Modify 探针文件；Test `tests/integration/test_pytest_probe.py`、`tests/unit/test_probe_comparison.py`。

**Interfaces:** 保持 `origin(filename) -> str` 与 `profile(frame, event, arg)`；增加最大 4096 条的内部缓存及 pytest_sessionstart 清理，session_finish 不使用该缓存解析模块来源。

- [ ] 新增 `test_repeated_target_calls_resolve_filename_once`：模拟同文件的 1000 个目标 call，断言真实 resolver 调用 1 次、target call 计数仍为 1000。
- [ ] 新增 `test_origin_cache_is_bounded_and_cleared_per_session`：超过 4096 个键不无限增长；新 session 的同一路径重新解析；虚拟文件名与 OSError 不生成来源。
- [ ] 新增 fresh 来源测试：profile 后模拟路径解析结果变化，session_finish 使用新结果；外部来源/源码改变仍被 Runner 与 Verifier 拒绝。
- [ ] 运行这组测试观察 RED；实现缓存与清理，独立插件只用 Python 标准库和 pytest，避免 3.10+ 语法。
- [ ] 运行 probe、comparison、runner 与 source-protection 回归确认 GREEN，定向提交。不得用“耗时小于 X”代替调用次数断言。

### Task 2：结构化执行诊断与 Verifier 顺序

**Files:** Modify `core/models.py`, `core/verifier.py`; Create `core/execution_diagnostics.py`, `tests/unit/test_execution_diagnostics.py`; Test `tests/unit/test_verifier.py` 及序列化测试。

**Interfaces:**
- `ExecutionDiagnostic(code: str, stop_reason: str, duration_seconds: float, probe_complete: bool, protection_checked: bool, last_test_stage: str='unknown')`，frozen/slots dataclass。
- `Verdict.execution_diagnostic: ExecutionDiagnostic | None = None`，追加在现有字段之后，不破坏位置参数。
- `diagnose_execution(contract, candidate, run) -> tuple[ExecutionDiagnostic | None, tuple[str, ...]]`：pure helper，按 Spec 优先级检查，不读任意文件或调用模型；共享现有 candidate_hash 与绑定关系。
- `diagnose_runtime(run) -> tuple[ExecutionDiagnostic | None, tuple[str, ...]]`：只处理 raw/探针完整性，供上述 helper 与 fixed 分支共用；fixed 不使用 original 的 snapshot/角色绑定检查。
- 受控码：RUN_BINDING_MISMATCH / CANDIDATE_HASH_MISMATCH / CLEANUP_FAILED / COMMAND_TIMEOUT / LOG_LIMIT / EXECUTION_NOT_EXITED / PROBE_INCOMPLETE / SOURCE_PROTECTION_FAILED。

- [ ] 写参数化测试：绑定、哈希、清理、各 stop_reason、日志截断、probe 不完整、真实保护失败分别返回具体码；timeout+错误绑定仍优先报绑定错误。

  timeout 的核心断言为：`diagnostic.code == 'COMMAND_TIMEOUT'`、`diagnostic.protection_checked is False`、`'execution_exited' in unmet_checks`。完整性 helper 返回 None 仅能发生在原布尔完整性检查会通过时。
- [ ] 测 timeout+candidate_ok=False 占位：classification=None，protection_checked=False；unmet_checks 包含 `execution_exited`，probe 不完整时也包含 `probe_complete`；不能记录“源码确实被修改”。
- [ ] 测最后 test_phase 仅 setup passed 时，last_test_stage=`setup:passed`；不存在时 unknown；不伪造 `call_started`。阶段字符串仅受控 when/outcome，不包含任意 traceback。
- [ ] 测故障前后模型调用：执行未完成时语义 gateway 的调用次数为 0；正常完整执行保持旧判定路径。
- [ ] 测旧 Verdict 缺字段可解码，新的 None/诊断记录往返一致；不改变模型 verdict JSON 输出契约。
- [ ] 跑新增测试确认 RED；实现 helper 和诊断接入，填 reason/unmet_checks/execution_diagnostic。保留 `_integrity_ok` 的兼容 boolean 接口及 aggregate trace check；不会执行的检查不记录为通过。
- [ ] GREEN 后提交。绑定/哈希/实际保护错误仍 INVALID_CANDIDATE；执行不完整分类 None，next_action=`revise_candidate_or_request_information`；取消及清理不由模型处置。

### Task 3：Controller 防止重复超时空转

**Files:** Modify `core/controller.py`, `prompts/explore.md`; Create `tests/unit/test_controller_execution_recovery.py`; Extend `tests/unit/test_controller.py`、`tests/integration/test_agentscope_backends.py`。

**Interfaces:**
- `MAX_CONSECUTIVE_COMMAND_TIMEOUTS = 2`。
- `Lifecycle.command_timeout_streaks: dict[tuple[str,str,str], int]`；key 为 snapshot_id/environment_id/execution_role。
- 程序读取 `verdict.execution_diagnostic.code`，不匹配 reason 文本。增加 COMMAND_TIMEOUT / REPEATED_COMMAND_TIMEOUT / LOG_LIMIT / PROBE_INCOMPLETE / EXECUTION_NOT_EXITED 到需要接收它们的 action/phase 受控集合。

- [ ] 构造第一次超时、第二候选完成复现的 runner 序列：确认只发生预期执行/重跑，第一条反馈具有 code、duration、stage、failed checks，不提前终止。
- [ ] 构造两个不同候选连续超时：只执行 2 次，状态 BLOCKED，stop_reason REPEATED_COMMAND_TIMEOUT，证据 NONE，两个 run/verdict 均落盘，不启动第三候选或语义模型。
- [ ] 测序列 timeout → EXITED/NOT_REPRODUCED → timeout：计数已重置；仅读文件/换假设/契约修订不能清零。不同环境/角色不混用计数。
- [ ] 测第二次故障伴随 cleanup_ok=False 或 CANCELLED/EXHAUSTED：原优先级胜出；不得把它们折叠成 repeated timeout。
- [ ] 测 replay 和 fixed 的 timeout：保留诊断，独立确认失败；fixed 使用 diagnose_runtime，fix_validation_status=blocked，uncertainties 含实际受控码，原版已确认的证据等级保留，不额外重试。fixed 正常退出但测试失败仍为 failed，不改已有语义。
- [ ] RED 后接入 confirm_candidate 及执行后处理：在保存实际 verdict 后识别故障，first 返回精确反馈，second 记录 action/phase 拒绝并结束。非完整执行不入 context_blocked 的“证据超限复用”机制。
- [ ] 更新探索提示词：执行诊断不是失败断言；先据具体原因缩小候选或报告障碍，不凭“候选无效”反复重写。
- [ ] 跑 Controller、SDK 后端、预算/取消/重复候选/固定版回归确认 GREEN，定向提交。

### Task 4：输出与本案离线重放验收

**Files:** Modify `src/reproagent/reporting.py`, `src/reproagent/resources/report.md.template`; Test `tests/integration/test_observability_lifecycle.py`, `tests/unit/test_reporting.py`; Create `docs/reviews/2026-10-14-execution-diagnosis-recovery-acceptance.md`。

- [ ] 读取 render_report 和现有模板的占位符，检查旧错误句在哪个现有报告区域显示；扩展该区域，不新建另一套报告格式。
- [ ] 写输出测试：COMMAND_TIMEOUT、实际秒数、probe_complete、保护未检查、最后阶段和建议均可见；旧 Verdict 没有诊断时正常显示。
- [ ] 写观测测试：action/phase/verdict/run 可关联；aggregate 失败有具体 unmet_checks；连续计数与停止原因可见。新增 attrs 若被 allowlist 丢弃，扩展最小受控字段并测试，不放任整个执行对象进入 span。
- [ ] RED 后实现有界展示与事件，不复制大日志，不增加模型调用，保留 report.md 既有模板。
- [ ] 定向 GREEN 后，在新的输出目录重放原保存候选与同一 Python 3.9 环境；继续 60 秒命令上限，保留探针和字节码禁写。必须通过 Runner 执行并核对 raw/probe/protection/source_binding，不仅运行裸 pytest。
- [ ] 重放应 EXITED、probe_complete=True、cleanup_ok=True、source_binding_ok=True、protection.ok=True，并得到 Issue 对应异常；比较原记录但不覆盖它们。若语义/契约有其他问题，明确报告，不以裸失败声称任务 DONE。
- [ ] 跑完整离线测试一次、`git diff --check` 和独立审查；禁止 Astra、禁止真实模型请求。记录日志、命令、提交、通过/跳过数及未通过项目。
- [ ] 填验收文档、更新计划 checkbox 并定向提交。Push/合并按用户后续指令执行。

## 执行与未覆盖范围

实施前创建或复用隔离工作树，记录当前 main 与未提交材料，不改用户评测 HTML。使用该工作树自身解释器及仓库外 basetemp，避免主环境/默认经验库污染。

非超时类执行故障也获得具体诊断，但第一版仅给连续 COMMAND_TIMEOUT 加自动停止策略；不是万能环境修复器。真实模型端到端复跑及固定版本 differential 单独授权。修探针不保证候选/契约语义一定正确。

## 计划自查

Spec 的性能、诊断、恢复、输出和边界分别对应四项任务；所有恢复以程序码驱动。缓存不是新的来源证明；未执行保护不是保护失败；原有预算和验收条件不变。阈值 2 是受控尝试策略，不能作为同根因的证据。
