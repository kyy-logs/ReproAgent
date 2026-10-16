# 探索纯文本提前结束修复验收

## 问题与范围

基线 `c9d3b5a`，隔离分支 `codex/holdout-execution-reliability`。8474 上次真实任务使用10/20步后，模型返回3,785字节普通正文（两份类似修契约参数的JSON和说明），`tool_calls=null`。中间件允许该回复，SDK结束，PhaseGate无结果，Controller以 `NEEDS_INFORMATION / NO_CANDIDATE` 停止。剩余10步未用于恢复。

本次只修正常探索回复没有真实工具调用的协议缺口。20步、时间、费用、取消、工具权限、契约及Verifier标准不变；thinking保持关闭。不将文本JSON转为工具调用，不增加隐式模型请求，不改变经验检索策略。修复需要的纠错轮由现有预算支付，可能增加实际调用及费用。

## 实现

- 中间件在SDK执行前检查整份回复；零工具调用明确拒绝为 `MISSING_TOOL_CALL`，不再作为普通完成。
- 与多工具、未知工具、参数格式错误共用每阶段最多3次协议拒绝；即首次失败后最多再纠正2轮，预算不足可更早停止。成功读取工具不会重置这份拒绝额度。
- 每次纠错仍经过同一模型调用守卫，计入原任务步骤；HTTP重试仍属于同一逻辑步。没有获得额外20步或额外SDK总结额度。
- 纠错提示要求一个真实工具调用，按已注册Schema继续读取，或通过 write_candidate / revise_contract / request_information 结束阶段。仅有正文JSON不会执行；拒绝正文也不会原样回灌下一轮上下文。
- 有效PhaseGate结果优先于迟到的无工具回复；取消与时间/步骤耗尽保留原停止路径。三次拒绝用尽且无结果时，以 `FAILED / MODEL_PROTOCOL_ERROR` 结束，诊断保留具体码及拒绝次数/历史。
- 错误码复用同一协议词表，进入现有协议事件、受控学习材料和事实触发词表。没有新增经验推送行为。

## 验证

新增测试先在旧实现验证：8 failed / 2 passed，失败均为无工具回复被误接纳，非收集或环境错误。实施修复后10条通过，再补阶段结果优先级和受控经验码，共13条通过（exit0，11.27秒）。

覆盖正文/空内容/多个正文JSON、真实工具恢复、共享三次上限、保留读取历史、1/2步预算、取消/截止时间、首轮/最后一轮已绑定阶段结果。Controller集成经过真实SDK与真实本地pytest执行、Verifier及重复执行，HTTP模型回复脚本化，最终为DONE / REPEATED_OBSERVATION。

相关旧测试同步移除“纯文本可正常结束”预期。外部Issue脱敏测试改用真实request_information工具终止，仍独立检查原文hash、正文脱敏与NEEDS_INFORMATION。SDK结构化总结也不能绕过阶段工具协议。

## 上次真实回复的零付费调用重放

结果：`E:/ReproAgent/repro-results/missing-call-recovery-validation-20261016/recorded-response-replay.json`。

- 输入为上次保存的 last-provider-response.json，输入文件hash：`d0e69c7aba554e0c8ffc3ae42d77757229c3e26397443ce265c15e91350425dd`。
- 将该原始回复交给真实SDK与中间件：明确产生 `MISSING_TOOL_CALL`，进入下一次请求；下一次请求包含有界纠错提示。
- 后续响应是脚本化的真实request_information工具调用；2次mock HTTP、2步、0候选，正文未被自动执行。
- 真实模型请求0次。这是原始失败响应的协议重放，不是8474新评测，不证明真实模型会改正或8474通过。
- 当次源码hash：`d4f8da5ab69c1c58d253589522074005273c1a03636b16655a1bb9baf4032265`；重放前后核对通过。

## 完整回归与审查

首次全量收集1449条，三个互斥文件组采用仓库外basetemp运行527.891秒：1428 passed / 10 skipped / 11 failed。11个失败全部来自观测文件的旧“纯文本正常结束”夹具（原日志、XML不覆盖），没有其他文件失败。

把这两个文件的正常终点改为真实request_information，权限与工具执行仍按实际call关联；新增结束工具使总执行数从1变2，Read专属数量及关联断言保持。新增追踪回归逐份核对2份wire与实际requests一致、原始拒绝response保留、第2份请求包含纠错且不包含拒绝正文。最终两文件重跑 **28 passed / exit0，14.58秒**。

最终收集1450条，按 `(classname, test_name)` 精确移除首次两个受影响文件的旧结果，再合入其最终XML；逐条核对当前pytest完整收集集合，保证没有遗漏、没有重复计数、没有隐藏其他失败。最终 **1440 passed / 10 skipped / 0 failures / 0 errors**，核对脚本exit0。生产源码在首次全套、受影响文件重跑及原始回复重放之间hash一致。此结果为全量加受影响文件重跑的汇总，不冒充首次全量一遍通过。

分组/命令/原始XML：`E:/ReproAgent/.local/holdout-five-20261015/missing-call-final-validation/`；最终逐条核对及源码回执：`E:/ReproAgent/repro-results/missing-call-recovery-validation-20261016/verification.json`。

fresh-context独立审查无阻塞问题；审查者另跑相关五文件 **79 passed / exit0，132.30秒**，并以真实SDK/MockTransport验证tool_calls为null、空列表、仅reasoning、第10步恢复及第20步不超预算。增量观测审查无阻塞；其建议的逐份wire/response数量及次轮正文隔离断言已采纳，最终28条重跑通过。

未再次调用真实模型跑8474；不改旧评测、不声称官方通过。此次不合并main、不push。
