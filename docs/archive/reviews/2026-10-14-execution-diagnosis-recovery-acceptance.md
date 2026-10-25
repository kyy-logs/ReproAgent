# 执行诊断与重复超时停止：验收记录

> **历史归档（2026-10-24）**：保存当时的设计、运行结果或验收依据，不能据此判断当前默认行为、最新测试数量或总体复现率。
> 当前入口见[文档导航](../../README.md)，旧路径对照见[归档索引](../README.md)。

日期：2026-10-14。基线：`b91f22baf8f9df0778e5926c783d723c4f9d4d06`。实现分支：`codex/execution-diagnosis-recovery`。

## 实现范围

- 探针按文件名缓存来源解析，上限 4096；每次目标调用仍计数，每次 pytest 会话清缓存，结束时模块来源重新解析。
- Verifier 在语义模型之前返回 ExecutionDiagnostic、具体 unmet_checks；未完整执行不再冒充候选内容无效，未执行的保护检查不冒充源码被修改。
- Controller 根据实际 Runner 返回立即更新连续超时计数；同一快照/环境/角色连续两次命令超时后 BLOCKED。读文件、换候选或修订契约不清零；实际非超时执行会清零，即使后续语义验收报错。
- 独立重跑未完成不能确认重复观察。fixed 执行未完成为 blocked；完整执行后的真实源码保护失败仍为 failed；两者保留原版已确认的证据。
- report.json/report.md 和 trace/event 保留具体执行阻碍、耗时、最后已观测阶段、检查失败项、关联运行和计数。
- 真实重放额外发现并修复包内候选的来源误判：仅在当前运行目录、发布清单和内容哈希全部匹配时，将候选来源及对应调用与原版来源分开。candidate_target_origins 保留事实；候选自身不能成为原版来源证据。未知、改动、外部来源仍拒绝。

未改探索阶段约束、20 步/900 秒/60 秒/32 KiB 原预算、经验召回、模型选择或安全验收标准；不引入诊断模型。

## 红绿验证和回归

| 检查 | 结果 |
| --- | --- |
| 缓存/探针/Runner/保护第一轮 | 32 passed（32.97s） |
| 执行诊断与 Verifier | 24 passed（24.78s） |
| Controller 和真实 AgentScope 后端 | 48 passed（141.91s） |
| 报告/诊断导出/观测生命周期 | 27 passed（23.30s） |
| 新候选来源分离 + Runner/探针/comparison | 修前 2 failed、2 passed；修后 19 passed（16.14s） |
| 既有 review/protection 回归 | 17 passed（23.47s） |
| 审查补充的模型/ValueError 重置和 fixed 真实保护失败 | 修前 3 failed；修后恢复策略完整 12 passed（37.49s） |
| 最终完整离线测试 | 945 passed、6 skipped、1 处旧断言失败（752.78s）；仅更新该断言后，下列 35 项相关回归全通过 |
| 旧断言更新后的审阅/执行诊断/Verifier 回归 | 35 passed（32.74s），exit 0 |
| 仓库外 SWT 对照评测 | 10 passed（32.15s） |
| Windows 长路径定向复核 | 23 passed、2 skipped（23.48s） |
| 合并到 E:/ReproAgent 后关键回归 | 52 passed（76.43s），exit 0 |
| 补丁格式 | git diff --check 通过（最终提交前再次确认） |

定向测试均使用本地解释器 `.venv/Scripts/python.exe`，早期定向检查曾使用 `.local` 专用目录；最终完整测试改用两个仓库之外的系统临时目录，不写经验默认库。最终完整测试命令：

```powershell
.venv/Scripts/python.exe -u -m pytest -q --basetemp C:/Users/Dell/AppData/Local/Temp/reproagent-execution-recovery-final-20261014
```

实施期间两次较早的全量运行因真实重放/独立审查发现需修复的边界而中止。另一次把 basetemp 放在 E:/ReproAgent/.local 下，SWT 子 pytest 自动选择父仓库 rootdir，使 receipt 的 nodeid 带 .local 前缀，导致 7 项对照匹配失败；原 stdout 证明测试实际执行。这是测试环境配置错误，未更改产品来适配；改用仓库外系统临时目录验证。所有中止运行均不计为通过，最终完整测试结果单列于上表。

该次完整运行唯一失败为 `test_real_collection_error_aborts_before_any_review`：仍要求探针不完整归类 INVALID_CANDIDATE，与本次批准的诊断契约不一致。仅将此测试更新为精确检查 classification=None、PROBE_INCOMPLETE、unmet_checks=(probe_complete,)、next_action=revise_candidate_or_request_information，继续断言语义模型未调用；无产品代码改动。随后 `tests/unit/test_review_context.py tests/unit/test_execution_diagnostics.py tests/unit/test_verifier.py` 共 35 项通过。没有再重复跑整套；不将两条命令表述成一次“946 passed”的全套运行。当前无已知未解决失败。

## SymPy 15678：真实 Runner 离线重放

读取原任务 `E:/ReproAgent/repro-results/batch-pre-sympy-15678` 的冻结快照和 `candidate-1ee8a3a00cc549d5a7f86e7c59080bf2`，校验候选内容哈希后发布到全新输出。使用原 Python 3.9 SymPy 环境，经产品 Workspace/Runner 执行；保留探针、字节码禁写、来源检查、保护检查和 60 秒命令上限。

| 事实 | 结果 |
| --- | --- |
| 新输出 | `E:/ReproAgent/repro-results/sympy-15678-execution-repair-14590adb` |
| 运行 | `run-91eda9e951ec4facb2402c903ebd1c8e` |
| stop_reason / exit_code | EXITED / 1（pytest 用例失败） |
| 命令耗时 | 8.125 秒 |
| cleanup_ok / probe_complete / executed | true / true / true |
| protection.ok / source_binding_ok | true / true |
| 原版目标调用次数（剔除候选模块） | 597503 |
| 观测异常类型 | builtins.IndexError、builtins.ValueError |

原记录七次执行均约 60 秒超时。中间一次修复缓存后的真实重放已在 9.094 秒退出，但来源绑定失败，推动了上述包内候选来源修复；不把该中间记录计为验收通过。

最终原始证据保留在新输出的 `runs/run-91eda9e951ec4facb2402c903ebd1c8e/execution.json`、probe.jsonl、stdout.log、stderr.log、spec.json。

这次验证了执行障碍及来源边界修复，未进行真实模型语义审阅、独立重复确认或 fixed differential；不能宣称此案例最终 DONE，也不据此对全部七个旧候选做语义结论。

## 独立审查

非 Astra 审查者对完整实现及新增 Runner 来源分离做只读审查，发现两项 Important：

1. 连续超时清零原本在 Verifier 成功后，模型/证据错误可使正常执行未清零。已前移到实际执行返回处，补 ModelOutputError/ValueError 两分支回归。
2. fixed 实际源码保护失败原本被归为 incomplete/blocked。已恢复 failed，并保留 SOURCE_PROTECTION_FAILED 的明确诊断，补真实运行后修改文件的回归。

两项修复经原审查者只读复核确认解决；未发现 Critical。审查者未运行测试，测试证据由实施者运行得到。新增 Runner 白名单/根目录/哈希三重约束、候选调用分离及原版非空要求未发现新增放宽验收路径。

## 本地提交与边界

- c10c177：批准的设计和实现计划。
- 8c8e22f：探针来源缓存。
- a01c8e6：结构化执行诊断。
- 3091260：连续超时停止与明确反馈。
- 99c2b6e：报告和 trace 的执行阻碍。
- 29f54a8：包内候选来源分离与边界测试。
- ee15a58：审查发现的两项恢复边界修复。
- e698b08：唯一旧测试断言对齐新的不完整执行诊断契约。

无真实模型提供商请求、无新增模型 token 费用、未使用 Astra。实现分支已按用户指令快进合并到 `E:/ReproAgent` 的本地 main；未 push。用户本地评测 HTML 的 SHA-256 在合并前后一致。两份初版计划在替换为最终版前已备份到 `.local/merge-backups/execution-recovery-20261014-093651/`；实施日志与离线重放脚本保存在 `.local/execution-recovery-audit-20261014/`。

合并后命令：`.venv/Scripts/python.exe -m pytest -q tests/unit/test_review_context.py tests/unit/test_execution_diagnostics.py tests/unit/test_verifier.py tests/unit/test_controller_execution_recovery.py tests/integration/test_runner_candidate_origins.py tests/integration/test_execution_diagnostic_reporting.py --basetemp C:/Users/Dell/AppData/Local/Temp/reproagent-merge-recovery-20261014`。解释器确认加载 `E:/ReproAgent/src/reproagent/runner.py`。
