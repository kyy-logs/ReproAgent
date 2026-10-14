# 执行诊断与重复超时停止设计

## 目标与证据

目标是让 Agent 区分候选内容问题与执行证据未完成，并在执行障碍持续时停止无效重写。不是通过放宽验收、关闭探针或增加模型步骤来得到 DONE。

sympy-15678 的七个 run 均在约 60 秒 COMMAND_TIMEOUT，探针停在第一个测试 setup=passed，未返回 call 报告。Verifier 的合成完整性理由进入下一阶段，但没有具体原因；next_action=explore 导致持续重写。不能据 executed=False 推断测试完全没有开始。

同一个候选的离线 pytest 对照：关闭探针 2.277 秒返回两条 Issue 对应异常；现有探针 20 秒超时；只在诊断进程缓存来源解析，保留探针后 5.851 秒返回同样异常。结果与原始日志分别在 `.local/sympy-timeout-diagnosis/results.json` 和 `repro-results/batch-pre-sympy-15678/`。这证明来源解析是明显的性能因素，不证明全部七个候选语义正确，也不等于通过完整验收。

## 修改边界

第一版做三件事：探针按执行会话缓存重复文件名解析；Verifier 返回具体且结构化的执行诊断；Controller 限制同环境下连续命令超时。

不改 20 步、900 秒、60 秒命令预算或 32 KiB 上下文配置；不关闭 profiling、来源核验、源码保护或独立重跑；不引入额外诊断模型，不扩展经验召回，也不建立完整沙箱系统调用追踪。

## 探针缓存

缓存以 co_filename 为键，最多 4096 条，作用域为当前 pytest 会话，在 pytest_sessionstart 清空。虚拟文件名和 OSError 继续不能成为来源证据；缓存的空结果不能计作有效来源。

每次匹配目标模块的 call 仍增加原计数。缓存只减少重复解析，不采样或跳过调用。pytest_sessionfinish 的模块来源解析继续 fresh，不使用缓存结果；Runner 的保护和原版哈希核验继续独立执行。源码被改动、来源切到仓库外等情况不能因缓存通过验收。

## 重放发现的候选来源边界

真实 Runner 重放还证明：当 target_modules 使用整个包（sympy）、候选位于包内测试目录时，探针会正确观察到候选模块，但 Runner 旧逻辑把它当成原版保护文件，导致来源绑定误拒绝。

仅对位于当前 run 内、路径属于发布清单且当前内容哈希匹配的候选来源，单独保留 candidate_target_origins，并从原版 target_origins 与 target_calls 中剔除。仍须至少一个通过原版快照哈希核验的来源；候选自身调用不能成为原版执行证据。候选变动、外部路径、未登记来源仍不能获得信任。原版保护及候选保护继续独立执行。

## 执行诊断

新增程序维护的 ExecutionDiagnostic，Verdict 增加可选 execution_diagnostic 字段，旧记录默认 None。诊断只含受控码和有界事实：code、stop_reason、duration_seconds、probe_complete、protection_checked、last_test_stage。last_test_stage 来自最后一条已有 test_phase，如 setup:passed；没有记录时 unknown，不猜测进入了哪个函数。

复用 Verdict.unmet_checks 填程序失败项，不再返回空列表配合成句。reason 保留人可读的具体事实，Controller 使用受控码，不能根据英文句子判断。

诊断优先级如下：

1. 运行绑定或候选 manifest 哈希错误：仍 INVALID_CANDIDATE，分别 RUN_BINDING_MISMATCH / CANDIDATE_HASH_MISMATCH。
2. 清理失败：保留 Controller 现有 FAILED 优先级；单独调用 Verifier 时也不得接受。
3. 非 EXITED、日志超限或探针不完整：classification=None，分别 COMMAND_TIMEOUT / LOG_LIMIT / EXECUTION_NOT_EXITED / PROBE_INCOMPLETE。超时不是候选内容错误，也不等同环境故障已被证明。
4. 完整退出后，实际保护检查失败：仍 INVALID_CANDIDATE / SOURCE_PROTECTION_FAILED。
5. 无完整性故障时，继续现有框架、行为和模型语义核验。

未完整退出时，Runner 目前填 candidate_ok=False 的保护占位结果；诊断必须标记 protection_checked=False，不能把它解释为源码确实被修改。没有 session_finish 的 target_origins 也不能被解释为确实加载了错误版本。

命令超时的反馈明确说明实际耗时、原 stop_reason、probe_complete、最后完成阶段和失败检查；建议缩小候选或报告执行障碍。它不能进入模型语义验收，也不能贡献 SINGLE_OBSERVATION。

## Controller 的有界处理

同一任务中，按原版 snapshot、environment 和 execution_role 跟踪连续 COMMAND_TIMEOUT，阈值为 2。

第一次：保存 run/verdict，返回明确反馈，允许探索模型调整一次候选；不自动重跑相同候选、不加命令时间、不新增模型请求。

第二次连续超时：记录 REPEATED_COMMAND_TIMEOUT，任务进入 BLOCKED，stop_reason=REPEATED_COMMAND_TIMEOUT；保留两个 run、诊断和候选。它表示当前执行条件下继续尝试的预算已用完，不宣称两个超时具有相同内部根因。

一次实际执行返回非 COMMAND_TIMEOUT 时重置该计数；读文件、换假设、列经验或修订契约不重置。候选 ID 或内容变化也不能自动清零，否则仍可能连续跑七个超时候选。不同执行环境与角色不共用计数。

此阈值只控制 original 探索循环。重跑出现执行故障不能确认该次复现；fixed 出现执行故障时保留明确诊断，fix_validation_status=blocked，保留已独立确认的原版证据，不声称 fixed passed。fixed 正常退出而测试失败仍为 failed。不新增额外重试。取消、任务预算耗尽和清理失败保持原优先级。

## 观测和输出

Verifier 继续记录 aggregate binding_and_integrity_ok，并显示实际失败项；没有执行的检查不能显示 passed。Controller action/phase 增加受控执行错误码，trace 关联 candidate_id/run_id/诊断及连续计数，不把权限与工具执行重复计数。

report.md 的现有模板显示具体执行阻碍、失败检查、最后完成阶段与下一步；历史缺少 execution_diagnostic 的记录仍可查看。程序诊断不能被包装成新的 Bug 复现证据。

## 验收

确定性测试验证重复路径解析调用次数下降、缓存上限与跨会话失效，而不是用机器耗时做单元断言。保护/绑定/清理等原检查保持拒绝；旧 Verdict 可反序列化。

Controller 测试覆盖一次超时后候选成功、两次连续超时停止、候选 ID/契约修订不绕过限次、正常执行重置、取消/清理优先级以及重跑/fixed 不虚报成功。

最终离线重放已保存 SymPy 候选：保持 60 秒命令上限，要求完整退出、probe_complete=True、原版来源和保护有效，并观察到 Issue 中的异常。此项不要求最终 DONE；语义验收与 differential 仍可能暴露候选/契约问题。真实模型复跑另行授权，不用 Astra。
