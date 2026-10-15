# Verifier 失败反馈与修复决策设计

日期：2026-10-14。核对基线：本地 main `dbdf7e7`。

状态：用户已要求实现并隔离；产品实现、独立审查修复和完整离线回归已完成；本地集成状态见验收记录。目标是让每次验收失败回答“哪里没过、依据是什么、接下来能做什么”，并证明反馈到达下一轮真实模型请求。核心完成门禁、验收强度和预算保持现有语义。

## 1. 当前事实

模型发布候选后，Controller 执行原版测试，Verifier 先做程序检查再用独立上下文做语义审阅。第一次 REPRODUCED 只贡献 SINGLE_OBSERVATION；第二次执行、验收和失败一致性确认后才能贡献 REPEATED_OBSERVATION。可选修复版通过后贡献 DIFFERENTIAL_VALIDATED。成功导出后由 Controller 提交 DONE；DONE 不等于修复版差分已通过。

Verdict 已有 classification、reason、evidence_refs、unmet_checks、next_action、候选/契约/run 绑定和 execution_diagnostic。执行超时、保护、日志和探针故障已经有程序维护的诊断；同环境、同角色连续两次命令超时的停止机制已经实现。

缺口是部分非执行失败只有自由文本 reason，unmet_checks 为空，模型不知道是哪项检查拒绝。next_action 只有 narrow_candidate 等少数分支被 Controller 特殊处理，其余主要作为反馈交回探索。Controller.feedback 将序列化文本按 8192 UTF-8 字节直接切片，存在切断 JSON、引用或关键建议的风险。

代码定位：core/verifier.py、core/controller.py、core/models.py、core/protocol.py、core/execution_diagnostics.py；下一阶段输入经过 AgentContext.feedback 和 adapters/agentscope/runtime.py 的 _message。

## 2. 第一版范围与方案选择

采用“程序组织结构化失败 + 现有 Controller 分支决定继续/停止 + 有界反馈送达”。

只调整提示词可减少笼统回答，但无法稳定覆盖程序短路失败，也无法让 Controller 根据事实处理；新增规划/诊断 Agent 会增加调用与编排。当前方案复用已有检查和已有语义请求，增加可解释性和稳定字段。

第一版不新增诊断模型，不增加语义重试次数，不自动安装依赖、不修改原版源码，不扩大探索权限、不新增候选工具或经验检索机制，不建立通用人工转交系统，不新增“所有错误重试两次”的策略。已有连续超时和同候选证据超限去重继续生效。

## 3. 记录结构：沿用 Verdict，增加可选 issues

新增嵌套 dataclass VerificationIssue；Verdict 在末尾增加 issues: tuple[VerificationIssue, ...] = ()，不改变原有位置参数顺序。

| 字段 | 含义与约束 |
| --- | --- |
| code | 程序预定义的受控诊断码；模型不自由生成错误码 |
| check | 实际到达的检查标识，或现有 provider 布尔检查名 |
| origin | program_check / provider_claim；区分程序事实与模型判断 |
| message | 简短、人可读说明；模型原理由可作为不可信说明，不能驱动程序分支 |
| evidence_refs | 已验证且适用于本次检查的引用；没有有效引用允许为空，不能制造引用 |
| measurements | 程序填充的有界事实，例如 needed_bytes、budget_bytes、stop_reason、duration_seconds；不接受模型任意键值 |
| recovery | revise_candidate / revise_contract / narrow_candidate / request_information / inspect_evidence；建议，不是执行授权或“必然可修复”承诺 |

classification 继续表示候选判定；issues 描述判定缺口。两者不能互相替代。Verdict 原有 run_ids、candidate_id、manifest_hash、contract_id/version 给出整体绑定，issue 不重复携带整份运行记录。

构造和反序列化均校验 code、check、origin、recovery 的受控取值；measurements 只允许该 code 明确规定的键和有限、合法类型。不能因为 dataclass 接受字符串就放任模型或损坏记录写入未知策略。

ExecutionDiagnostic 保持不变，不复制或替代现有运行诊断。执行类 issue 使用其中的真实 code 和事实，并保留原字段。unmet_checks 填实际失败的检查名；尚未执行的检查不得填作失败或通过。provider 布尔项未获确认时需标为 provider_claim，不能伪装成程序证明。

旧 schema_version=1 记录缺少 issues 时使用默认空元组，维持现有读取方式。新记录仍为 v1 的可选扩展；旧程序目前严格拒绝未知字段，因此代码回滚后不能保证直接读取新增字段的记录，需保留原版本读者或另行导出，不宣称双向兼容。

## 4. 程序维护错误码，不解析自由文本来猜原因

以下是诊断字段的封闭词表设计，不替换已有 action/phase result_code，也不改变 CandidateClass。已有受控执行码直接复用。

| 实际分支 | issue code / check | 下一步建议 |
| --- | --- | --- |
| 没有执行记录 | EXECUTION_EVIDENCE_MISSING / execution_evidence_present | inspect_evidence |
| 执行绑定、候选哈希、保护等运行诊断 | 原 ExecutionDiagnostic.code / 实际 failed checks | 保留现有策略；绑定/哈希问题先 inspect_evidence，不能假定环境或模型错误 |
| 非原版框架错误 | FRAMEWORK_CHECK_FAILED / no_non_origin_framework_errors | revise_candidate；保留 adapter 返回的具体错误条目 |
| 目标执行前候选导入不可用模块 | CANDIDATE_IMPORT_BLOCKED / framework_not_blocked | inspect_evidence；不能无依据承诺修改导入能解决 |
| 已按现规则判为环境阻塞 | ENVIRONMENT_BLOCKED / framework_not_blocked | request_information；维持 BLOCKED |
| 框架无效或没有已执行测试 | TEST_EXECUTION_INVALID / tests_executed_and_valid | inspect_evidence；保留具体框架错误、收集和执行事实 |
| 原版候选通过 | CANDIDATE_NOT_REPRODUCED / candidate_reproduced | revise_candidate |
| 预期或来源缺失 | EXPECTATION_UNGROUNDED / expectation_is_grounded | revise_contract；来源找不到时 request_information |
| 候选文件发生变化 | CANDIDATE_HASH_MISMATCH / candidate_file_hash | inspect_evidence；不把保护失败说成测试语义失败 |
| 审阅证据超限 | REVIEW_CONTEXT_TOO_LARGE / review_context_fits | narrow_candidate；保留 needed_bytes 和 budget_bytes |
| 证据不可用、被改或无法解码，当前异常不能进一步确定 | EVIDENCE_UNAVAILABLE / evidence_sources_available | inspect_evidence；未知子原因保持未知 |
| 合法语义答复中的 expected_assertion=False | EXPECTED_ASSERTION_UNCONFIRMED / expected_assertion | inspect_evidence / revise_candidate |
| 合法语义答复中的 target_triggered=False | TARGET_TRIGGER_UNCONFIRMED / target_triggered | inspect_evidence / revise_candidate |
| 合法语义答复中的 failure_matches_issue=False | ISSUE_FAILURE_MATCH_UNCONFIRMED / failure_matches_issue | inspect_evidence / revise_candidate |
| 合法语义答复拒绝候选，但没有更具体可映射的布尔缺口 | SEMANTIC_REPRODUCTION_UNCONFIRMED / provider_semantic_claims | inspect_evidence；保留真实 classification 和引用，不推断具体根因 |
| 缺少预期来源与实际失败双方的引用 | SEMANTIC_CITATIONS_INCOMPLETE / citations_cover_expectation_and_failure | inspect_evidence；保留现有协议纠错，不让探索伪造引用 |
| 答复字段、格式、引用无效，现有纠错耗尽或纠错信封超限 | MODEL_PROTOCOL_ERROR / semantic_response_valid | inspect_evidence；保留尝试次数和容量事实 |

上述 UNCONFIRMED 命名刻意保留不确定性：现提示词要求 false 或 null 表达不确定，不能把 false 直接解释为已经证明失败与 Issue 无关。

合法响应可同时产生多项未确认检查；拒绝的程序检查仍按原顺序短路，不为收集更多错误而执行原本不会执行的模型调用。三项为真但分类拒绝时也必须生成总体语义诊断，不能遗漏。

框架 adapter 目前返回部分自由文本。第一版保留其原始条目和原始失败证据，以 FRAMEWORK_CHECK_FAILED 等可确认的大类组织，不用字符串关键词伪装细粒度的稳定错误码。独立细化 adapter 的语法/fixture/skip 错误码是后续工作。

804ac7b 基线的语义响应为六字段：classification、reason、evidence_refs 和三个布尔项；实施时沿用并行 main 已加入 assertion_reviews 的七字段 schema（见整合说明）。程序从通过校验的响应生成 issues，模型不承担存储协议、程序策略或诊断码生成。格式无效时只能报告协议问题；即使包含负面声明仍保持现有“纠错不能升格成功”保护，不把无效字段当成已验收的语义证据。

本设计的返回分支覆盖不包括原本向 Controller 抛出的 BudgetStopped、取消和不可重试 ModelOutputError；它们保持现有异常与优先级，不伪造“已经完成验收”的 Verdict。Controller 的错误反馈保留其原受控代码。

## 5. 证据来源与保存时机

程序检查使用完整 ExecutionResult，事实来自 raw/protection/observation；语义检查只使用现有独立审阅投影，引用必须精确位于 available_refs 并通过来源校验。

运行事实可以通过 Verdict.run_ids 定位对应 runs/<run_id>/execution.json；可引用已校验的 stdout/stderr/failure_refs。某条探针失败文本不能替代执行未完整退出的实际量。候选内容变动时不能把变动后文件当作已验证候选证据；引用校验本身失败时不得再附同一个失效引用冒充证据。

Verifier 返回前形成完整 Verdict，Controller 在第一次和重跑验收后仍按现有时机保存 verdicts/verdict-<run_id>.json。先保存完整记录，再构建面向模型的精简反馈；预算缩减不能回写完整 Verdict。

没有 run 的失败仍保留为空的 run_ids，并使用任务/候选已知身份；不虚构执行。回放不一致继续使用 REPLAY_UNCONFIRMED 并引用两个真实 run，保持未知具体原因。第一版不改 confirm 的布尔 API、不新增失败签名解释器。

## 6. Controller 决定动作，修复建议不是强制流程

Controller 继续优先处理取消、全局预算、清理失败、现有重复超时停止、环境阻塞和证据超限去重；REPRODUCED 仍须经过重复执行和 confirm。issues 不能把这些拒绝翻成接受。

对能继续的失败，程序根据受控 code 形成简短 instruction，并在下一阶段反馈中给出 recovery。revise_candidate 意味着允许模型在现有工具权限内提出更合适的候选；revise_contract 意味着寻找原版依据后提出修订；inspect_evidence 表示先检查现有证据，不承诺问题可修复。原 reason 仍供解释，但不得用自由文本分支判断。

不增加硬性“下一步只能 Read”或探索阶段约束。真正的外部信息缺失可由现有 request_information 工具结束为 NEEDS_INFORMATION；程序证据明确的终止条件由 Controller 结束，不能等模型同意。模型的建议永远不能提高步骤、时间或费用上限。

Controller 记录本次决定 continue / stop 和实际依据。新诊断 code 放在独立字段，不扩大现有 PHASE_RESULT_CODES 或替换既有 INVALID_CANDIDATE、NOT_REPRODUCED、MODEL_OUTPUT_ERROR 等顶层结果。历史 viewer、评测和经验匹配保持既有语义。

## 7. 下一轮反馈：8 KiB 内有序缩减，禁止直接截断 JSON

第一版给候选验收反馈新增专用程序投影，不全面重写 Controller.feedback 的其他用途。实际输出仍放在 AgentContext.feedback 字符串，保持现有 Runtime 输入接口。

反馈结构包含原顶层 result_code、候选/契约/run 绑定、classification、unmet_checks、简短 reason、issues、现有 execution_diagnostic、instruction 和 projection（omitted_issues、omitted_refs、omitted_details）。现有 Controller 受控结果保持不变，例如单次 COMMAND_TIMEOUT、证据超限 REVIEW_CONTEXT_TOO_LARGE、普通语义拒绝原 classification。

容量按 min(8192, tool_response_bytes) 的 UTF-8 字节计算。先脱敏再编码；reason 和 message 使用有界摘要/摘录并显式标记截短，绝不新增模型摘要调用。保留第一主错误和关联身份、失败检查、决策与建议；先减长错误细节和辅助引用，再减尾部次级 issue，所有省略累计标记。若仍放不下则使用保留完整 JSON 的最小信封；最小信封也放不下则显式报告输入容量不足，沿用 NEEDS_INFORMATION，不把不完整 JSON或无反馈继续交给模型。

完整详情保留在既有 Verdict 文件，反馈可附其已保存记录的引用。第一版不新增查询工具，也不假定探索工具可以读取内部 Verdict；下一轮实际必需的主诊断必须在反馈正文内。不能用“详见文件”替代必需材料。

Runtime 仍检查 Issue、契约、反馈整体是否能装入阶段输入；当前优先移除最旧 history，不裁掉核心 Issue/契约的规则保持不变。反馈单独合规不等于完整请求一定合规；外层容量不足必须明确终止。

设计示例（新结构示意，非现有 trace 的原样输出）：

```json
{
  "result_code": "COMMAND_TIMEOUT",
  "run_ids": ["run-example"],
  "classification": null,
  "unmet_checks": ["execution_exited", "probe_complete"],
  "issues": [{
    "code": "COMMAND_TIMEOUT",
    "check": "binding_and_integrity_ok",
    "origin": "program_check",
    "message": "本次命令超时，未取得完整执行证据，尚未进行语义验收。",
    "evidence_refs": [],
    "measurements": {"stop_reason": "COMMAND_TIMEOUT", "probe_complete": false},
    "recovery": "narrow_candidate"
  }],
  "instruction": "缩小候选或报告执行障碍；只改假设不能解决执行超时。",
  "projection": {"omitted_issues": 0, "omitted_refs": 0, "omitted_details": 0}
}
```

示例省略其他生产字段，仅演示诊断关系。实际耗时必须来自真实 run；未知值不可填猜测。

## 8. 观测与报告

在现有 verify span/check 事件上增加受控 diagnostic_codes 和 check_source，Controller 记录 verification.feedback 的决定、候选/run/契约身份、反馈字节数和省略计数，关联已保存 Verdict。详细自由文本只留在受控工件，不把长文本、绝对路径或敏感信息放入指标标签。

报告现有失败说明区域可展示错误码、未通过检查和恢复建议，继续使用既有 report.md 模板；新字段缺失的旧记录回退为原 reason，并标明没有结构化诊断。不为旧记录推断不存在的错误码。

第一版不改 trace viewer 的交互布局和筛选。使用现有事件详情呈现新增字段；实际实现需验证字段能通过本地投影保留，不能仅调用 mark 就声称可见。探针、权限和模型思考内容的采集策略保持不变。

## 9. 文件边界

预期修改：core/models.py（嵌套可选诊断）、core/verifier.py（实际分支映射）、core/controller.py（候选反馈投影及决定记录）、新增 core/verification_feedback.py（受控映射和有界序列化，名称可在计划确定），reporting.py / exporter.py（仅有必要的安全展示），prompts/explore.md（解释诊断是反馈而非授权）。相关测试覆盖旧记录读取、Verifier 分支、Controller 决策、反馈容量、实际 SDK 请求送达和报告。

若共享序列化已能正确处理嵌套可选 dataclass，则无需改 serialization.py。review_evidence.md / protocol.py 不引入新模型必填字段。observe/check 的 facade 只有发现实际字段丢失才做最小适配。

不改完成状态门禁、预算策略、pytest 探针、源码保护、执行环境隔离、工具集、引用授权、经验分类/写入/召回和 Langfuse/OTel 基础设施。不能顺带提交用户评测 HTML 或其他会话的修改。

## 10. 验收条件

1. 列举所有 Verifier 返回分支，实际产生拒绝的分支均有至少一项可确认的结构化诊断；成功无失败 issue。短路后未执行的检查不被记作失败。
2. 程序事实与模型声明明确分开；合法语义 false 映射为未确认，不把不确定性抹掉。模型 schema 错、未知引用、无双方引用、负面语义不能通过补诊断变成 REPRODUCED。
3. 所有原绑定、保护、探针、完整退出、重复一致性、修复版隔离和成功导出条件保持原拒绝效果；旧 Verdict 读取默认 issues=()；新嵌套记录往返保持字段、引用和身份。
4. 候选验收反馈在多字节中文、大量引用、多错误、脱敏扩张和边界容量下始终为预算内完整 JSON；明确省略，完整 Verdict 不变。最小信封无法容纳时停止而非无声截断。
5. 现有单次超时后继续、两次超时 BLOCKED、证据超限同候选去重、取消/清理/预算优先级保持生效；新诊断不增加自动重试，不改原 action/phase 顶层代码。
6. 用真实 AgentScope SDK 和离线 MockTransport 截获下一轮实际序列化请求，证明 diagnostic code、failed checks、主要证据/身份、恢复建议与省略标记进入输入；仅断言工具记录或 AgentContext 字段不算通过。外层阶段容量不足不会丢核心事实继续探索。
7. 现有 trace 的新增事件详情和报告能看到主诊断，历史记录正常回退；不把停止与修复建议宣称为任务成功。
8. 本阶段不访问真实模型接口，不使用 Astra。真实 Bug 成功率改善需要后续真实评测；离线分支和送达测试只证明机制，不声称 8906 或其他案例已 DONE。

## 并行实现整合说明（2026-10-15）

实施期间另一代理将断言逐条审查与终止优先级合入 main `2e1f967`。本分支保留其 assertion_sites/assertion_reviews、每条断言的来源审查、无效 JSON 不再纠错、模型返回后检查取消/预算，以及负面审阅不能升格成功的保护。

原设计的“六字段 schema 不变”针对 804ac7b 基线；整合后沿用 main 已增加 assertion_reviews 的七字段 schema。本功能仍不另外改变模型响应协议。新拒绝分支增加 CANDIDATE_ASSERTIONS_UNCONFIRMED / candidate_assertions_supported / provider_claim，保存实际 assertion_ids 与省略计数，建议修订候选。无效 JSON 的返回增加 MODEL_PROTOCOL_ERROR 程序诊断，不新增纠错请求。

受控 trace 属性缺失已通过真实导出复现，按第 9 节允许的最小适配扩展观测白名单。低容量反馈的重复身份字段只在等价时去重，保留真实绑定和计量；已终止分支不再发送模型请求，反馈容量不足仅记录不可用，不改写其停止原因。

## 11. 待批准与后续

用户已要求按设计实现并隔离开发。实施计划、逐项回归及独立审查已执行，具体结果见 ../../reviews/2026-10-14-verifier-failure-feedback-acceptance.md。最终全量与主分支集成状态仍以验收记录为准，不把离线送达证明视为真实模型有效性的证明。


## 审查修复的 Runtime 边界（2026-10-15）

运行时将 JSON 反馈继续作为字符串交付 AgentContext，保留原接口；在 Runtime 最终脱敏处先严格解析反馈对象、按字段值脱敏、重新编码，避免对编码字符串替换 JSON 转义。旧纯文本继续原路径；脱敏后仍执行完整阶段容量检查。受控备用建议插入前同样脱敏并重新测量，不把容量恢复作为绕过脱敏的理由。
