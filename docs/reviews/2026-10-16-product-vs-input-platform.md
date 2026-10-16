# 剩余失败：产品、输入与平台的分界及修复

日期：2026-10-16。基线 codex/holdout-execution-reliability c43e750；主工作区及历史轮次不修改。

## 如何判断

官方不通过是结果，不是根因。分别核对原始 Issue、候选源码、原版执行、独立语义验收、修复版对照及官方候选侧结果；不查看金标测试/补丁来指导候选。预算耗尽也不能单独证明模型能力不足、输入有问题或增加步数有效。

| 案例 | 已核实事实 | 归因与仍未证明的部分 |
|---|---|---|
| 8365 | Issue 明确 Windows 用户名含反斜杠。新候选在 Windows 原版失败、修复版通过；Linux 官方两个节点均 PASS_TO_PASS，added_f2p=0。 | 平台相关触发成立；本地结果不能证明 Linux 交付。数据损坏无证据。产品缺少目标平台执行闭环，之前的提示词/静态检查不等于此能力。 |
| 5221 | 新候选在同一个测试中依次要求默认 function scope、session scope 与 fixture 名同一行。原版失败，修复版亦失败；官方 FAIL_TO_FAIL。Issue 仅要求显示 scope，未规定精确排版。历史生成器地址引起重跑不一致已另行机械验证修复。 | 候选存在未独立证明的格式约束，语义验收未可靠区分功能需求与排版要求。参考修复未满足该测试，可能来自候选过窄或修复覆盖范围差异，不能仅凭结果断定哪一方语义错误，更不能说数据损坏。 |
| 8627 | 旧轮次的 tox 子环境缺 pkg_resources，独立修复环境可得到差分。新轮次对 stringify/restify 精确内部名称的同一候选，独立验收先给 REPRODUCED，重跑给 INVALID_CANDIDATE；真实 autodoc 候选在本地原版通过。 | 旧环境故障和新候选问题是两件事。新候选把文档交叉引用问题替换成内部字符串精确比较，属于产品误选验证目标；真实触发尚受环境/输入条件影响。不能笼统继续算作环境的锅。 |
| 8474 | 六行 Issue 没有最小文档/config，主要询问升级后 warning 的来源。四版契约仍缺触发输入，最后一版还把自述“不构成阻塞”的格式问题放在 missing_information。最后动作 revise_contract，随后步数耗尽。 | 输入不足是实际限制，但是否足以判定 bug、期望是否必须无 warning 均未确定。契约模型没有收到上一版状态和探索者修订原因，是确定产品漏洞；不能通过清空阻塞或编造正确输出换取成功。 |
| 8906 | 新的 passing-output 单例本地差分通过、官方 resolved=true。 | 原先临时路径与通过输出不可见已修复并有单例正向结果；n=1 不能证明单一改动的因果收益。暂不再次调整。 |

原始五例：`E:/ReproAgent/repro-results/holdout-reliability-20261016`。
官方重判：`E:/ReproAgent/repro-results/holdout-reliability-20261016-grade-r1`。
新单例：`passing-output-8906-20261016`、`generator-replay-5221-20261016`。

## 本次确认并修复的产品漏洞

### 1. 契约修订输入丢失

调用方创建 EvidenceContext(previous_contract=...)，但 ReproAgent.analyze 的真实请求只有 description/sources/texts/response_schema；previous_contract 只用于返回后的程序比较。phase.reason 则在调用完成后才写进保存的契约，模型从未收到。因此“针对旧阻塞修正”实际是重新分析 Issue，不是状态连续的修订。

修复：仅修订请求增加 revision.previous_contract 和 revision.reason。两者明确定义为待核查状态/建议，不是新规范证据；来源仍只有 sources/texts。保留物质性变更要求新证据的现有门禁，不修改验收标准。

### 2. 精确重复引用膨胀上下文

Controller 把旧 sources 与本次 source_refs 直接拼接。回归实测两个修订后的来源数量为 1→3→5；完全重复的原版行重复发给模型，白白消耗预算。

修复：按不可变 SourceRef 完整值精确去重，保留不同 hash、范围、来源类型。回归为 1→2→3，不合并相邻/重叠但不同的行范围；新修订状态与原因也计入输入容量。独立审查复现“估算27,494 B，实际34,280 B”的边界后，已改为预检与执行共用 contract_request_payload，按最终用户消息 JSON 的 UTF-8 字节数检查；包括协议纠错与 BudgetedGateway 脱敏后的再次检查，超限早于费用预留，拒绝携带 needed_bytes/budget_bytes。该限额不指整个 HTTP 包的 headers、system prompt 或工具 schemas。

### 3. 耗尽后丢失当前阻塞

最后一步如果是 revise_contract 而不是 write_candidate，原有特殊收口不会生效，只保留 agent step limit reached。

修复：保留 EXHAUSTED/原停止原因，不把预算问题改成数据缺陷；附加最新 contract_id/version 的未解决信息，并产生 contract.unresolved_at_stop 事件，引用完整冻结契约。origin=contract_analysis、cause_confirmed=false，明确它是待核查的模型阻塞，不是程序判定的数据集缺陷。不增加总结模型调用。

### 4. 独立 Verifier 缺少实际平台字段

探索上下文已有实际平台，但语义验收请求没有明确环境平台。裁判提示要求考虑平台，并不代表输入具备这一事实。

修复：使用 ExecutionResult.environment_id 加载绑定环境，向有界验收投影加入环境 id、platform、pytest 版本；旧缺字段记录保持 unknown，缺记录标明 missing，错绑拒绝并保留 EVIDENCE_UNAVAILABLE。新增内容计入原 32768 字节上限，不传解释器绝对路径、不读取修复版信息。

### 5. 交付报告范围不清

修复：产品 report.json/report.md 明确实际本地复现平台、带来源的目标平台和 target_unverified。Windows 本地差分通过仍然是有效的本地证据，不会被重新包装成 Linux 验证。此改动不实现 Linux runner，不提高官方成功数。

## 验收及边界

- 两轮 RED→GREEN 已记录：修订输入/精确去重/耗尽阻塞；独立验收平台/旧字段/错绑/交付报告。
- 相关回归：93 passed，exit 0；实际SDK序列化与HTTP MockTransport验证输入传递，不代表真实模型理解或效果。
- 未采用负面语义判定永久缓存：某次裁判可能把合法执行控制错判为无依据，直接缓存会固化错误。应从证据与审查标准改善，而非投票或缓存制造确定性。
- 不增加探索步骤、不启用 thinking、不修改参考补丁/官方计分，不手写替代候选。
- 后续真实单例和最终宽回归结果另记，不把旧版成功拼成新版通过率。

## 未解决的能力问题

8365 要完成目标平台交付，需要在原版探索/验证期间提供有来源记录的真实 Linux 执行，或在匹配环境运行整个 Agent；仅新增提示/报告不能解决。5221/8627 的语义正确性还需要公开原版依据支持具体 oracle；程序能校验引用存在，不能把存在的引用自动当成逻辑蕴含。8474 若仍找不到触发文档及有依据的正确行为，应诚实报告未复现，而非强制取消所有 warning。


## 本次真实运行：8474

新轮次 `E:/ReproAgent/repro-results/revision-context-8474-20261016`，保持冻结 Issue/绑定、DeepSeek flash、thinking disabled、20步/900秒/命令60秒。

- 源码 hash：a2dc58d54058d98c33e6d3d35742aa6a20d389b68c997332d9a31b601bb62f58，基线c43e750+本次未提交修复；这是下面诊断保留补丁之前的版本。
- 最终 EXHAUSTED / SINGLE_OBSERVATION，2候选、3原版执行、3判定；203.047秒、24次HTTP尝试、1,001,952 tokens。价格未配置，金额未知而非免费。
- v1缺3项信息；引用公开原版 tests/roots/test-numfig 与对应测试/实现后，v2明确了触发方式并清空阻塞。真实wire内容完整保留的修订用户消息为7710B，含旧v1、3项旧阻塞和探索者的非空修订理由。证明输入送达，不证明修改必然导致该结果。
- 第1候选把构建产物写进tests/roots/test-numfig/_build，被 SOURCE_PROTECTION_FAILED 拒绝，没有放宽保护。第2候选调整目录，原版运行两次均完整、保护通过。
- 第2候选首次裁判 REPRODUCED；重跑裁判反对 A3/A4/A7/A8 额外内部断言（figtype == 'table'、get_fignumber返回结构），并因另一些supported=true条目缺少期望来源引用而整体schema无效。程序正确未提交DONE，剩余步骤耗尽。
- 这不能继续归为“数据集缺输入所以不能执行”，因为本轮已经从公开仓库得到一个可执行触发；也不能计作成功，独立语义验收仍未确认。未导出可计分候选，未浪费官方容器判一个空补丁。

## 真实运行追加修复：保留无效否定审查的具体缺口

之前只有 invalid_negative_response，丢掉了 schema 的具体错因及裁判意见，探索者难以修正。新增 _negative_diagnostics，把校验异常、provider_reason、已定位的否定意见传入 MODEL_PROTOCOL_ERROR.measurements。每项先脱敏再限制为512字符，最多16项，记录 omitted_negative_checks；意见明确标为 unvalidated model objection。classification仍为null，不新增纠错请求，不把错误格式中的模型意见当成已验证结论。

对本轮录制响应 ce2b16fb4836374b 做了零provider调用的只读重放：得到明确校验错因 supported assertion_reviews require expectation source citations，以及A3/A4/A7/A8四条具体意见。原task.json/events.jsonl哈希前后一致；输出 `E:/ReproAgent/repro-results/revision-negative-diagnostic-replay-20261016/{receipt,verdict}.json`。这是诊断回放，不是新的Agent成功或官方判分。新增3项回归先RED后GREEN，相关30项通过；独立审查又验证22条意见→保留16/省略6、脱敏、畸形ID/reason等边界，无新增阻断。

最终完整离线回归：1372 passed、10 skipped、3 failed，967.60秒。三处失败均为测试夹具：两个精确长路径生成器在特定临时根长度下少1字符；一个纠错容量夹具未将新环境元数据计入首轮投影。只修这两个测试文件，保留原精确长度/首轮可发送/仅纠错超限的断言；使用等长的新basetemp复跑相关9个模块，91 passed、1 skipped、exit 0，128.56秒，覆盖全部三处失败。生产源码未再改变，hash与诊断只读回放时一致。未重复第二次完整全套调用；不伪称单次全套exit 0。详见 E:/ReproAgent/repro-results/product-context-repair-20261016/verification.json。没有再次为诊断改动发送真实模型请求，亦不把诊断前轮次冒称最终版本的性能评测。
