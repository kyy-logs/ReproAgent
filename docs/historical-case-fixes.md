# 历史 Bug 验证问题修复与复测

日期：2026-10-05。

## 当前结果

原来的三个历史案例，在相同描述、提交、模型和预算下，最终均为
**DONE / DIFFERENTIAL_VALIDATED**。模型自动生成测试，原版在两个独立副本中失败，
同一测试在修复版通过，成功导出后又在新的原版与修复版副本中独立重跑。
没有把人工对照测试或修复代码提供给模型，也没有人工修改生成的测试。

| 案例 | Agent 状态 | 独立导出复跑：原版 / 修复版 | 模型请求 | Agent 耗时 |
| --- | --- | --- | ---: | ---: |
| more-itertools-645：空迭代器窗口 | DONE / DIFFERENTIAL_VALIDATED | 1 failed / 1 passed | 8 | 18.03 秒 |
| humanize-250：一字节浮点数与字符串 | DONE / DIFFERENTIAL_VALIDATED | 2 failed + 1 passed / 3 passed | 10 | 31.11 秒 |
| humanize-254：月份与多余零天 | DONE / DIFFERENTIAL_VALIDATED | 3 failed / 3 passed | 10 | 36.19 秒 |

第二例额外包含整数输入对照，该测试在原版、修复版均通过。第三例覆盖 31、92、32 天三个报告输入。
Codex 已逐个读取生成测试，确认调用真实目标函数、断言来自报告的正确行为，
未使用替身、宽泛异常捕获或无条件失败。独立人类正确性审查尚未进行。
这里的 **3/3 是这三个手工选择案例的完整自动流程结果**，不代表一般复现率。
评估中的 `effective_reproductions` 仍为 null，等待独立人类审查。

最终离线验证：**122 passed，1 skipped，85.42 秒**；`pip check` 无依赖冲突。
跳过项仍是 Windows 账户无符号链接权限。实际平台仍为 Windows、Python 3.12.14、pytest 9.1.1；
未把 CI 配置当作其他平台已验证的证据。

## 修复内容

1. **工具协议明确化。** 模型获得完整 JSON schema、必填字段、搜索范围枚举和路径示例。
   `scope` 只能为 snapshot/candidates/all；读取使用仓库相对路径。
   错误反馈提供允许值，区分任务证据路径和工具可读取路径。
2. **缺失信息判断收窄。** 只把当前 Bug 的复现前提列为阻塞项。
   其他输入、内部原因或非必需版本信息进入 assumptions。
   预期行为不明确或缺少来源时仍不能生成已确认复现，来源校验没有取消。
3. **契约结构严格校验。** 检查七个必填字段、字段类型、来源索引和未知字段。
   空对象、缺字段、对象代替文本等错误不会被当成用户信息不足。
4. **模型输出有限纠正。** 契约和语义验收最多各三次调用，共用原任务期限、取消状态和用量记录。
   provider 空内容、截断内容和异常响应结构使用 ModelOutputError，与配置错误分开。
   契约三次仍失败标记 FAILED / MODEL_PROTOCOL_ERROR；验收无法形成有效结论则保持未确认，保留真实执行证据。
5. **验收字段和引用明确化。** expected_assertion、target_triggered、failure_matches_issue
   必须是 JSON 布尔值；断言解释写入 reason。纠正使用相同原始证据和精确 available_refs。
   任一明确 false 不会通过纠正重试被提升为成功；无效引用也不能作为成功证据。
6. **契约修订的来源保护。** 只接受原始快照中已登记路径与哈希匹配、工具实际返回的引用。
   模型生成的候选测试不能被重新标成 repository 来源来支撑新预期。
7. **异常响应也记录用量。** 在解析 choices/message/content 前独立提取和结算有效 usage。
   即使没有 content，已返回的 token 和估算费用仍计入；未知费用仍为 unknown。

新增 25 个回归场景，包括真实 HTTP MockTransport、真实 pytest 子进程与来源保护。
修复发现的缺陷先用失败测试确认，再修复到通过。独立子智能体进行了三轮只读审查，
发现的五项重要问题均修复，最终复审没有新的 Critical/Important 问题。

## 复测过程与边界

保留了三轮完整记录，没有只留下最后的成功结果：

| 轮次 | 自动完整流程 | 模型请求 | 输入 token | 输出 token |
| --- | ---: | ---: | ---: | ---: |
| 原始基线 | 0/3 | 27 | 62182 | 15363 |
| 工具与契约修复后 | 0/3 | 45 | 188341 | 37999 |
| 完整修复后 | 3/3 | 28 | 98977 | 10985 |
| 合计 | 9 次案例运行 | 100 | 349500 | 64347 |

中间轮已经生成并执行候选，但验收模型把 expected_assertion 返回为断言文本，
校验器要求布尔值，且当时没有反馈纠正，因此仍耗尽预算。这是最后一轮修复的直接依据。
最终轮 humanize-250 仍有一次动作响应包含多个 JSON 对象，被拒绝并计步，
下一步收到错误反馈后自动恢复；这次失败请求也保留在统计中。

模型仍为 deepseek-flash、max_tokens=4096；每例仍为 12 个动作、主任务 240 秒、单命令 30 秒。
主任务耗时不包括完成后独立导出复跑。未配置费率，费用不能由这些 token 记录直接推算。
可分享结果记录最终提示词和产品源码的 SHA-256，实际输入哈希与基线逐例核对一致。

独立导出复跑使用仅安装 pytest 的目标解释器，无 ReproAgent 或 HTTPX。
验证退出码、完整 Probe、真实目标来源位于新副本，以及相同完成测试节点。
首个独立验证脚本曾直接比较 pytest 节点的不同根目录前缀而误判；
修正为移除精确新副本前缀后，在另一个新副本重新执行，原失败比较及日志仍保留。
这个脚本比较问题没有修改产品或生成测试。

## 证据位置

- 原始案例、提交、报告来源与哈希：`evals/cases/historical-smoke.json`。
- 原始基线结果：`evals/cases/historical-smoke-results.json`；说明见 `historical-case-validation.md`。
- 中间失败轮结果：`evals/cases/historical-smoke-after-fixes-results.json`。
- 最终结果、用量与代码哈希：`evals/cases/historical-smoke-verified-results.json`。
- 最终任务与成功包：`repro-results/historical-verified/<case>/artifacts/reproduction`。
- 本地模型记录：`.local/historical-cases/<case>/trace-verified.jsonl`。
- 独立复跑日志：`.local/historical-cases/<case>/independent-verified2-{buggy,fixed}.log`。

后两类目录默认忽略。原始基线及中间失败记录没有覆盖，独立人类评审、
更多项目、更多类型 Bug 和跨平台验证仍待进行。
