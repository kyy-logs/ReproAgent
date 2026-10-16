# Setup Feedback and First Execution Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans in the current session. User explicitly requested plan then execution.

**Goal:** 让setup失败可修正，并为首次试跑后的修正保留步骤。
**Architecture:** Controller持有真实执行计数；Verifier投影setup事实；AgentScope middleware与progress共用预算策略，既有Runner负责真实执行。
**Tech Stack:** Python >=3.11、AgentScope 2.0.9、pytest、HTTP MockTransport；无新增依赖。
**Spec:** docs/superpowers/specs/2026-10-16-setup-feedback-and-repair-budget-design.md

## Global Constraints

- 总预算不增加：20步/900秒/单命令60秒；thinking关闭。运行期间冻结生产源码。
- 原门禁顺序、独立Verifier上下文、源码保护、精确引用和取消/费用/时限仍有效。
- 仅修改隔离工作树；不合并、不push、不重写历史轮次。
- 测试basetemp位于仓库外；复用本工作树Python并保留显式PYTHONPATH优先级。
- setup_failures最多4条、每条三个字符串字段各<=512字符；progress完整JSON<=1024 UTF8字节。

## Review Focus

- 缺文件不等于候选错误：保留观察事实，未知路径/动态fixture不能提前误拒（Task1）。
- SDK计步发生在工具权限之前，预留边界必须以实际消耗后计算（Task2）。
- 合同修订/独立重跑/fixed执行不能伪造或重置首次执行标志（Task2）。
- 秘密与Windows双反斜杠先脱敏/规范化再节选，完整证据仍可查（Task1/3）。
- 小预算、原deny、取消与deadline不能被新的修正读取权限绕过（Task2/3）。

### Task 1: Setup diagnostic projection

**Files:** Create src/reproagent/core/setup_diagnostics.py; modify core/verifier.py, core/verification_issues.py; test tests/unit/test_setup_diagnostics.py and test_verifier.py.
**Interfaces:** setup_failure_details(run, *, task_root, secrets=()) -> dict with setup_failures and omitted_setup_failures. Existing VerificationIssue/feedback carries it without another model call.

- [x] Write real-run failing test: missing fixture resource producessetup_failed exception details, references, no semantic request; legacy/no usable setup staysoldcode. Add bounds/redaction/root-format/malformed record cases.
- [x] Run tests, recordRED caused by absent projection.
- [x] Implement bounded projection and TEST_SETUP_FAILED vocabulary at existing no-executed branch; preserve all earlier checks.
- [x] Verify schema validation, feedback JSON capacity and report compatibility; commit task.

### Task 2: First execution vs repair reserve

**Files:** Modify core/exploration_progress.py, core/models.py, core/controller.py, adapters/agentscope/{middleware,progress,runtime}.py. Test unit/test_exploration_progress.py and integration/test_agentscope_runtime.py, controller feedback tests.
**Interfaces:** AgentContext.original_execution_count:int=0; Lifecycle counter; execution_budget_policy(total_steps, used_steps, original_execution_count) -> immutable policy with mode/reserve/next-reader availability.

- [ ] Write failing tests:20-step initial reserve6/repair reserve2; tiny budgets; real SDKinitial boundary vs repair boundary; wire gets controlled count; contract changesretaincount, fixed executions excluded, forgedhistoryignored.
- [ ] Run RED. Read actual before_model_call/permission ordering before modifying thresholds.
- [ ] Implement counter in Controller.observe_execution and phase envelope; shared policy used by permissions and bounded fresh system reminder. Permission code stays RESERVED_FOR_PUBLISHING.
- [ ] Update existing boundary fixtures to explicitly exercise first/repair mode; verify original deny, requiredexperiencebrowse, cancellation/deadline, livecontext capacity, step charging; commit task.

### Task 3: End-to-end repair and validation

**Files:** Create tests/integration/test_setup_repair_loop.py; add docs/reviews/2026-10-16-setup-repair-budget-acceptance.md; optional strengthen pure diagnostic validation tests.
**Interfaces:** Real Controller/Runner/Verifier/AgentScope; only HTTPmodelresponse boundary scripted, no handcrafted holdout candidate.

- [ ] RED: initial candidate hasmissingresource; feedback next wire contains controlled setup detail; targeted Readallowed in repair; second candidate supplies missing data; confirm original independentreplay and unchanged original source, steps<=20.
- [ ] GREEN: use Tasks1/2 path without specialcase fixture names or faking verdicts/execution. Check capacity/stop paths.
- [ ] Run related and full suite once production code isstable; investigate failures, record exact counts/exits.
- [ ] Fresh-context non-Astra review of whole diff; fix actionable findings with RED→GREEN checks.
- [ ] Run one new8474 after source freeze, same frozen inputs/model/budget. If local accepted candidate exists, use original official path; otherwise preserve diagnostic outcome without scoring empty patch.
- [ ] Record observed outcome, costs, limitations, sourcehash; local commit only, keep branch isolated.
