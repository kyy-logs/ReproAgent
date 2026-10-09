# 双层可观测性 验收回执

日期：2026-10-09。分支 `feat/minimal-observability`（已 rebase 到 `main`）。本文只记录**实测**结果，
不写印象分、不用测试总数代替逐项结论。

## 范围合同

**改**：新增 `observability_content.py`、`trace_rendering.py`；扩展 `observability.py`、
`adapters/agentscope/{observability,middleware,model_factory,runtime}.py`、
`core/{controller,verifier}.py`、`runner.py`、`adapters/runtimes/local.py`、`experience.py`、
`cli.py`、`resources/trace.html.template`、文档与测试。

**不改**：模型配置/temperature/thinking/输出限额、system prompt、工具注册与输入输出、权限与最后三步
预留、业务状态推进、预算、EvidenceLedger 规则、TaskStore 领域事件格式、Verifier 接受标准、学习来源
与时序、manifest/replay、官方评分。

**与计划的偏差（已在计划中记录）**：基线由 `76d4f95` 更正为 `278903f`；`span()` 恢复 `expected=`
（否则阶段正常收尾会被记成失败，已在真实 DeepSeek 轮次观察到）；HTML 上限 32MiB → 8MiB。

## 环境

Windows 11，CPython 3.12.14，pytest 9.1.1，AgentScope 2.0.9。本 worktree 自建 `.venv`（editable
指向本 worktree 的 `src`）——共享 venv 的 `.pth` 指向 main 的 `src`，缺了它 worktree 内测试全是假绿。

**`REPROAGENT_RG_PATH` 无法有效设置**：本机 `rg` 只是包装 `claude.exe` 的 shell 函数，
`shutil.which()` 解析不到真实可执行文件。4 个 rg 相关用例保持基线跳过，如实记为**环境缺口**，
不声称已设置。

## 验收矩阵

| ID | 必须检查 | 状态 | 实测证据 |
| --- | --- | --- | --- |
| O01 | 普通 run 默认采集并自动写两文件；`--no-trace` 不读正文/不建文件；两者不改调用 | **pass** | `test_a_normal_run_writes_the_json_and_the_page_without_a_flag`、`test_no_trace_writes_no_observability_file`；默认产出 `trace.json`+`trace.html`，`--no-trace` 产出为空 |
| O02 | wire 输入/输出、工具参数与结果可审查；坏输出、缺思考、截断诚实；不猜理由 | **pass** | `test_the_real_wire_request_is_captured`（第二次请求含首次工具结果）、`test_a_rejected_response_is_captured_before_it_is_refused`、`test_reasoning_is_kept_when_returned_and_marked_when_absent`、`test_the_tool_input_and_result_are_captured_and_correlated` |
| O03 | HTTP 重试不双计；权限三态分列；检查与执行同 key；真实六/七条件 | **pass** | `test_three_http_attempts_share_one_logical_call`、`test_permission_and_execution_are_joined_by_call`、`test_reserved_denial_is_distinct_from_plain_denial`、`test_the_trace_records_the_budget_and_the_registered_tool_set`（实测 7 工具含 `read_experience`） |
| O04 | 契约版本/候选/verdict 关联；检查短路不变、未走到不标 pass；模型声明与程序接受分开 | **pass** | `test_a_real_run_shows_which_checks_ran_and_the_process_health`、`test_a_model_claiming_reproduction_is_not_believed_over_the_program`、`test_record_check_returns_the_value_and_records_the_outcome`（`provider_semantic_claims=provider_claim`，`binding_and_integrity_ok=program_check`） |
| O05 | 两种模式状态/证据/候选/调用/预算等价；学习在封存后且预算独立 | **pass** | `test_a_captured_run_reaches_the_same_result_as_an_uncaptured_one`（状态/证据/候选字节/请求种类与条数全等）、`test_main_and_learning_have_separate_budget_metrics` |
| O06 | 只复用真实 probe/运行/清理；超时取消可见；候选退出 1 不伪报环境坏 | **pass** | `test_process_health_uses_only_the_runs_own_signals`（8 组参数化）、`test_a_candidates_failing_tests_are_not_a_broken_environment`、真实运行中 `process.health` 全为 `passed` 而候选退出码为 1 |
| O07 | UTF-8 序列化大小、副本/脱敏、partial 分层；错误不影响主结果；不进入模型/证据/学习 | **pass** | `tests/unit/test_observability_content.py`（15 条，含 512/4MiB/32KiB 边界与 `capture_error`）、`test_trace_not_in_manifest_or_learning_material` |
| O08 | 普通 run 只增两个观测文件；HTML 失败保留 JSON/主结果；HTML 纯文本、refs 可核对；长路径/链接/读限额/模板 wheel | **pass** | `tests/unit/test_trace_rendering.py`（24 条，含 junction 拒绝、读限额、注入转义）、`test_a_failed_json_write_skips_the_page_and_keeps_the_result`、`test_windows_long_paths.py` 全过、wheel 实测含三个新件 |
| O09 | 完整 unit/integration 与 pip check 有实测日志；跳过/平台缺口单列 | **pass** | 审查修复**后**重跑：`pytest tests/unit -q` → **384 passed, 3 skipped**；`pytest tests/integration -q` → **279 passed, 6 skipped**；`pip check` → No broken requirements。（修正前为 382/278，差的 3 条是本次新增的回归测试。） |
| O10 | 命令/结果/证据/未过修复文件和符号齐全；审查与取舍可读 | **pass** | 本文件；全分支独立审查结果见下节 |

### O09 的 9 个跳过（逐项原因）

| 数量 | 原因 |
| --- | --- |
| 4 | 本机无可执行 `rg`（`test_agentscope_domain_tools`、`test_agentscope_snapshot_tools`）——环境缺口 |
| 3 | Windows 符号链接/链接创建权限（`test_experience_store`、`test_paths`、`test_workspace`） |
| 2 | Windows 链接创建权限与"解释器未经链接到达"（`test_windows_long_paths`） |

## 未验证（明确另列，不用总体通过率抵消）

- **真实模型能力/性能比较：not_run。** 本轮的真实模型证据只有早前那次端到端跑通（Task 2 之前），
  采集功能本身的真实模型轮次**未重跑**。前端全部为 `httpx.MockTransport`。
- **`REPROAGENT_RG_PATH`：环境缺口**（见上）。
- **官方 Docker harness：not_run**（本次改动不涉及评分链路）。

## 独立审查

一次全分支独立审查（`/code-review main...HEAD`）报出 10 条，**逐条复现后全部成立**，无一误报。
两条是本次自己引入的**产品级缺陷**：

| # | 缺陷 | 复现 | 处置 |
| --- | --- | --- | --- |
| 1 | 正文去重只保留首个 owner，第二个引用同一 content 的 span 被判"引用他人内容"，`validate_trace` 拒绝——**`reproagent trace` 在运行自己刚写的 trace 时报错退出 2** | 已复现（两次 `provider_reasoning/not_returned` 即触发） | 去掉 owner 匹配规则（去重本身就是"一个 id 多个引用"）；保留"引用必须能解析" |
| 2 | span 上限在**关闭时**丢弃，而父子关系在**创建时**授予；长寿父 span（`task`/`explore`）后关闭被丢，子 span 全留在文件里成悬空 parent——**写出者产出自己的读者拒绝的文档** | 已复现（`MAX_SPANS=3`） | 达到上限时若某 span 是**已保留 span 的父**则必须保留；并释放被丢弃 span 的登记（同时修掉内存无界） |
| 3 | `expected=(PhaseEnded,)` 只覆盖阶段正常收尾；`BudgetStopped`（EXHAUSTED）同样结束一轮却被记成 `error` | 已复现（`agent_steps=1`） | `expected` 加入 `BudgetStopped`，按其 `reason` 标注 |
| 4 | `write_trace` 用**副本**标记超限，调用方手里的 document 未被标记，页面因此声称完整而旁边的 JSON 说不完整 | 已复现（1.6MB 文档） | 改为**就地标记**，保证"同一份 document"为真 |
| 5 | `validate_trace` 加固不完整：祖父缺失 → `KeyError`；非 dict 的 `contents`/`usage` → `AttributeError`；深层 JSON → `RecursionError` 逃出 `cli.main` | 已复现 | 补齐形状校验与防御式上行；`read_trace` 捕获 `RecursionError` |
| 6 | 限额与文档不一致（文档 6/8MiB，代码 1/4/8MiB） | 已核对 | 统一为 文档=代码：单文件 6MiB、HTML 8MiB、读取 6MiB |
| 7 | `record_check('framework_blocked', ...)` 名字与语义相反：PASS 表示**被阻塞** | 已核对 | 改名 `framework_not_blocked` 并传正向值 |
| 8 | `PROCESS_HEALTH` 缺 `EXHAUSTED`，最常见的预算停止报 `unknown` | 已核对 | 补 `EXHAUSTED → blocked` |
| 9 | `--no-trace` 下 request hook 仍**逐次解码并解析整个请求体**，与"不读正文"的承诺矛盾 | 已核对 | 加 `tracing_enabled()` 短路；runtime 的 `exploration.tools` 同样短路 |
| 10 | `_entries`/`_span_ids` 从不回收，span 上限只约束文件不约束内存 | 已核对 | 丢弃 span 时释放登记 |

另修复从上限裁掉的低severity项：`_response_hook` 与模块头**过时 docstring**（仍称"不保留任何内容"）、
工具 span 名用了**未脱敏的模型可控原名**、`SECTIONS` 悬空且**第三个"决策"区域从未渲染**、
`read_trace` 未用仓库加固过的 `parse_json`。

**取舍记录**：审查建议把 `owner_span_id` 改成多值列表；我选择保留单值（首个捕获者）并删除该条校验——
去重的语义就是"一个 id 被多个 span 引用"，把校验加在去重之上本身就是矛盾，改数据结构解决的是症状。

修复全部按 RED→GREEN 进行（每条都有对应失败测试），修复后 `tests/unit` 与 `tests/integration` 重跑通过。

