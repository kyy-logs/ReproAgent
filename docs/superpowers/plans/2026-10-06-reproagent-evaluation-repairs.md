# ReproAgent 真实评测失败修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复核验上下文、模型协议与候选推进问题，让有效测试完成独立差分交付，并得到可信的复测结果。

**Architecture:** 沿用 Controller/Runner/Verifier 与 native/AgentScope 边界。新增纯函数上下文构建器、共享模型选项与工作流策略；硬证据与完整记录仍由现有流水线保存，评测单独负责来源固定和环境对照。

**Tech Stack:** Python >=3.11、pytest、httpx、AgentScope 2.0.9 可选依赖；现有 Markdown 报告和 SWT 评测工具。

**Spec:** [修复设计](../specs/2026-10-06-reproagent-evaluation-repairs-design.md)。

**Evidence:** [0/5 诊断报告](../../evaluations/2026-10-06-swt-development.md)。本计划未执行，所有任务保持未勾选。

**Execution:** 保留当前会话逐项执行的选择。每任务 TDD 与验证；全分支完成后独立审查。按优先级分三批交付，不等到扩大环境覆盖才验证核心修复。

## Global Constraints

- 默认动作预算仍为 20，单命令 60 秒，任务 900 秒；tool_response_bytes 默认仍为 32768。
- 原始输入、候选字节、源码快照与完整执行记录保留；硬核验、来源哈希、进程清理和重复失败确认继续有效。
- REPRODUCED 仍要求预期断言、目标触发、失败匹配全部为真，并引用预期来源与实际失败证据。
- 明确的语义否定不能在协议纠正中变成肯定；缺失关键证据不得宣布成功。
- 生成阶段不读取修复版代码、数据集 patch/test_patch/hints，也不根据隐藏修复反馈迭代候选。
- 官方成绩只来自独立 SWT harness；原版重复失败、诊断候选 1/0、DONE 均不得代替正式成绩。
- native 与 AgentScope 使用同一预算、协议和模型选项规则；AgentScope 固定 2.0.9。
- 不自动扩大预算、换模型、合并多轮最佳结果或替换失败样本。
- Windows 工作区路径与 Python 解释器路径分开处理；解释器保持 venv 身份，不解析其符号链接为基础 Python。
- 旧任务记录可读取；新字段带默认值。用户主要审查 report.md，新增诊断数据留在机器记录中。

## 文件与责任

| 文件 | 责任 |
| --- | --- |
| `evals/swt_bench/provenance.py`（新增） | 实现来源指纹与变化检查 |
| `src/reproagent/core/review_context.py`（新增） | 有界、可引用的核验证据投影 |
| `src/reproagent/adapters/models/options.py`（新增） | 共享输出上限与提供方显式选项 |
| `src/reproagent/core/workflow.py`（新增） | 候选状态、动作限制及确定性推进 |
| `src/reproagent/paths.py`（新增） | 工作区内部平台路径表示 |
| `evals/swt_bench/control.py`（新增） | 独立参考测试与环境对照回执 |
| 现有 `core/models.py`、`agent.py`、`controller.py`、`verifier.py` | 接入以上单元与兼容字段 |
| 现有两个 gateway、`reporting.py`、`exporter.py`、`workspace.py`、`store.py`、`resources/replay.py` | 选项、结果展示和路径集成 |

## Review Focus

1. 压缩后的来源摘要掩盖错误模块或被篡改的失败文件：Task 2 必须先完整硬检查，引用被改时零模型调用。
2. 模型返回 length、空内容或恶意错误字段名：Task 1/3 必须分类且不记录正文，否定不能经纠正升级。
3. 预算只剩两步、契约已修订或重复动作：Task 4 必须拒绝未校验提交，不超 20 步；缺少事实仍可请求信息。
4. 长路径、Unicode 和 Linux venv 符号链接：Task 6 验证整个交付与 replay，解释器身份不改变。
5. 源码在评测中改变、参考测试实际未执行或旧报告混入：Task 1/7/8 标记无效/阻塞，保持所有样本与轮次隔离。

## 第一批：P0，恢复核验与交付

### Task 1：固定评测来源与动作诊断

**Files:** 新增 `evals/swt_bench/provenance.py`、`tests/unit/test_swt_provenance.py`；修改 `evals/swt_bench/run.py`、`results.py`、`src/reproagent/core/agent.py`、`controller.py`、两个 gateway；测试 `tests/integration/test_swt_batch.py`、`tests/unit/test_model_gateway.py`、`tests/unit/test_controller.py`。

**Interfaces:** `capture_tool_source(root: Path) -> dict`、`assert_tool_source(root: Path, receipt: dict) -> None`。覆盖 `src/reproagent` 的代码/提示词/资源、`evals` 的 Python 文件和 `pyproject.toml`；不读取秘密文件。变化抛出 `ToolSourceChanged`。round 新增 `source_comparison_status`，取 `verified/changed/not_recorded`，旧轮为 not_recorded。

- [ ] 隔离执行工作区并建立自己的工具 venv；记录 Git SHA、已有并行修改与实际导入的 `reproagent.__file__`。不覆盖现有 CI/解释器路径修改，也不把未知修改混进提交。
- [ ] 写 `test_source_change_invalidates_round_and_stops_later_model_calls`：第一例后更改受指纹保护的提示词，断言后续例不调用模型，20 例仍有记录，本轮不能比较版本。
- [ ] 写 `test_action_error_events_use_codes_not_raw_response`：未知候选 ID/参数错误有 action/result_code/参数哈希/steps_remaining；正文、密钥、提供方错误字段名不进事件。
- [ ] RED：运行上述测试，确认缺少来源检查和事件字段导致失败。
- [ ] 实现来源前后检查与 `action.selected/action.completed/action.rejected/protocol.error` 事件；参数只存 canonical hash。错误代码使用闭集，追加字段兼容旧读取。
- [ ] GREEN：运行 `tests/unit/test_swt_provenance.py`、`tests/integration/test_swt_batch.py`、`tests/unit/test_model_gateway.py`、`tests/unit/test_controller.py`。提交仅包含本任务文件，消息 `feat: pin evaluation source and trace bounded actions`。

### Task 2：紧凑且可引用的语义核验上下文

**Files:** 新增 `src/reproagent/core/review_context.py`、`tests/unit/test_review_context.py`；修改 `core/verifier.py`、`prompts/review_evidence.md`；测试 `tests/unit/test_verifier.py` 与新增 `tests/integration/test_large_review_context.py`。

**Interfaces:** 定义 frozen `ReviewContext(payload: dict, available_refs: tuple[EvidenceRef,...], input_bytes: int, output_bytes: int, omitted_fields: tuple[str,...])`。`build_review_context(contract, candidate, run, *, read_ref, read_file, max_bytes: int, protocol_error: str='') -> ReviewContext`；核心放不下抛 `ReviewContextTooLarge`。`read_ref(EvidenceRef)->str` 校验全文哈希后由构建器提取引用行段；`read_file(FileEntry)->bytes` 校验候选哈希。

- [ ] 写 `test_132_verified_origins_fit_32768_without_mutating_execution`：构造与 Sphinx 记录同等规模的来源映射，断言投影 JSON ≤32768、完整断言及预期/失败引用存在，原 observation 的132条映射不变。
- [ ] 写 `test_wrong_origin_or_changed_failure_cannot_be_hidden_by_projection` 与 `test_citation_outside_sent_line_range_is_rejected`；必须非成功且无绕过硬校验的模型调用。
- [ ] 写 `test_core_evidence_too_large_stays_uncertain`：测试/关键数据超过容量时不截断关键内容，不返回 REPRODUCED。
- [ ] RED：执行新增单元测试及 verifier 回归，看到超限/缺构建器失败。
- [ ] 实现完整硬校验后的投影：来源摘要、相对必要路径、引用真实行段、完整候选/关键断言与失败。每次加入纠正信息后重新检查完整 JSON 体积；未发送引用不能引用。
- [ ] 实际 pytest 集成：生成会加载大量目标子模块的临时项目，使用脚本模型返回有来源的 verdict；原版失败、修复版通过；同时验证一个来源越界场景被拒绝。脚本模型证明流水线，不作为能力分数。
- [ ] GREEN：执行 `tests/unit/test_review_context.py`、`tests/unit/test_verifier.py`、`tests/integration/test_large_review_context.py`，记录 RED→GREEN。提交 `fix: compact semantic evidence without weakening verification`。

### Task 3：修复实际输出预算与模型错误分类

**Files:** 新增 `src/reproagent/adapters/models/options.py`、`tests/unit/test_model_options.py`；修改 `core/models.py`、`core/protocol.py`、`core/agent.py`、`core/verifier.py`、`adapters/models/provider.py`、`adapters/agentscope/gateway.py`；测试 `tests/unit/test_model_gateway.py`、`tests/unit/test_model_protocol_regressions.py`、`tests/integration/test_agentscope_gateway.py`；新增 `examples/model.deepseek.non-thinking.json`。

**Interfaces:** `ModelRequest.max_output_tokens: int|None=None`；`ModelConfig.thinking_mode: str|None=None`，仅 enabled/disabled/None。新增 frozen `ProviderRequestOptions(output_limit: int, extra_body: dict)` 与 `resolve_model_options(config, request)->ProviderRequestOptions`。`ModelOutputError(message, code='INVALID_PROTOCOL', retryable=True)` 保持 ValueError 兼容。

- [ ] 写 `test_config_8192_reaches_wire_when_request_limit_unspecified`、`test_explicit_request_1024_remains_capped`、`test_invalid_limits_and_thinking_mode_rejected`。默认配置仍发送4096。
- [ ] 用 MockTransport 分别验证 native 和真实 SDK 的请求：thinking_mode=disabled 时最终 HTTP JSON 含 `thinking.type=disabled`；None 不增加该字段；两个后端输出上限相同。
- [ ] 写 `test_length_is_not_retried_with_identical_request`、`test_complete_invalid_json_corrects_at_most_three_times`、`test_negative_semantics_never_promoted`。length 的 usage 仍记账。
- [ ] RED：运行新增单元与真实 SDK 集成测试。
- [ ] 实现共享选项：native 直接写 JSON；SDK 使用其 OpenAI 调用的 extra_body 通道，保持 SDK retries=0。分类输出截断/空内容/结构错误/网络/HTTP 错误；非可纠正错误终止该逻辑调用，网络重试上限仍3。
- [ ] 事件记录受控 outcome/finish_reason、response_kind、effective_output_limit、content_bytes；不保存推理文本或原始响应。
- [ ] 新评测配置保留 deepseek-flash、4096 上限并显式 disabled；原 `examples/model.deepseek.json` 不覆盖。只用于新轮次对照，不声称关闭思考必然提升分数。
- [ ] GREEN：执行本任务所有测试与 AgentScope 后端回归，提交 `fix: align model output limits and classify protocol failures`。

### Task 4：确保候选执行、重复核验与提交

**Files:** 新增 `src/reproagent/core/workflow.py`、`tests/unit/test_workflow.py`；修改 `core/models.py`、`core/controller.py`、`core/agent.py`、`prompts/explore.md`；测试 `tests/unit/test_controller.py`、`tests/unit/test_agent.py`、`tests/integration/test_agentscope_backends.py`。

**Interfaces:** frozen `WorkflowState(steps_remaining, grounded, candidate_id, candidate_contract_version, current_contract_version, has_execution, reproduced, repeated_action)`；frozen `WorkflowDecision(forced_action: AgentAction|None, allowed_actions: tuple[str,...], reason_code: str)`；`choose_workflow(state)->WorkflowDecision`。`AgentContext` 追加默认 `allowed_actions=()`，ReproAgent 将非空集合与现有 schema 取交集。

- [ ] 写 `test_pending_candidate_forces_run_without_model_decision`、`test_reproduced_candidate_forces_submit_without_model_decision`；强制动作也消耗1步。
- [ ] 写 `test_two_remaining_steps_do_not_allow_new_unrunnable_candidate`、`test_missing_facts_still_allow_information`、`test_revised_contract_cannot_submit_stale_candidate`。
- [ ] 写 `test_same_action_and_result_twice_cannot_consume_all_twenty_steps`；重复键包含契约版本、候选集合/执行状态、动作和参数哈希，状态变化后允许合法重试。
- [ ] RED：运行 workflow/controller 定点测试。
- [ ] Controller 在 Explorer 之前应用策略：真实候选ID的run、有效verdict的submit走现有分支，避免额外模型决策；其余动作仍由Agent选择。保留时间/成本/取消检查；≤4步且预期充分时保留写/跑/提交余量。
- [ ] 更新脚本模型测试的预期调用序列，验证其语义核验仍真实执行；不能删掉失败场景来适配新序列。
- [ ] GREEN：执行 workflow、controller、agent 及两种 Agent 后端集成。提交 `fix: advance valid candidates within the action budget`。

**第一批验收:** 完整离线套件通过；将 Sphinx-8801 已有候选作为确定性回归输入可以完成严格核验；再用原始 Issue 发起一个全新真实模型任务，要求 DIFFERENTIAL_VALIDATED 与新副本独立1/0。实际模型未通过时保留失败，先定位，暂不花费整轮预算。

## 第二批：P1，结果与本地环境可靠性

### Task 5：区分接口提案、重复失败与差分成功

**Files:** 修改 `prompts/analyze_issue.md`、`prompts/explore.md`、`prompts/review_evidence.md`、`core/models.py`、`core/controller.py`、`exporter.py`、`reporting.py`、`resources/report.md.template`、`evals/schema.py`、`evals/run.py`、`evals/swt_bench/results.py`；测试 `tests/unit/test_model_protocol_regressions.py`、`tests/unit/test_controller.py`、`tests/unit/test_reporting.py`、`tests/unit/test_swt_results.py`。

**Interfaces:** `TaskResult.fix_validation_status='not_provided'`，枚举 not_provided/passed/failed/blocked；`EvalResult` 增加同名默认字段，报告原样传递。旧记录默认 not_provided。

- [ ] 写 `test_repeated_failure_with_failed_fix_is_not_differential_success`：DONE/重复观察可保留，但报告主结论明确差分未成立，评测有效成功数为0。
- [ ] 写 `test_no_fixed_version_preserves_buggy_only_workflow` 与 `test_legacy_record_without_fix_status_can_be_read`。
- [ ] 写接口提案契约回归：mode="b"、Domain 列名只能作为待核对的提议；脚本模型标记缺失信息时，Controller 必须阻止写候选，允许原版来源读取/修订。此测试不证明模型一定能正确识别所有提议。
- [ ] RED：运行上述定点测试。
- [ ] 接入明确的修复版结果；调整三个提示词，要求区分可观察行为和提议中的接口名称，不能仅凭提议把接口名称当成正确性标准。隐藏修复状态仍不反馈给 Explorer。
- [ ] GREEN：运行 controller/reporting/模型协议/SWT汇总回归；人工对照 Flask-4992 报告和测试。提交 `fix: report reproduction and fixed-version outcomes separately`。

### Task 6：内部处理 Windows 长路径

**Files:** 新增 `src/reproagent/paths.py`、`tests/unit/test_paths.py`、`tests/integration/test_windows_long_paths.py`；修改 `store.py`、`workspace.py`、`runner.py`、`adapters/runtimes/local.py`、`exporter.py`、`resources/replay.py` 中必要的工作区路径使用点；保留现有 `absolute_python` 的 venv 语义。

**Interfaces:** `workspace_path(path: Path)->Path`：Windows 内部工作区统一绝对、长路径表示，其他平台保留原规则；`display_path(path: Path)->str` 仅用于展示。解释器路径不经过 workspace_path。

- [ ] 写 Windows 集成用例：普通路径参数下，原子临时文件路径超过260字符的快照、执行、保护检查、导出和独立replay全部成功；用户无需手写平台前缀。
- [ ] 写 Unicode/越界链接回归；另验证 Linux venv 的解释器仍运行其自己的 pytest/site-packages。
- [ ] RED：在 Windows 执行长路径用例，确认原有快照/物化失败；Linux 只跳过Windows专属用例，仍执行路径和venv回归。
- [ ] 统一内部根与子路径表示、I/O和源身份比较；内嵌 replay 保持独立运行，不依赖已安装 reproagent。不得修改注册表或放宽 safe_child。
- [ ] GREEN：Windows 长路径整链路 + Windows/Linux普通路径与解释器回归。提交 `fix: support long workspace paths without changing venv identity`。

### Task 7：让环境对照证明测试真的执行

**Files:** 新增 `evals/swt_bench/control.py`、`tests/integration/test_swt_controls.py`；修改 `evals/swt_bench/__main__.py`、`prepare.py` 与 `docs/swt-bench.md`；本地版本化绑定/依赖清单存入独立新目录。

**Interfaces:** `run_reference_control(row: dict, binding: dict, output: Path)->dict`，row仅在独立评测端读取，包含固定patch/test_patch与目标节点；新增 CLI `python -m evals.swt_bench control --snapshot ... --manifest ... --bindings ... --output ...`。

- [ ] 写 `test_collection_error_is_not_a_discriminating_control`、`test_skipped_or_setup_failed_nodes_do_not_qualify`、`test_success_requires_buggy_call_failure_and_all_fixed_calls_pass`、`test_control_never_mutates_generation_repositories`。
- [ ] 写参考补丁应用后的内容校验：应用返回0但未改任何目标文件必须报 patch_error，避免外层Git忽略规则造成假控制实验。
- [ ] RED：运行新增controls集成测试。
- [ ] 在全新副本应用参考补丁并采集pytest节点/阶段；保存源码、补丁、解释器及依赖哈希和完整日志。所有秘密环境变量剥离；参考材料不进入 run_case 的生成输入。
- [ ] Sphinx 按原版 version 分开固定扩展依赖，验证5个原有效对照，其他3例逐例解释；不能为了成功数替换固定清单。12例旧Python/构建阻塞继续明确标记，另列环境专项。
- [ ] GREEN：controls、prepare、SWT隔离测试通过；真实参考对照回执齐全。提交 `feat: validate reference controls in isolated environments`。

## 第三批：P2，重评并确定能否使用

### Task 8：冻结版本后的开发集、新样本与官方判分

**Files:** 新增实际日期的 `docs/evaluations/<date>-swt-repaired.md`；修改 `docs/swt-bench.md`、`README.md`、`docs/implementation-status.md`。评测轮次与完整事件留在 `repro-results`；源指纹和回执来自 Task 1/7。

**Interfaces:** 复用现有 SWT CLI 和预测/来源校验；不新增能力评分算法。summary 增加来源状态以及实际执行/原版重复/差分/独立交付/官方判分的明确计数，缺失官方结果显示待判分。

- [ ] 完整离线测试：`.venv\Scripts\python.exe -m pytest tests/unit tests/integration -q --basetemp=.tmp/repair-final-<unique>`；`.venv\Scripts\python.exe -m pip check`。检查真实SDK测试被执行，不能只看总通过数。
- [ ] 冻结干净实现提交、实际导入路径、模型配置和依赖回执；在一轮内来源发生变化则该轮不可用于对比。
- [ ] 原 dev20 全部20例重新执行，使用 new native/native + 显式disabled配置，动作/时间预算沿用20/60/900；每例记录，失败不替换。不把原第三轮和新轮解释成纯算法对照，因为模型选项/环境变化也需列出。
- [ ] 全部已交付包独立1/0重跑并技术检查；人工结论未提供时保持 pending。Sphinx-8801 的既有诊断候选不加入新生成成功数。
- [ ] 从排除dev20与已调试历史案例的合格数据中，用固定seed按仓库轮询选择10个新样本；选择不依赖隐藏补丁、参考测试通过情况或生成结果。事先冻结清单，准备失败仍留在分母。
- [ ] 在相同配置与预算下跑新清单；公开所有失败、环境阻塞、实际调用数、HTTP/token、未知费用和人工待审。只将其称为对本项目未调试样本，不保证模型训练未见过公开数据。
- [ ] 在可用Linux/Docker环境执行固定官方harness并核验receipt；本机仍不可用时明确保留待判分，不能将本地比例标成官方成绩。现阶段不新增上传密钥或自动云发布流程。
- [ ] 重写报告首段说明实际结果与是否达到已知定点回归门槛。提交 `docs: publish reproducible repaired-agent evaluation results`；不自动push或发布榜单。

## 任务依赖与完成标准

依赖：Task 1 → Task 2/3 → Task 4 → 第一批验收；Task 5/6/7 在其后收敛；全部完成后 Task 8。共享接口以各任务 Interfaces 为准，实行逐项执行。

| 门槛 | 必须满足 |
| --- | --- |
| 硬核验回归 | 错误来源、篡改哈希、被跳过测试、缺证据、语义否定继续阻止成功 |
| 协议回归 | 输出预算真正到达HTTP，错误分型可追踪，网络/协议尝试有上限 |
| 生命周期回归 | 新候选得到执行；有效verdict得到独立确认；总动作数不超过20 |
| 已知真实定点 | 新生成的Sphinx-8801完成DIFF与全新副本1/0，失败不能用诊断候选抵扣 |
| 成绩可信 | 原清单、新清单、原版重复、差分、独立交付与官方成绩分开；所有分母与失败保留 |

不承诺修复后自动达到某个成功率。已知定点恢复且新清单产出可核验结果后，再根据数据决定扩大范围、比较AgentScope策略或继续修探索逻辑。

## 后续环境专项

12个阻塞案例另分三个问题：Requests/SymPy 的历史Python/依赖环境、pytest仓库的可验证构建产物支持、Linux/Docker官方运行资源。它们可以提高覆盖率，但不应阻止先修复已经证明会挡住正确候选的核验/交付问题。
