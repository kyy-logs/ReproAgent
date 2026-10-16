# 验收容量与候选 setup 反馈修复

## 问题与变更

最近一次 Sphinx #8474（trigger-feedback-8474-20261016）20 步耗尽，3 次候选执行均未获验收。第一个候选把 `env.all_docs` 的 float 值当作 doctree；第二个引用不存在的 `warning_stream` fixture；第三个验收材料 36,393 B 超过共享的 32,768 B 上限。

本次保留来源绑定、文件保护、完整执行、断言和 oracle 语义校验，不删除关键验收项：

- `BudgetLimits.review_context_bytes` 独立设置验收材料上限，默认 65,536 B。`tool_response_bytes` 默认仍为 32,768 B。两个字段分别验证并随 request 持久化；旧请求缺少新字段时使用新默认值。较大验收输入可能增加 token，用量与任务期限继续受原预算体系约束。
- 精确重复的 longrepr、异常帧和 crash 统一引用同包的完整 failure evidence，不等超限后才处理。不同 run 或不匹配的内容不去重；不截断候选、引用源码或失败正文。真正超限仍返回 `REVIEW_CONTEXT_TOO_LARGE` 和字节测量。
- `FRAMEWORK_CHECK_FAILED` 增加受控 `setup_failures`、`collected`、`executed` 测量。即使另一个测试已执行，也保留失败 fixture 的具体原因。保留原有拒绝代码、程序来源与验收门禁。
- pytest setup 报告先完整脱敏和路径归一，再优先取 E 错误行，避免长 fixture 清单使“fixture 不存在”从 512 字符摘录中消失。
- setup 反馈引导先 Read 原版 fixture/API；探索提示要求复用原版 helper/fixture、核对实际返回类型、不无据改写内部状态。它能修复反馈缺口，不能保证模型不再生成错误测试。

## 验证方法

先运行新增测试观察失败，再实现。首次 red 因外部 basetemp 父目录不存在有执行错误，已另存；改用系统临时目录后得到 13 个预期失败 / 21 通过 / 0 error（red-corrected.xml）。原版测试仍完整保留。

79 项相关测试通过（targeted.xml）。新增实际 Controller→SDK HTTP 请求测试最初确认缺少 Read/fixture 恢复指导，修复后与 API/容量反馈测试一起通过（wire-final.xml，4 通过）。旧的显式超限测试改为配置 `review_context_bytes`；脱敏扩张测试用实际请求的执行环境一起测量初始大小，保持“证据原本可容纳，脱敏后超限”的测试含义。

全量回归：完整收集 1,483 项，按 132 个文件分成三个互斥组（44 / 44 / 44 文件），1,473 通过 / 10 跳过 / 0 失败 / 0 错误，三组 exit 0，总用时 550.906 秒。明细见 `full-suite/summary.json` 与每组 JUnit XML。

## 真实记录零调用回放

产物目录：`E:/ReproAgent/repro-results/review-capacity-repair-20261016`。

- 同一份 8474 证据在旧上限仍需要 36,393 B / 32,768 B；新上限完整装入 36,393 B / 65,536 B，保留 6 条可引用证据（5 条预期来源、1 条失败记录）及完整候选。
- 第二次执行即使 `executed=true`，反馈也包含原始 `fixture 'warning_stream' not found`，并明确指向 Read 原版定义。反馈 1,872 B。
- 所有读取的历史记录、候选、引用源码和执行证据均验证哈希未变。模型请求 0，测试执行 0，未改写历史 verdict，未提交官方重判。

回放只能证明容量和反馈问题已修复；不证明第三个候选符合 Issue、不证明它在修复版通过，也不能将原来的 EXHAUSTED 改报 DONE。需要新的真实生成运行才能验证端到端效果。

## 独立审查

review_capacity_setup_repair 未发现可行动的实质缺陷；独立无模型探针确认旧记录默认值、非法容量、同 run 精确匹配去重和先脱敏后摘录的边界。审查代理的 pytest 受其临时目录权限限制，不能计作通过；测试结论仅依据主会话记录的有效运行。

本次未使用 Astra；未新增付费模型请求；未合并或 push。
