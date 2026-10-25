# 契约范围与批量引用反馈设计

> **历史归档（2026-10-24）**：保存当时的设计、运行结果或验收依据，不能据此判断当前默认行为、最新测试数量或总体复现率。
> 当前入口见[文档导航](../../README.md)，旧路径对照见[归档索引](../README.md)。

状态：实现与离线机制验收完成，真实模型语义效果未验证。范围来自本轮已确认讨论：①区分可验证目标、可选提案与真正阻塞项；②一次反馈所有受支持的引用错误及可用引用。基线为本地 main `1d14e76`（2026-10-14），执行前须重新确认。

## 目标

减少两类无效探索：复现本不依赖某项提案，却被其未知细节阻塞；已读取源码，却因引用范围不匹配而逐个纠错。不得以清空缺失项、改变问题目标或放宽来源校验换取通过。

已实现的步骤软收敛不属于本次工作；保留最后三步拒读、预算、AgentScope 编排、Controller、Verifier 程序检查、经验工具和所有既有权限码。

## 1. 契约：按是否影响当前复现目标划分

沿用 IssueContract 的 expected、observable_checks、assumptions、missing_information、sources 和 description_hash，不增加字段或迁移历史契约。

| 信息 | 字段 | 规则 |
| --- | --- | --- |
| 本次有依据的行为目标 | expected / observable_checks | 明确触发、正确行为和判断粒度；来源可以包括 Issue 中明确的行为要求 |
| 未采用的可选实现方案或非阻塞细节 | assumptions | 明确标注未确认、可选、本次不采用；不能写成既定事实或作为验收依据 |
| 没有它就不能构造触发或判断正确行为的信息 | missing_information | 继续阻止发布；每项说明具体影响哪个触发或判断 |

分析和修订均由现有契约分析模型在原请求内完成。判断顺序：确定有来源支持的核心问题 → 判断未知信息是否为这个目标所必需 → 在三个现有位置保留其状态。程序不按“提案”等关键词删除缺失项，也不自动从 missing_information 搬到 assumptions。

当前 analyze_issue.md 同时要求“未确认的接口提案进入 missing_information”和“这里只放复现必需信息”，需要替换前者并统一三个提示词的解释。接口提案不能自动成为已有正确接口；同时，非必要的提案也不自动阻塞独立可验证的问题。

### 8906 的适用例与反例

Issue 同时描述错误提示误导用户、已有 allow_module_level=True 的正确用法，以及新 skip_module API 的可选建议。可以选择“错误提示未说明已有模块级跳过用法”作为复现范围，并明确范围；原版文档支持已有参数，Issue 支持需要改善的指引。只检查有依据的提示内容，不猜整句最终文案，不调用尚未确认的新 API。

skip_module 的名称与签名作为未采用提案记录，完整文案的不确定性作为非阻塞备注。若已能构造触发并判断提示缺陷，missing_information 可以为空。不能把任务偷换成“allow_module_level=True 跳过功能本身正常”，因为 Issue 已说明该方法有效。

如果 Issue 只要求新增 API，而接口或输出语义未确定，或核心触发输入/正确输出缺失，应保留阻塞。若有多个互斥且没有可独立验证目标的解释，request_information；不得为了发布选择更方便的另一题。历史契约不原地修改；重要行为变化仍须经过现有 revise_contract 和新来源要求。

Verifier 的语义提示同步要求检查契约与原始来源、所有测试是否对准问题；assumptions 不证明正确行为。它不能因“某测试在原版失败”就接受错误范围，也不能因用户未给整句文案就否定已有明确的语义要求。

## 2. 引用：保持严格接受规则，改善失败反馈

EvidenceLedger.contains、EvidenceViews.contains、canonical_ref 和 ReviseContract._cite 的接受条件不变：路径属于冻结快照，哈希有效，引用精确匹配已签发范围，当前文件仍通过校验。已显示范围的子范围仍需已有完整签发引用或精确 Read；本次不允许子范围自动通过。

对于参数结构合法、注册路径及行范围有效的 source_refs，逐项检查并汇总 SOURCE_HASH_MISMATCH / SOURCE_RANGE_NOT_DISPLAYED，而不是遇到第一项就返回。结构错误、未注册路径、非法行号或文件损坏继续走既有拒绝路径；不新造错误码、不把安全校验失败变成建议。

任何一项失败都不结束 phase、不产生 PhaseResult、不调用契约分析模型、不部分接受引用。仅当所有引用通过时按原流程一次提出修订。

### 反馈正文

正常容量内返回一份 JSON：

```json
{
  "code": "SOURCE_RANGE_NOT_DISPLAYED",
  "message": "Citation request rejected. Copy exact verified displayed references; no revision was accepted.",
  "error_count": 2,
  "errors": [
    {
      "index": 0,
      "code": "SOURCE_RANGE_NOT_DISPLAYED",
      "submitted": {"path": "src/_pytest/python.py", "content_hash": "<submitted-hash>", "start_line": 607, "end_line": 615},
      "available_refs": [{"path": "src/_pytest/python.py", "content_hash": "<verified-displayed-hash>", "start_line": 590, "end_line": 629}]
    },
    {
      "index": 1,
      "code": "SOURCE_HASH_MISMATCH",
      "submitted": {"path": "src/_pytest/outcomes.py", "content_hash": "<submitted-hash>", "start_line": 124, "end_line": 144},
      "available_refs": [{"path": "src/_pytest/outcomes.py", "content_hash": "<verified-displayed-hash>", "start_line": 110, "end_line": 169}]
    }
  ],
  "omitted_errors": 0,
  "available_refs_truncated": false
}
```

尖括号是格式示意；生产数据必须来自程序校验，不得填示意字符串。index 是提交数组的零基位置。顶层 code 和 metadata[reproagent_error_code] 都沿用输入顺序中第一个受控错误码，保留经验事件/学习和 trace 的兼容性；每项实际错误见 errors。一次工具调用仍只计一次，不为每个错误增加模型或工具调用。

available_refs 只来自当前 EvidenceViews.issued_refs，按同一规范化文件及当前真实哈希过滤、去重，且重新通过 views.contains。转回 snapshot-relative 路径，以便模型直接提交。每项最多 3 个建议：优先包含提交范围，其次相交，其次该文件其他已签发范围；同组按范围长度、起止行排序。没有可用已展示引用就返回空数组，不读未展示文件生成建议、不把建议签发成新证据、不自动替换提交。

建议集合在一次工具调用内按规范化文件/真实哈希复用，避免每条错误都重复扫描并校验相同文件；每条提交的 canonical_ref 仍重新读取并验证原版字节，来源改变则在使用缓存前拒绝。缓存不跨工具调用，排序仍按每项提交范围单独计算。

### 容量与安全

总正文不超过现有 tool_response_bytes（默认 32768 B），中文按 UTF-8 字节计。先减少建议，再从末尾省略错误条目；error_count 保留总数，omitted_errors 明示省略，available_refs_truncated 标记建议未完整显示。默认容量足够时，8906 的五项请求必须一次反馈全部错误。

不能切断 JSON。容量不足以容纳最小诊断对象时返回预算内的固定 ASCII 短文，metadata 仍保留首错误码；零容量返回空正文。不得借诊断突破预算，不修改全局 _capped。路径、提交内容、来源和反馈仍是不可信数据，不提升为系统指令。

## 验收边界

- 契约：明确可复现行为 + 可选提案；仅未确认新接口；缺少核心触发/预期；源码与契约冲突；候选仅验证已正常功能。验证提示同步、既有结构与发布规则、原 Issue 和证据保留。
- 引用：多项混合错误一次反馈；有效提交仍接受；子范围仍拒绝；未展示/被截断行无建议；过期哈希/损坏文件无可用建议；外部路径拒绝；中文与小容量有明确省略；失败 phase 保持打开。
- 真正送达：真实 AgentScope + httpx.MockTransport，校验同一 tool_call_id 对应的完整拒绝正文出现在下一次实际 wire_request；随后复制已签发范围能结束 phase。不以 tool 日志存在替代送达证明。
- 离线测试不能证明真实模型已学会分离提案。模型语义效果另跑真实 8906，报告契约、引用失败次数、候选目标、执行和最终证据等级；须单独授权付费运行，thinking 保持现状，验收不用 Astra。

## 不在范围内

不新增分类模型、自动契约语义审判器、经验自动推送、索引/错误码迁移、源码子范围授权、额外探索步骤或新预算。既有软收敛功能只做兼容回归，不重复实现。
