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


### 授权后的真实复测结果

首次启动被自动审批拒绝（前一次授权仅覆盖上一轮付费请求），进程未启动；用户随后明确授权这一次8474调用后再运行。最终运行提交 `66e9a94`，目录 `E:/ReproAgent/repro-results/scope-information-8474-20261016/`，源码与round seal均复核通过。

- **EXHAUSTED / NONE**；243.141秒（另预检33.188秒），20/20步；3候选 / 3真实执行 / 3程序判定。
- v2 **0条阻塞 / 3条非阻塞未知**，模型确实使用新分类并越过旧门禁。期望的该阶段修复已进入真实运行。
- 三次运行都正常EXITED、exit0，均NOT_REPRODUCED；模型选的仍是有标题表格，修改引用与HTML关键词也未触发缺陷。旧文件对象getvalue异常本轮未出现，但不能把单轮变化归因于指导文案。
- HTTP22次（contract2、exploration20），900,492 tokens；未配价格，货币费用未知。原版通过由程序直接拒绝，无语义Verifier请求。
- accepted_candidate为空、predictions为空补丁，未做官方判分；CLI批次exit0不等于案例成功。

范围修复已验证，**8474自动闭环仍未通过**。剩余缺口是原版通过这一负反馈没有促使模型重审错误触发假设；候选的宽泛HTML关键词也可能被自身标题满足。没有通过手改候选、灌入手工最小输入或放宽验收取得分数。详见新目录run-analysis.json/md及trace.html。未做第二次新增付费运行。


## 后续最小修复：原版通过后的触发重审反馈

用户要求“最小修复”，基线 `1b74f40`。生产修改仅verification_feedback.py的ORIGINAL_PASS_GUIDANCE一个常量：对CANDIDATE_NOT_REPRODUCED保留原版通过事实，说明当前输入可能是正常对照，提示核对原版失败分支/触发条件；契约选错时凭原版证据revise_contract。保留名称/说明/回显污染提醒，禁止靠无关断言造失败。不改诊断码、恢复枚举、判决、门禁、数据结构、工具或预算，不自动改契约，不增加模型调用。

先验证三档容量与真实wire缺指导的RED，再通过初步54条相关回归。独立审查随后发现初版文案使已有完整长度绑定的1KiB回退失败（1096>1024），根代理独立复现；新增测试原先使用短ID/classification=None覆盖不足。压缩唯一常量到211个UTF8字节，并改用既有passing_verdict()的完整绑定。最终直接相关8文件 **88 passed / exit0 / 68.03秒**；覆盖8192/1536/1024容量、绑定/分类/来源/恢复不变、脱敏、非对应判定不误用，以及真实执行→反馈→SDK实际请求。原测试保留、失败旧日志不计通过。没有重复完整套件，也不把66e9a94的完整验收冒充本次完整验收。

独立增量审查6项通过，先前P2关闭。完整绑定的1024档输出1017字节（剩7字节），后续加文案仍需容量回归；证据修订条件仍保留。本次是反馈提示修复，不保证真实模型采纳。

上轮8474三份真实NOT_REPRODUCED记录以最终代码零调用重放，原始文件hash、candidate_id、run_ids与program_check来源保持。最终回执与反馈在 `E:/ReproAgent/repro-results/original-pass-minimal-validation-20261016/final/`，相关测试XML在父目录related-tests-final.xml。无新测试执行或模型请求用于该重放，8474历史结果仍未通过。

本次未再次发起付费复测，未合并main、未push。
