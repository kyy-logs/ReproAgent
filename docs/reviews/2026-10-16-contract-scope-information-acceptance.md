# 契约阻塞范围修复验收

## 范围与基线

隔离分支 `codex/holdout-execution-reliability`，基线 `e5f4def`。修复真实8474运行中“其他场景未知/完整原项目配置缺失也阻塞当前候选”的表达与恢复链路；不修改历史评测，不把手工诊断测试替换为Agent候选，不改20步/900秒/单命令60秒/32KiB或thinking关闭配置，不放宽Verifier标准。没有新增工具或额外分类模型请求。

前置诊断位于 `E:/ReproAgent/repro-results/diag-8474-trigger-20261016/analysis.md`：无标题表格+numref在两个builder均触发原版目标警告，参考修复版消失；有标题对照原版无目标警告。冻结的被拒候选原样旁路执行还存在文件对象getvalue误用。这些诊断不算Agent通过。

## 变更

- IssueContract末尾增加可选 `nonblocking_information: tuple[str,...]=()`；模型响应schema允许但不要求该字段，老响应及老存储可按默认空值读取。原七个必填字段及未知额外字段拒绝保持。
- 模型在同一次契约分析/修订请求中分类：missing_information是当前已选场景的必要阻塞；nonblocking_information是仍未知但不阻塞此场景的事实并说明范围；assumptions保留已有假设。程序只检查结构，不声称能自动识别自然语言语义或防止所有误分类。
- 新字段必须为非空字符串数组；同一问题去首尾空白及casefold后不能同时出现在阻塞与非阻塞两类。
- 删掉旧missing项也视为实质契约变化：响应实际选择的sources中必须有旧契约没有的原版SourceRef，仅提供但未选中的来源不够。不自动过滤、迁移或清空任何阻塞。
- CandidateService仍拒绝任意真实missing_information，也仍要求expected及sources。非阻塞上下文不能替代预期证据。
- tool拒绝反馈显示两类信息并指向证据修订；Explorer、持久化、Verifier投影和report.md保留显式非阻塞上下文。空字段不扩大旧Verifier投影；非空内容仍计入实际32KiB总限，不能靠分类隐藏超限。
- 通用探索指导要求沿原版失败分支核对触发条件、区分失败输入与正常对照、构造最小fixture、验证辅助对象真实类型/API。没有Sphinx表格、警告或正确输入特例。独立Verifier明确不把非阻塞未知作为断言证据。
- 修正上一次修复遗留的提示词说明：无工具回复进入MISSING_TOOL_CALL纠正，而不是正常结束。

## 回归与审查

新增范围测试先观察到schema缺字段、analyzer拒新字段、无新证据仍能删阻塞、字段未入发布反馈/Verifier等失败。旧存储夹具最初缺schema_version且tuple未JSON化，这两次为夹具错误；修正后单独确认真正RED为旧对象缺少新属性，不把夹具错误算成产品证据。

第一组契约/发布/关系修订回归29 passed；契约、真实SDK wire、Verifier投影、报告、协议回归82 passed。真实SDK集成确认新字段进入探索模型、两次独立Verifier请求、存储和最终报告；本地pytest真实执行两次并走Controller导出，模型HTTP回答脚本化，不证明真实模型采用分类。

fresh-context独立审查34 passed，发现一项P2：通用新增指导放在Optional历史段后，experience_enabled=False时被partition裁掉。先补双模式RED（1 fail / 1 pass），再移动通用段到Optional历史区前；最终新增字段及真实wire测试15 passed。审查者只读增量核对确认P2关闭，其他无新问题。

首次全量因上述P2修复中止，旧目录 `E:/ReproAgent/.local/holdout-five-20261015/scope-information-final-validation/` 标记interrupted，任何局部结果不计最终通过。最终完整回归在独立v2目录一次通过：1465条收集与执行总数一致，1455 passed / 10 skipped / 0 failures / 0 errors；三个互斥文件组均exit0，墙钟797.563秒。源码指纹复核一致。日志、分组、命令及XML位于 `E:/ReproAgent/.local/holdout-five-20261015/scope-information-final-validation-v2/`，汇总与源码回执位于 `E:/ReproAgent/repro-results/scope-information-validation-20261016/verification.json`。

## 真实单例复测

待最终离线验收后，使用同一冻结公开Issue和原版源码在修复后的隔离分支跑一轮8474。不发送手工probe、正确触发输入、参考补丁或金标测试。真实结果、分类是否被采用、候选是否自行纠错与官方结果须分别核对，不能以schema或脚本化集成通过代替案例通过。
