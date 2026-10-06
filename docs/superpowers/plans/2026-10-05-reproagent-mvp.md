# ReproAgent MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将本地 Python 仓库、Bug 描述和已有 pytest 环境转化为经过实际执行、干净重放并附带证据的复现包。

**Architecture:** 模块化单体，单个探索 Agent 提出动作，Controller 管理状态与统一预算。语言适配器构造 pytest 运行规格并解析 Probe，执行后端管理本地进程树；Verifier 执行硬性检查后进行语义核对，Exporter 只消费已保存证据。

**Tech Stack:** CPython、标准库 dataclasses/enum/argparse/asyncio/ctypes、HTTPX、pytest、JSON/JSONL、本地文件存储；不引入图编排框架、数据库或任务队列。

**Spec:** [项目工程架构](../specs/2026-10-05-reproagent-architecture-design.md)、[产品设计与优化路线](../specs/2026-10-05-reproagent-design.md)、[设计审查记录](../specs/2026-10-05-reproagent-review.md)。执行者先读三份资料；本计划不修改产品范围。

日期：2026-10-05。状态：当前会话实现、离线测试与独立代码审查修复已完成；最终 97 passed、1 权限限制 skip。用户选择先完成实现和离线测试，因此 Task 14 的真实模型、历史样本与人工评估保留未执行；条件 Git 提交因当前目录没有 Git 仓库而不适用。详情见 [实现记录](../../implementation-status.md)。

## Global Constraints

- Python + pytest，用户提供能够运行目标测试的环境，不自动安装或修改目标依赖。
- 第一版采用 Python CLI、单个探索 Agent、确定性 Controller、本地子进程。
- 第一版面向用户信任的项目；本地文件副本不构成进程沙箱。
- Agent 不能编辑业务代码、已有测试、conftest、项目配置或依赖声明，不能自行宣布成功。
- 最终测试断言有来源的正确行为；问题版本因目标缺陷失败，兼容修复版本若提供应通过同一测试。
- 所有候选执行与确认使用同一冻结快照的新工作副本、新进程和临时输出目录。
- 候选发布后不可变；文件、安装位置、运行参数或契约变化后，不能沿用旧成功证据。
- 所有模型调用共用预算；每次调用最多额外重试两次，重试仍计入费用与时间。
- 默认建议：20 步、60 秒命令、900 秒任务、10 秒清理；均可配置，不是性能承诺。
- 初始日志上限 32 MiB，每个工具响应文本上限 32 KiB；截断证据不能成为成功依据。
- 无兼容修复版本不能显示为差异验证已通过；两次一致观察不等于证明完全确定性或根因。
- 运行受阻自动换入口、恢复、Docker、JS/TS、多 Agent、服务化和自动修复不进入本轮。
- 当前目录不是 Git 仓库。任务中的提交只在执行目录已是 Git 仓库时进行；不因本文自动初始化仓库或创建远程项目。

## Review Focus

1. Windows 路径含中文、空格或不同分隔符：argv 和路径归属仍正确，不能误执行 shell；任务 3、5、7、13 验证。
2. JSON 重复字段、未知格式版本或非法数值：配置报具体错误，持久化记录不能静默改变含义；任务 1、2、13 验证。
3. 输出目录位于仓库内部、越界链接或排除项遗漏必要文件：不递归复制、不越界，缺失条件明确报告；任务 3 验证。
4. pytest 主进程已退出但子进程仍运行：确认清理后才允许继续，失败时保留目录并终止任务；任务 5、13 验证。
5. Probe 缺结束事件、日志到达上限或报告仅有部分节点：保留部分证据且不接受成功；任务 6、7、10、13 验证。

## 实现细化与执行约定

以下是原规格未固定、在本计划中选定的实现细节：

- 控制器 Python 最低 3.11。开发验证使用 pytest 8/9；目标环境初始验证 CPython 3.10/3.11/3.12 与 pytest 7.4/8/9，在 Windows、Linux 上只声明实际通过的组合。
- `pyproject.toml` 使用 setuptools，运行依赖 `httpx>=0.27,<1`，开发依赖 `pytest>=8,<10`；首次安装后记录实际版本，不将版本区间视作已通过兼容性验证。
- 第一个模型适配器为配置化 Chat Completions HTTP 协议，非流式、只消费文本 JSON。`base_url`、`model`、`api_key_env` 必填，模型和地址不写死；不承诺所有兼容端点有相同参数。输出限制字段由适配器配置明确选择，默认 `max_completion_tokens`，不静默去掉限制。官方请求、文本响应与 token 用量结构依据 [OpenAI API 参考](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)；通用核心不依赖该协议。
- ModelGateway、Explorer、Verifier 的语义阶段、ExecutionBackend 和 Controller 使用 async 方法；工作空间和存储为同步小操作，循环内检查取消/期限。测试用 `asyncio.run` 驱动 async 方法，不增加 pytest-asyncio。
- `schema_version=1`、`probe_version=1`。内容清单规范 JSON：UTF-8、键排序、紧凑分隔符、拒绝 NaN/Infinity；路径在清单内统一为仓库相对 POSIX 写法，实际执行使用原生绝对路径。候选 manifest_hash 对完整候选清单除 manifest_hash 自身字段外计算，包含文件字节哈希、安装位置、运行参数和契约/快照绑定，避免自包含哈希。
- 取消后不发新模型请求。正在进行的 HTTP 调用由 async 取消或总期限中断；使用 HTTPX AsyncClient 并关闭响应资源，不能只依赖每次读取超时。[HTTPX async 文档](https://www.python-httpx.org/async/)
- 收尾预算新增具体默认值 5 秒，仅保存本地记录与诊断；清理最多 10 秒。报告同时记录主任务和收尾耗时，主预算结束后不继续成功确认。
- 测试辅助构造器集中于 `tests/conftest.py` 的 `facts` fixture，返回 Factories；它只创建数据和小型本地项目，不模拟被测核心逻辑。fake model/backend 用于 Controller 边界验证，真实 pytest 和进程清理使用集成测试。每个测试步骤下的代码是同名测试中的关键断言；局部变量由该测试调用 Interfaces 中的接口或记录测试传输取得，不代表新增公共 API。
- Files 中花括号表示列出的各个准确文件名，不是执行命令。命令从项目根目录执行，开发解释器记作 `python`，目标运行始终使用用户指定的解释器绝对路径。
- 各任务先写行为测试并确认失败，再实现和确认通过；失败原因应是尚缺能力，不能是测试本身语法错误。仅重跑当前任务及受影响范围，最终一次运行完整检查。

## 文件责任地图

| 文件 | 责任 | 任务 |
| --- | --- | --- |
| `src/reproagent/core/models.py`、`serialization.py`、`ports.py` | 数据、校验、协议接口 | 1 |
| `src/reproagent/store.py` | 原子记录、事件和只读检查 | 2 |
| `src/reproagent/workspace.py` | 路径、快照、候选冻结和安装 | 3 |
| `src/reproagent/core/budget.py` | 统一预算、取消和模型调用包装 | 4、9 |
| `src/reproagent/adapters/runtimes/process_tree.py`、`local.py` | 平台进程树和本地执行 | 5 |
| `src/reproagent/adapters/languages/python_pytest/probe/reproagent_pytest_probe.py`、`collector.py` | 独立 Probe 与事件解析 | 6 |
| `src/reproagent/adapters/languages/python_pytest/adapter.py`、`src/reproagent/runner.py` | 运行规格、环境记录和执行关联 | 7 |
| `src/reproagent/core/tools.py` | 受限搜索、读取和候选写入 | 8 |
| `src/reproagent/adapters/models/provider.py` | HTTP 模型适配器 | 9 |
| `src/reproagent/core/verifier.py`、`prompts/review_evidence.md` | 规则与语义验证、重放比较 | 10 |
| `src/reproagent/core/agent.py`、`prompts/analyze_issue.md`、`explore.md` | 契约分析和下一步动作 | 11 |
| `src/reproagent/exporter.py` | 成功/诊断包和报告 | 12 |
| `src/reproagent/core/controller.py`、`app.py`、`cli.py`、`__main__.py` | 调度、组装和 CLI | 13 |
| `evals/schema.py`、`run.py`、`cases/`、`README.md` | 隔离生成/修复材料与评估 | 14 |
| `.github/workflows/test.yml`、`README.md`、`docs/compatibility.md` | 兼容验证和交付说明 | 15 |

必要包目录增加 `__init__.py`。新增 `serialization.py` 使格式校验不塞进领域对象，评估脚本不进入产品核心。此表中的产品文件目前均不存在。

## Task 1: 数据契约、序列化和可安装包

**Files:** Create `pyproject.toml`, `.gitignore`, `src/reproagent/__init__.py`, `src/reproagent/core/{__init__,models,serialization,ports}.py`, `tests/conftest.py`, `tests/unit/test_models.py`。

**Interfaces:** Produces 冻结 dataclass、枚举、`encode_record(obj) -> dict[str, JsonValue]`、`decode_record(kind: str, data: dict[str, JsonValue]) -> Record`、`canonical_hash(data: dict[str, JsonValue]) -> str`。记录类型如下，后续任务不另造同名类型：

| 类型 | 固定字段/含义 |
| --- | --- |
| BudgetLimits | agent_steps=20、command_timeout_seconds=60、task_timeout_seconds=900、cleanup_timeout_seconds=10、finalize_timeout_seconds=5、model_cost_limit=null、log_bytes=33554432、tool_response_bytes=32768 |
| PythonPytestConfig / ModelConfig | 原设计的解释器、测试参数、源码根、目标模块、测试位置；模型地址/标识/凭据变量名、输出限制字段及 token 上限、可选计费配置 |
| TaskRequest / IssueDescription / FixValidationRequest | task_id、repo、output_dir、issue_file、语言/运行标识、limits 和适配器配置；原始描述字节哈希与文本；可选修复仓库/解释器单独传给 Controller，不进入 AgentContext |
| SourceRef / EvidenceRef | 相对记录路径、内容哈希、行范围/JSON 定位；SourceRef 同时标识来源类别与版本 |
| IssueContract | contract_id、version、description_hash、trigger、expected、reported_actual、observable_checks、sources、assumptions、missing_information、revision_reason |
| FileEntry / DraftFile | FileEntry 的相对安装路径、内容哈希、长度与角色；DraftFile 还持有内容字节，仅用于发布前 |
| CodeSnapshot / ProjectView | snapshot_id、root、manifest_hash、文件清单、排除项、Git 状态；ProjectView 指向只读冻结代码与候选视图 |
| CandidateDraft / Candidate | 候选 ID/父 ID、快照 ID、契约 ID/版本、language_id、文件、selectors、run_options、hypothesis、expectation_sources、fixture_refs、preconditions；Candidate 额外保存 manifest_hash，内容引用已保存文件 |
| RunWorkspace / ProtectionCheck | run_id、root、temp_root、snapshot_id、candidate_id；保护检查包含新增/修改/删除文件与候选哈希结果 |
| LanguageInspection / ProbeResults | 静态配置事实、需要执行的探测规格、信息缺口；探测结果按规格 ID 关联 RawExecution |
| EnvironmentSnapshot | environment_id、解释器/工具版本、源码根、目标模块、前置条件、能力与限制，不含秘密值 |
| ExecutionSpec / ProbeArtifacts | spec_id、argv、cwd、资源映射、env_names、各对象 ID/哈希、execution_role=original/fixed、probe 配置；ProbeArtifacts 保存结构化事件文件路径 |
| RawExecution | spec_id、exit_code、duration、stop_reason、stdout_ref、stderr_ref、cleanup_ok、probe_artifacts、log_truncated |
| TestObservation / FrameworkChecks | collected、executed、测试与阶段状态、target_origins、failure_refs、probe_complete、framework_details；框架检查包含阻碍、无效项、可疑项与不确定项 |
| ExecutionResult | run_id、spec_id、snapshot_id、environment_id、candidate_id/manifest_hash、contract_id/version、execution_role、raw、observation、protection |
| AgentAction / ToolResult | action 的枚举名称与校验后参数；工具状态、有限文本、完整证据引用、新候选 ID（若有） |
| ModelRequest / ModelResponse | messages、response_kind、max_output_tokens；text、request_id、usage、cost_kind、cost_value、duration、finish_reason |
| CallContext / RunContext | 共享 Budget、取消事件、deadline；RunContext 另外持有运行 ID 和仅在内存使用的环境变量值 |
| EvidenceContext / AgentContext | 来源及只读记录；当前契约、快照视图、候选历史和最新反馈，不含修复材料 |
| Verdict | classification 可为 null、reason、evidence_refs、unmet_checks、uncertainties、next_action、各对象绑定 |
| TaskResult / ArtifactManifest / TaskEvent | status、stop_reason、evidence_level、accepted_candidate_id、export_state；package_kind、manifest_hash、安装清单、事件截止序号；seq、事件类型、关联对象与 payload |

TaskState 包含规格的阶段/终止状态；CandidateClass 为 ENVIRONMENT_BLOCKED/INVALID_CANDIDATE/NOT_REPRODUCED/REPRODUCED；EvidenceLevel 为 NONE/SINGLE_OBSERVATION/REPEATED_OBSERVATION/DIFFERENTIAL_VALIDATED。JsonValue 为递归 JSON 类型；Record 为上述可持久化对象的联合，不包含 CallContext/RunContext/AgentContext/EvidenceContext 或私有环境值。CallContext 的 Budget 使用类型检查引用，避免 models 与 budget 循环导入；取消事件使用 asyncio.Event。

- [x] **Step 1:** 在 `test_models.py` 写 `test_rejects_duplicate_keys_unknown_version_and_nonfinite_numbers`，断言重复字段、版本 999、NaN、负预算均产生带字段位置的 ValueError；写 `test_manifest_hash_is_stable_and_sensitive_to_install_path`，相同对象不同键顺序哈希相同，安装位置改变哈希不同。

```python
assert canonical_hash({"path": "tests/a.py", "size": 1}) == canonical_hash({"size": 1, "path": "tests/a.py"})
assert canonical_hash({"path": "tests/a.py"}) != canonical_hash({"path": "tests/nested/a.py"})
with pytest.raises(ValueError, match="schema_version"):
    decode_record("task", {"schema_version": 999})
```

- [x] **Step 2:** 运行 `python -m pytest tests/unit/test_models.py -q`，确认因缺失上述接口失败。若开发环境尚无 pytest，仅安装开发工具依赖后再运行，不能将“pytest 未安装”算作红灯验证。
- [x] **Step 3:** 实现类型与 JSON 编解码，tuple/enum/path 明确编码；保留原始描述字节哈希，未知版本拒绝解码。定义架构中的 ModelGateway/LanguageAdapter/ExecutionBackend/Explorer Protocol；其中 complete/execute/analyze/next_action 为 async。建立 facts 构造器：request、contract、draft、candidate、context、spec、execution、observation、model_response、verdict，默认关联 ID 一致，允许按字段覆盖。任务 4 前 facts.context 使用仅提供 check/deadline 的测试预算替身，任务 4 后默认使用真实 Budget，避免前置测试依赖尚未实现的模块。
- [x] **Step 4:** 建立最小 pyproject 包配置与开发 extra，运行 `python -m pip install -e '.[dev]'`，再运行本任务测试。预期 PASS；全默认值逐项断言与上表一致。包构建纳入 Probe/提示资源的配置在任务 15 验证。
- [x] **Step 5:** 若处于 Git 仓库，提交本任务明确文件：`feat: define task and evidence contracts`。

## Task 2: 原子存储与事件记录

**Files:** Create `src/reproagent/store.py`, `tests/unit/test_store.py`。

**Interfaces:** Consumes Task 1 编解码；Produces `TaskStore(root: Path)`、`save_record(kind: str, key: str, obj: Record) -> EvidenceRef`、`load_record(kind: str, key: str) -> Record`、`append_event(kind: str, refs: tuple[str, ...], payload: dict[str, JsonValue]) -> TaskEvent`、`inspect() -> dict[str, JsonValue]`。固定 collection 名称为 task/request/contracts/snapshots/environments/candidates/runs/verdicts；只允许 Controller 更新 task 状态。

- [x] **Step 1:** 写 `test_interrupted_replace_preserves_old_record`：模拟替换前写入失败，旧记录仍可读取；`test_truncated_event_tail_is_reported_without_inventing_success`：末行不完整时保留完整事件，并在 inspect 中显示记录不完整；`test_rejects_unknown_schema_and_path_escape`：拒绝版本 999 和 key=`../outside`。

```python
assert store.load_record("contracts", "contract-1") == original_contract
assert [event.seq for event in complete_events] == [0, 1]
assert inspection["incomplete_records"] is True
with pytest.raises(ValueError):
    store.save_record("contracts", "../outside", original_contract)
```

- [x] **Step 2:** 运行 `python -m pytest tests/unit/test_store.py -q`，预期上述行为尚未实现而 FAIL。
- [x] **Step 3:** 实现同目录临时文件写入和原子替换、单写者事件序号；不可变 collection 同 ID 内容冲突拒绝覆盖，先保存实体再追加引用事件。inspect 不创建/修改记录、不恢复任务、不把日志当最终状态。
- [x] **Step 4:** 运行本任务命令，预期 PASS；额外断言读损坏 JSON 给出文件位置，事件序号递增，未引用实体显示为未完成记录。
- [x] **Step 5:** 条件提交：`feat: persist versioned task records atomically`。

## Task 3: 冻结快照、候选与干净副本

**Files:** Create `src/reproagent/workspace.py`, `tests/unit/test_workspace.py`, `tests/fixtures/projects/plain/` 和 `src_layout/`；Modify `tests/conftest.py` 增加 ProjectFactory 的 `plain()`、`src_layout()`。

**Interfaces:** Consumes Store、TaskRequest、CandidateDraft；Produces `Workspace(task_root: Path, store: TaskStore)`、`freeze(request: TaskRequest, context: RunContext) -> CodeSnapshot`、`publish(draft: CandidateDraft, snapshot: CodeSnapshot) -> Candidate`、`fresh_run(snapshot: CodeSnapshot, candidate: Candidate) -> RunWorkspace`、`check(run: RunWorkspace, snapshot: CodeSnapshot, candidate: Candidate) -> ProtectionCheck`。ProjectFactory 的 `projects` fixture 创建函数 parse([]) 错抛 IndexError 的小项目及有来源的正确行为描述，供后续真实 pytest 集成使用；修复项目由 `projects.fixed()` 创建，函数返回空列表。

- [x] **Step 1:** 写 `test_nested_output_is_excluded_from_snapshot`、`test_rejects_external_symlink_and_install_escape`、`test_unicode_space_paths_and_uncommitted_files_survive`、`test_each_run_starts_clean_and_candidate_is_immutable`。断言当前代码内容保留，输出不递归，`../`/绝对安装路径拒绝，运行 A 的生成文件不出现在 B，已发布候选内容不能覆盖。

```python
assert (snapshot.root / "src" / "parser.py").read_bytes() == uncommitted_bytes
assert not (snapshot.root / "repro-output").exists()
assert not (second_run.root / "generated.txt").exists()
assert (request.repo / "tests" / "test_existing.py").read_bytes() == original_test_bytes
```

- [x] **Step 2:** 运行 `python -m pytest tests/unit/test_workspace.py -q`，预期 FAIL。链接创建受操作系统权限限制时标明能力跳过；路径穿越用例必须运行，不能一起跳过。
- [x] **Step 3:** 实现实际路径归属检查、快照复制/清单、按字节保存候选、唯一测试文件名与清单安装。处理内部链接时固定为物化快照内内容，拒绝循环和外部目标；拒绝输出位置覆盖原文件与宽泛保护例外。复制期间核对源内容稳定性，变化则重试一次后报告阻碍。
- [x] **Step 4:** 本任务测试 PASS；再验证已有源码/测试/conftest/config 修改均被 check 发现，候选文件自身改写也被发现；临时输出路径使用明确例外，不排除整个 tests。
- [x] **Step 5:** 条件提交：`feat: freeze code and immutable reproduction candidates`。

## Task 4: 统一预算和取消上下文

**Files:** Create `src/reproagent/core/budget.py`, `tests/unit/test_budget.py`。

**Interfaces:** Consumes BudgetLimits、CallContext、RunContext；Produces `Budget(limits: BudgetLimits, clock: Callable[[], float])`、只读 `steps_used: int`、`check() -> None`、`take_step() -> None`、`command_timeout() -> float`、`reserve_cost(upper_bound: Decimal | None) -> str`、`settle_cost(reservation_id: str, actual: Decimal | None) -> None`。统一 BudgetStopped 异常携带 CANCELLED/EXHAUSTED 原因；清理/收尾上下文不允许发模型请求。

- [x] **Step 1:** 写 `test_twenty_steps_then_stop`：20 次可领取，第 21 次拒绝；`test_command_uses_remaining_time`：剩余 12 秒时返回 12，不能返回默认 60；`test_unknown_cost_cannot_enable_hard_cost_limit`：配置费用上限但不能预留时拒绝；`test_cancel_blocks_new_work_but_allows_bounded_cleanup`。

```python
now = [0.0]
budget = Budget(BudgetLimits(), clock=lambda: now[0])
for _ in range(20):
    budget.take_step()
with pytest.raises(BudgetStopped):
    budget.take_step()
```

- [x] **Step 2:** 运行 `python -m pytest tests/unit/test_budget.py -q`，预期 FAIL。
- [x] **Step 3:** 用单调时钟实现期限、统一步骤/费用账本、取消事件；测试注入时钟，不进行 900 秒等待。将 facts.context 默认切为真实 Budget。费用不明记录 unknown，启用硬费用限制需要供应商可核对的计价与保守上界，不能把简单 token 估算包装成保证。
- [x] **Step 4:** 本任务测试 PASS；断言默认清理 10 秒、收尾 5 秒，两者不能增加可探索步骤；失败调用的预留不能在费用尚未确认时退回为零消耗。
- [x] **Step 5:** 条件提交：`feat: enforce shared task budgets and cancellation`。

## Task 5: 本地执行与进程树清理

**Files:** Create `src/reproagent/adapters/runtimes/{__init__,process_tree,local}.py`, `tests/integration/test_local_backend.py`, `tests/fixtures/processes/{spawn_child,emit_logs}.py`。

**Interfaces:** Consumes ExecutionSpec、RunContext；Produces `LocalBackend.execute(spec: ExecutionSpec, context: RunContext) -> RawExecution`（async）、`ManagedProcess.spawn(argv: tuple[str, ...], cwd: Path, env: dict[str, str]) -> ManagedProcess`、`poll() -> int | None`、`terminate_tree(timeout: float) -> bool`、`close() -> None`。ManagedProcess 仅供本地后端使用。

- [x] **Step 1:** 写 `test_timeout_and_cancel_reap_parent_and_child`、`test_child_is_reaped_after_parent_exits`、`test_argv_preserves_unicode_space_path_without_shell`、`test_log_limit_marks_truncated_and_stops_process`。验证 PID 真正退出、参数作为单个参数抵达子进程、指定小日志上限触发 log_truncated；不能只 mock kill 方法。

```python
assert result.cleanup_ok is True
assert parent_still_alive is False
assert child_still_alive is False
assert json.loads(captured_argument_file.read_text()) == ["中文 路径", "a;b"]
assert overflow_result.log_truncated is True
```

- [x] **Step 2:** 运行 `python -m pytest tests/integration/test_local_backend.py -q`，预期 FAIL；测试程序在 finally 中有自身兜底清理，避免红灯验证遗留进程。
- [x] **Step 3:** 实现 shell=False 的参数执行、stdout/stderr 文件流、总期限和取消。Windows 用 ctypes 调用 CreateProcessW，先以 CREATE_SUSPENDED/CREATE_NO_WINDOW 建立进程并加入 Job Object，再恢复线程；类 Unix 先建立新会话/进程组。正确关闭句柄、stdin 默认关闭，禁用脱离作业的配置；主进程退出仍检查关联子进程。
- [x] **Step 4:** 本任务集成测试 PASS；清理失败返回 cleanup_ok=False 并保留目录，不制造零退出结果。Windows、Linux 分别实际验证后才声明支持，不能用 mock 的平台路径算通过。
- [x] **Step 5:** 条件提交：`feat: run local commands with bounded process-tree cleanup`。

## Task 6: 独立 pytest Probe 与结构化采集

**Files:** Create `src/reproagent/adapters/languages/python_pytest/probe/reproagent_pytest_probe.py`, `src/reproagent/adapters/languages/python_pytest/collector.py`, `tests/integration/test_pytest_probe.py`；Modify `tests/conftest.py` 增加现有项目 fixture 场景。

**Interfaces:** Consumes LocalBackend 和 probe_version=1；Produces `read_probe(path: Path, expected_run_id: str) -> TestObservation`。Probe 从当前执行专用环境变量读取运行 ID、产物路径与需要观察的目标模块名单，不导入产品主包。

- [x] **Step 1:** 写 `test_probe_records_setup_call_teardown_skip_and_xfail`、`test_import_bug_is_observed_without_probe_importing_target`、`test_incomplete_probe_never_becomes_complete`。使用真实 pytest，断言各阶段分别记录；目标导入异常只在测试触发；删除结束事件或强杀进程后 probe_complete=False。

```python
assert {event["payload"]["when"] for event in phase_events} == {"setup", "call", "teardown"}
assert complete_observation.probe_complete is True
assert read_probe(truncated_probe_path, expected_run_id="run-1").probe_complete is False
assert premature_import_marker.exists() is False
```

- [x] **Step 2:** 运行 `python -m pytest tests/integration/test_pytest_probe.py -q`，预期缺少 Probe/collector 而 FAIL。
- [x] **Step 3:** 用公开 pytest hooks 写会话/收集/运行阶段/结束 JSONL，每条刷新；事件外层固定为 probe_version/run_id/seq/event/payload，seq 从 0 开始，阶段事件 payload.when 为 setup/call/teardown。采集已加载模块及异常栈路径，不主动导入目标；记录框架特有信息与 xfail。collector 对错 ID/版本拒绝接受，对不完整序号/节点保留完整前缀诊断且 probe_complete=False；不解析 stdout 的成功标记。[hook 参考](https://docs.pytest.org/en/stable/reference/reference.html#pytest.hookspec.pytest_runtest_logreport)
- [x] **Step 4:** 本任务集成测试 PASS；在独立目标解释器只安装 pytest 的环境运行 Probe，确认没有 ReproAgent/HTTPX 仍可采集。该安装只用于项目开发测试创建的环境，不能安装到用户目标环境。
- [x] **Step 5:** 条件提交：`feat: collect pytest evidence through a standalone probe`。

## Task 7: 语言适配器与 Runner 证据绑定

**Files:** Create `src/reproagent/adapters/languages/{__init__.py,python_pytest/__init__.py,python_pytest/adapter.py}`, `src/reproagent/runner.py`, `tests/integration/test_runner.py`。

**Interfaces:** Consumes Tasks 1–6；Produces PythonPytestAdapter 的 `inspect`、`describe_environment`、`build_execution`、`normalize`、`check_framework`（精确参数沿用架构接口）；`Runner.prepare(request: TaskRequest, snapshot: CodeSnapshot, context: RunContext) -> EnvironmentSnapshot`、`Runner.execute(candidate: Candidate, snapshot: CodeSnapshot, environment: EnvironmentSnapshot, context: RunContext, execution_role: str = "original") -> ExecutionResult`，两者 async。

- [x] **Step 1:** 写 `test_src_layout_executes_snapshot_not_installed_old_package`、`test_candidate_inherits_nested_conftest`、`test_baseline_assertion_failure_does_not_block_environment`、`test_wrong_origin_and_probe_disabled_are_not_accepted`、`test_run_hash_is_bound_to_frozen_candidate`。使用真实普通/src 项目和一个故意优先指向旧包的配置，断言检测实际来源，不只预检查。

```python
assert execution.candidate_id == candidate.candidate_id
assert execution.manifest_hash == candidate.manifest_hash
assert execution.snapshot_id == snapshot.snapshot_id
assert execution.observation.executed is True
assert all(Path(origin).is_relative_to(run.root) for origin in observed_target_paths)
```

- [x] **Step 2:** 运行 `python -m pytest tests/integration/test_runner.py -q`，预期 FAIL。
- [x] **Step 3:** inspect 返回静态事实与解释器/pytest 的受限探测规格，Runner 经后端执行后形成环境记录。候选运行使用新副本、绝对解释器、argv、独立 Probe 搜索目录和显式源码根，继承项目配置；Agent 不能覆盖插件或禁用实际执行。拒绝本轮不支持的 xdist 配置，记录限制。
- [x] **Step 4:** 本任务集成测试 PASS；ExecutionResult 从 Runner 附加契约/快照/候选绑定，不信任候选输出。缺结束记录、错误源码来源、保护文件或候选内容变化均保存诊断；运行前候选哈希不符则不启动进程。original 运行拒绝与候选原快照不一致；fixed 运行只由 Controller 授权，使用兼容修复快照并记录角色，不能成为两次 original 观察中的一次。
- [x] **Step 5:** 条件提交：`feat: execute pytest candidates against bound snapshots`。

## Task 8: Agent 的受限工具

**Files:** Create `src/reproagent/core/tools.py`, `tests/unit/test_tools.py`。

**Interfaces:** Consumes Workspace、Store、Budget；Produces `Tools.search_code(query: str, scope: str) -> ToolResult`、`read_file(path: str, start: int, end: int) -> ToolResult`、`write_candidate(draft: CandidateDraft) -> ToolResult`、`validate_action(data: dict[str, JsonValue]) -> AgentAction`。run_candidate/submit_candidate 校验后留给 Controller 分发，不在 Tools 中执行。

- [x] **Step 1:** 写 `test_read_cannot_escape_snapshot_or_access_credentials`、`test_search_and_read_respect_32768_byte_response_cap`、`test_write_creates_new_candidate_without_overwriting_original`、`test_unknown_action_and_extra_execution_arguments_are_rejected`。断言敏感排除文件不可读取，截断带原始引用且 UTF-8 可解码，写入不能覆盖现有测试。

```python
assert len(tool_result.text.encode("utf-8")) <= 32768
assert tool_result.evidence_refs
with pytest.raises(ValueError):
    validate_action({"name": "shell", "parameters": {"command": "anything"}})
assert original_test_path.read_bytes() == original_test_bytes
```

- [x] **Step 2:** 运行 `python -m pytest tests/unit/test_tools.py -q`，预期 FAIL。
- [x] **Step 3:** 实现仅搜索快照/候选、有限行范围读取和候选发布。第一版用标准库有界文本搜索，不要求安装 rg 到目标项目；不读取二进制，不建立向量索引。动作参数逐字段校验，搜索/读取过程检查任务期限，结果不能成为修改系统权限的指令。
- [x] **Step 4:** 本任务测试 PASS；断言 Tools 不持有 Controller 或后端执行回调，返回候选 ID 与清单引用；源文件保护检查仍由 Workspace/Runner 负责。
- [x] **Step 5:** 条件提交：`feat: expose bounded reproduction tools`。

## Task 9: HTTP 模型适配器与受预算控制的调用

**Files:** Create `src/reproagent/adapters/models/{__init__,provider}.py`, `tests/unit/test_model_gateway.py`；Modify `src/reproagent/core/budget.py` 增加调用包装。

**Interfaces:** Consumes ModelRequest/ModelResponse/CallContext 与 Budget；Produces `ChatCompletionGateway.complete(request: ModelRequest, context: CallContext) -> ModelResponse`、`BudgetedGateway.complete(request: ModelRequest, context: CallContext) -> ModelResponse`（async），后者是 Agent/Verifier 唯一可用入口；HTTPX transport 通过构造参数注入供离线测试使用。

- [x] **Step 1:** 写 `test_rate_limit_has_at_most_two_extra_attempts`：3 次总尝试后停止；`test_cancel_closes_inflight_request_and_sends_no_new_request`；`test_usage_missing_is_unknown_not_zero`；`test_truncated_or_invalid_provider_response_is_protocol_error`；`test_credentials_never_enter_records`。使用 MockTransport 与可取消的本地 async 测试传输，不调用付费模型；动作 JSON 校验归任务 11。

```python
assert request_count == 3
assert usage_missing_response.cost_kind == "unknown"
assert usage_missing_response.cost_value is None
assert secret_value not in json.dumps(saved_call_record)
assert calls_after_cancel == 0
```

- [x] **Step 2:** 运行 `python -m pytest tests/unit/test_model_gateway.py -q`，预期 FAIL。
- [x] **Step 3:** 实现配置化 endpoint、消息、输出上限、文本和 usage 解析、分类错误。超时/限流/部分服务错误受限重试，认证与协议不兼容不无限重试；取消和主期限覆盖整个调用及等待。凭据只在 HTTP 层读取，调用记录去掉授权头和已知秘密；不保存完整 HTTP 请求原文。
- [x] **Step 4:** 本任务测试 PASS；断言分析/探索/验证共用一个 Budget，重试不重新领取独立费用上限。首个适配器默认不声明硬费用保证：有配置费率与 usage 时报告估算，无信息为 unknown；用户启用硬费用限制而适配器无可靠上界时，在开始模型工作前明确拒绝配置，不能忽略。真实 API 冒烟只在执行阶段提供凭据及预算后进行。
- [x] **Step 5:** 条件提交：`feat: add budgeted configurable model gateway`。

## Task 10: 硬性验证、语义核对和重放比较

**Files:** Create `src/reproagent/core/verifier.py`, `src/reproagent/prompts/review_evidence.md`, `tests/unit/test_verifier.py`, `tests/integration/test_reproduction_verdict.py`。

**Interfaces:** Consumes Runner 结果、语言适配器 FrameworkChecks、Store 和 BudgetedGateway；Produces `Verifier.evaluate(contract: IssueContract, candidate: Candidate, executions: tuple[ExecutionResult, ...], context: CallContext) -> Verdict`（async）、`Verifier.confirm(contract: IssueContract, candidate: Candidate, first: ExecutionResult, second: ExecutionResult, verdicts: tuple[Verdict, Verdict]) -> bool`。confirm 只接受 original/original，并核对同候选、契约、快照、前置条件与目标现象。

- [x] **Step 1:** 写 `test_wrong_hash_origin_xfail_or_incomplete_probe_blocks_success_without_model_call`；`test_expected_bug_exception_probe_is_not_final_regression_test`；`test_unknown_fixture_unrelated_failure_and_dependency_block_are_distinct`；`test_semantic_verdict_requires_resolvable_evidence_refs`；`test_replay_compares_target_failure_not_entire_stdout`。用真实 parse 项目验证正确断言失败；反向 pytest.raises 用例不可接受。

```python
assert stale_candidate_verdict.classification == CandidateClass.INVALID_CANDIDATE
assert model_call_count == 0
assert unrelated_failure_verdict.classification != CandidateClass.REPRODUCED
assert missing_reference_verdict.classification is None
assert reverse_assertion_verdict.classification != CandidateClass.REPRODUCED
```

- [x] **Step 2:** 运行 `python -m pytest tests/unit/test_verifier.py tests/integration/test_reproduction_verdict.py -q`，预期 FAIL。
- [x] **Step 3:** 规则先行，绑定/路径/保护/完整性失败不调用模型。语义阶段使用契约、候选和有限已知值脱敏日志，核对有来源的预期、真实触发和失败位置。可疑 mock/宽泛捕获先静态提示，再结合上下文判断；不声称静态分析证明真实触发。模型无引用、错引用或不确定时 classification=null，不强行复现。
- [x] **Step 4:** 本任务测试 PASS；分别断言四个候选分类与未确定情况，原始日志打印 reproduced 不影响判定。重放前置资源不可重置时拒绝升级证据；允许日志中的时间/临时路径变化，不能允许目标异常/关键位置变化。
- [x] **Step 5:** 条件提交：`feat: verify reproduction evidence before accepting candidates`。

## Task 11: 单 Agent 分析和探索策略

**Files:** Create `src/reproagent/core/agent.py`, `src/reproagent/prompts/{analyze_issue,explore}.md`, `tests/unit/test_agent.py`。

**Interfaces:** Consumes BudgetedGateway、Tools.validate_action、AgentContext/EvidenceContext；Produces `ReproAgent.analyze(description: IssueDescription, evidence: EvidenceContext) -> IssueContract`、`ReproAgent.next_action(context: AgentContext) -> AgentAction`（async）。构造 ReproAgent 时注入共享 CallContext，方法内不能新建 Budget。

- [x] **Step 1:** 写 `test_expectation_requires_source_or_missing_information`；`test_contract_revision_requires_new_source`；`test_prompt_injection_cannot_add_shell_action`；`test_malformed_action_consumes_step_without_running_tool`；`test_agent_has_no_fixed_repo_context`。检查输出结构及拒绝情况，不以精确提示文本匹配证明模型能力。

```python
assert contract.sources or contract.missing_information
assert tool_call_count == 0
assert context.budget.steps_used == 1
assert str(fixed_repo) not in json.dumps(model_messages)
assert fix_patch_text not in json.dumps(model_messages)
```

- [x] **Step 2:** 运行 `python -m pytest tests/unit/test_agent.py -q`，预期 FAIL。
- [x] **Step 3:** 实现三类消息：规则与工具说明、契约与引用资料、历史反馈；仓库/Issue/日志标成数据。next_action 每轮进入时通过共享 Budget.take_step 计一步，包括格式错误，Controller 不重复扣步；analyze 只扣模型用量/时间。模型每轮返回一个结构化动作，不并行执行多个动作；超长或不完整输出反馈为格式错误。缺可靠预期产生 missing_information；改变契约的来源由 Controller 校验并发布，Agent 无文件覆盖权限。
- [x] **Step 4:** 本任务测试 PASS；fake 模型序列能提出读取→新候选→执行→提交，但单 Agent 本身不能触发子进程、标记 DONE 或修改状态。重复尝试仍受全局步骤预算，不为本轮增加多候选搜索或恢复功能。
- [x] **Step 5:** 条件提交：`feat: explore bug reproduction with a single bounded agent`。

## Task 12: 可重跑的成功包和诊断包

**Files:** Create `src/reproagent/exporter.py`, `tests/integration/test_exporter.py`。

**Interfaces:** Consumes 冻结 Candidate、TaskResult、Store、环境和运行记录；Produces `Exporter.export(result: TaskResult, store: TaskStore) -> ArtifactManifest`。输入任务记录在导出期间只读；Controller 在返回后写最终状态/事件。包类型明确为 reproduction 或 diagnostic。

- [x] **Step 1:** 写 `test_exported_candidate_replays_from_declared_install_paths`、`test_export_failure_never_publishes_success_package`、`test_manifest_does_not_hash_itself_or_final_task_event`、`test_redacted_logs_keep_explicit_source_to_export_reference_mapping`。将包安装到全新项目副本，使用报告命令执行真实 pytest；缺失辅助文件和临时任务路径依赖必须失败。

```python
assert installed_test.read_bytes() == frozen_test_bytes
assert replay_observation.probe_complete is True
assert "manifest.json" not in manifest_file_paths
assert internal_task_path not in report_text
assert secret_value not in exported_log_text
```

- [x] **Step 2:** 运行 `python -m pytest tests/integration/test_exporter.py -q`，预期 FAIL。
- [x] **Step 3:** 从记录渲染报告、按候选清单逐字节复制测试与数据、保存命令/节点/源码根/fixture 哈希/前置条件和证据级别。原始日志引用与脱敏副本存在变化时，清单保存明确映射和两份内容哈希，不能让脱敏日志冒充原始字节。候选文件含已知秘密时拒绝成功导出并给出诊断，不能自动改写冻结测试后冒用旧证据。
- [x] **Step 4:** 本任务集成测试 PASS；临时目录形成、检查全部文件与清单后再发布。manifest 不含自身哈希，报告记录事件截止序号，最终 export.completed 在外部任务目录引用清单哈希。诊断包保留未验证标识、不写成功说明；Exporter 不使用模型接口。
- [x] **Step 5:** 条件提交：`feat: export auditable reproduction and diagnostic packages`。

## Task 13: Controller、App 与 CLI 完整闭环

**Files:** Create `src/reproagent/core/controller.py`, `src/reproagent/{app,cli,__main__}.py`, `tests/unit/test_controller.py`, `tests/integration/test_cli.py`, `examples/task.json`, `examples/model.json`。

**Interfaces:** Consumes Tasks 1–12；Produces `Controller.run(request: TaskRequest, context: RunContext, fixed: FixValidationRequest | None = None) -> TaskResult`（async）、`create_controller(request: TaskRequest, model: ModelConfig) -> Controller`、`cli.main(argv: list[str] | None = None) -> int`。CLI 为 `reproagent run --config <task.json> --model-config <model.json> [--fixed-repo <path> --fixed-python <path>]`、`reproagent inspect <task-dir>`。命令/界面不提供 resume。

- [x] **Step 1:** 写 `test_controller_requires_replay_export_and_cleanup_before_done`、`test_budget_after_first_observation_preserves_partial_evidence`、`test_cleanup_failure_overrides_cancel_with_original_reason_saved`、`test_missing_information_and_environment_block_have_diagnostic_packages`、`test_fixed_validation_is_separate_and_same_candidate`。另写 CLI 的重复字段配置、中文路径、inspect 只读和 Ctrl+C 用例。

```python
assert partial_result.status == TaskState.EXHAUSTED
assert partial_result.evidence_level == EvidenceLevel.SINGLE_OBSERVATION
assert cleanup_failed_result.status == TaskState.FAILED
assert cleanup_failed_result.stop_reason == "CANCELLED"
assert success_result.status == TaskState.DONE
assert success_result.export_state == "published"
```

- [x] **Step 2:** 运行 `python -m pytest tests/unit/test_controller.py tests/integration/test_cli.py -q`，预期 FAIL。集成闭环使用可控模型响应与真实 Runner/pytest，不能全部替换成 fake 后端。
- [x] **Step 3:** 实现状态转换和动作分发；run_candidate 调用 Runner→Verifier，submit_candidate 校验已有结果后才重放。当前契约实质变更时发布新版本并使旧候选判定失效。CLI 参数错误退出 2；DONE=0、BLOCKED/NEEDS_INFORMATION/EXHAUSTED=1、FAILED=3、CANCELLED=130。成功前预算耗尽保留证据，最多 10 秒清理、5 秒本地收尾；状态持久化失败不能打印已成功。
- [x] **Step 4:** 本任务测试 PASS。固定版材料只由 Controller 在生成冻结后使用；同一候选在独立 fixed 快照/用户提供环境运行，fixed 测试通过且绑定/保护条件满足才升级为 DIFFERENTIAL_VALIDATED。API/fixture 不兼容或修复版仍失败时记录未通过和限制，不改测试、不冒充差异成功。若用户要求的验证因预算未完成，保留原观察并 EXHAUSTED。测试检查模型从未看到修复路径、代码或新增修复测试。
- [x] **Step 5:** 条件提交：`feat: ship local reproduction CLI and task lifecycle`。

## Task 14: 隔离修复信息的评估工具与真实样本

**Files:** Create `evals/{__init__,schema,run}.py`, `evals/README.md`, `evals/cases/`, `tests/unit/test_eval_runner.py`。Modify `tests/conftest.py` 复用测试 Bug/修复项目，不将其计为真实历史样本。

**Interfaces:** Consumes CLI/App、冻结产物与任务记录；Produces `EvalCase`、`run_case(case: EvalCase, model: ModelConfig, output_dir: Path) -> EvalResult`（async）、`summarize(results: tuple[EvalResult, ...]) -> dict[str, JsonValue]`。EvalCase 字段固定为 case_id、Issue 来源/哈希、buggy/fixed 仓库与版本、两套解释器/前置步骤、允许描述、生成时禁止暴露材料、评审状态；EvalResult 保存终止/证据级别、环境状态、复现/重跑/差异结论、人工判断、时间、费用种类和缺失值。summary 固定包含 all_tasks、runnable_tasks、effective_reproductions、total_rate、runnable_rate、false_positives、export_replay_rate、costs_known_count、costs_unknown_count、cost_samples、time_samples；分母为 0 的率保存 null。

- [x] **Step 1:** 写 `test_eval_input_excludes_fix_and_new_regression_tests`、`test_blocked_cases_remain_in_total_denominator`、`test_unknown_cost_is_not_counted_as_zero`、`test_same_model_environment_and_budget_are_recorded_for_comparison`。构造一个成功、一个受阻、一个误报、一个费用未知样本，断言总分母为 4，可运行分母单独报告。

```python
assert summary["all_tasks"] == 4
assert summary["runnable_tasks"] == 3
assert summary["costs_unknown_count"] == 1
assert "fixed_repo" not in serialized_agent_input
assert new_regression_test_text not in serialized_agent_input
```

- [x] **Step 2:** 运行 `python -m pytest tests/unit/test_eval_runner.py -q`，预期 FAIL。
- [x] **Step 3:** 实现只将问题版本与允许描述传给 Agent；固定候选后在另一份修复工作副本运行，按清单记录人工判定。summary 区分整体有效复现率、可运行复现率、误报、导出重跑、人类补充次数和延迟/费用分布；unknown 保留缺失值，不与真实 0 混淆。
- [ ] **Step 4:** 单元测试 PASS。整理约 20 个有公开 Issue/修复提交的历史 Python Bug，每个保存验证过的版本、可运行命令和来源；先跑 3 个作为评估工具冒烟，准备好环境/模型预算后跑完整集。模型生成时隐藏修复补丁和新增回归测试，禁止用原任务临时目录完成导出重跑检查。样本暂不能运行也保留并计为受阻，不以删除样本提高指标。
- [ ] **Step 5:** 保存人工审查表和实际结果；比较通用编码 Agent 时使用相同描述、模型、环境和预算，比较尚未运行时写未评估，不编数字。条件提交：`feat: evaluate reproduction without leaking fixes`。

## Task 15: 平台兼容验证、打包和使用说明

**Files:** Create `.github/workflows/test.yml`, `docs/compatibility.md`, `README.md`, `tests/integration/test_installed_package.py`；Modify `pyproject.toml`, `examples/task.json`, `examples/model.json`。

**Interfaces:** Consumes 完整 CLI 与测试/评估结果；Produces 安装后可用的 Probe/提示资源、实际通过的支持矩阵和完整的本地用法。CI 不运行付费模型，不使用用户凭据，也不自动发布包。

- [x] **Step 1:** 写 `test_installed_wheel_can_run_probe_and_load_prompts_without_source_checkout`：构建 wheel、安装到独立工具环境，在另一份仅有 pytest 的目标环境完整执行测试；断言不依赖 src 目录、相对开发路径或用户安装 ReproAgent。

```python
assert installed_probe_observation.probe_complete is True
assert source_checkout_path not in installed_probe_command
assert loaded_analysis_prompt == source_analysis_prompt
assert target_environment_has_reproagent is False
```

- [x] **Step 2:** 运行 `python -m pytest tests/integration/test_installed_package.py -q`，若资源未打包应 FAIL；缺 pytest/pip 的开发测试环境先准备，不把工具缺失作为目标行为红灯。
- [x] **Step 3:** 使用 importlib.resources 提供 Probe 与提示；构建通过 `python -m pip wheel . --no-deps --wheel-dir <临时目录>`。配置 Windows/Linux 的本地后端检查与目标 CPython 3.10/3.11/3.12 × pytest 7.4/8/9 验证任务；先按基础组合冒烟，再补其余组合，文档只标已通过的组合。
- [x] **Step 4:** 运行 `python -m pytest tests/unit tests/integration -q` 与 `python -m pip check`，预期全部必需行为通过、依赖无冲突；跨平台任务未运行则支持矩阵标未验证，不能宣布全部平台兼容。README 给出配置、run/inspect、成功/受阻示例、导出安装位置、预算、文件隔离限制和已知覆盖范围，真实能力只引用任务 14 的实际数据。
- [x] **Step 5:** 条件提交：`docs: document validated compatibility and local usage`。若已经有 Git 仓库，检查仅包含授权文件，完成最终变更审查；本计划不包含发布、推送、PR 或部署步骤。

## 里程碑与完成标准

| 里程碑 | 任务 | 必须能展示的结果 |
| --- | --- | --- |
| M1 执行与证据基础 | 1–8 | 手动给定候选，经真实 pytest 在正确快照运行，证据和进程清理可检查 |
| M2 完整 Agent 闭环 | 9–13 | 可控模型与真实执行完成探索→验证→干净重放→导出；受阻/耗尽/取消正确结束 |
| M3 实际能力与交付 | 14–15 | 真实样本审查、独立导出重跑、实际支持矩阵和可安装工具 |

任务按编号顺序执行。15 个任务属于同一个可交付 CLI 项目，评估工具检验其真实能力，不另建产品子系统。没有设未经测量的复现率门槛，也没有承诺高 Star。

## 规格覆盖与计划自审

| 规格要求 | 对应任务 |
| --- | --- |
| 变化接口与模块职责 | 1、5、7、9、11、13 |
| 契约来源、不可变候选与版本绑定 | 1–3、7、10、11、13 |
| 文件保护、源码来源、fixture 与干净运行 | 3、6、7、10、12 |
| 预算、取消、日志和进程树 | 4–7、9、13 |
| 候选分类、语义引用、重放与差异验证 | 7、10、13、14 |
| 原子存储、部分证据、成功/诊断导出 | 2、12、13 |
| CLI、安装资源与支持矩阵 | 13、15 |
| 实际评估与避免修复信息泄漏 | 11、13、14 |
| 后续优化与本轮边界 | Global Constraints；不建立提前占位实现 |

文档自审覆盖了规格映射、跨任务类型/签名、五项 Review Focus 的测试归属，以及后续功能未进入 MVP。上述命令均为将来实施阶段执行，当前没有测试结果或复现率可报告。

## 执行交接

请用户先评审本计划，再选择执行方式：

- **原会话执行：** 同一实现者逐项开发，最后统一独立审查。推荐；这些任务共享数据、预算和证据接口，连续执行容易保持一致，也较省上下文成本。
- **子智能体逐项执行：** 每个任务由新的实现者执行并由独立审查者检查，再进入下一项。任务间审查更充分，调用与上下文成本更高。

此前用户授权的子智能体用于设计审查，不自动视作选择本轮实施方式。计划评审和执行方式确认后，使用对应执行技能；本轮只交付计划。
