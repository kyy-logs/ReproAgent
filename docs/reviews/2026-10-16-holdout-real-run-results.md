# 五例修复的真实运行、官方结果与剩余原因

本文件记录 2026-10-16 的实际证据。没有把不同版本的单例成功拼成同一轮分数；没有根据参考修复手写或替换候选。

## 第一轮：代码 c6690cd，五例仍为官方 0/5

Agent 轮次：E:/ReproAgent/repro-results/holdout-reliability-20261016。
源指纹 2bbd54e326c23cbe26d27f94e298aeeabbc8748c5ffab1998895260e744d6c67。DeepSeek flash、thinking disabled、20步/900秒/单命令60秒/32768字节，与旧冻结配置相同。

| 案例 | 本地 | 官方 | 本轮的具体问题 |
|---|---|---|---|
| 8365 | DONE / DIFFERENTIAL_VALIDATED | false | 不再带skipif，但两个测试在Linux问题版与修复版都通过：PASS_TO_PASS=2、added_f2p=0。Windows反斜杠用户名没有在Linux触发同样问题；披露delivery不能代替真实目标环境执行。 |
| 8627 | NEEDS_INFORMATION / SINGLE_OBSERVATION | false | 新候选推断stringify/restify精确输出；独立审查不一致，真实autodoc触发后又在原版通过。新轮次不能沿用“旧候选有效、只是环境坏”的结论。 |
| 8906 | NEEDS_INFORMATION / NONE | false | 控制退出码/计数与真实触发条件有误，后续原版通过又缺captured stdout，模型无法诊断广泛文本搜索的命中来源。 |
| 8474 | EXHAUSTED / NONE | false | 20步都用于探索与四版契约；最后一版仍缺确切最小构建输入，最后动作是revise_contract，未发布候选。最后一次write拒绝收口机制并未被这轮触发。 |
| 5221 | NEEDS_INFORMATION / SINGLE_OBSERVATION | false | 候选已产生，存在两次语义批准后仅因generator地址改变被拒的机械重放问题；另一些候选还包含LineMatcher地址和RunResult/quoted stdout耗时差异。不能笼统归因模型不会写或数据坏。 |

这轮129次HTTP尝试，3,017,439 tokens，未配置价格所以金额未知；不能写成0成本。

首次官方进程在外部会话中断后退出，无最终回执，原日志保留，未计分。随后只复制冻结预测与已封存输入至官方重试目录：E:/ReproAgent/repro-results/holdout-reliability-20261016-grade-r1；没有重新请求模型。predictions hash两者均为70aa2325aa31111724186058ab53943150d6fc1d6215704dd5f80066d02b9063。

重试回执exit 0，687.987秒，5份report hash，official_grade_status=final、official_successes=0、official_graded=5。harness固定330a649a764fab2fadaea632776eeae87272f74b；输入hash a27011af436dad5552a72e1f4cf5ced9a8c7edf1c0e178cceec1ac0e9cb1f007。只提取候选自身判分，没有读取gold测试或补丁内容作为设计/模型依据。

## 独立的新8906：本地与官方均通过

轮次 E:/ReproAgent/repro-results/passing-output-8906-20261016，源指纹13a962454f218160e6a3813d82ea752072c9df335e6abfa5a009b8eb411b4444。

- 本地DONE / DIFFERENTIAL_VALIDATED / fix_validation_status=passed，286.594秒，28次HTTP，428,880 tokens；结束剩3步。
- 官方resolved=true，exit 0，124.502秒，official_grade_status=final、1/1；预测hash33fe0b3fc716f4d30c65fbfe0033c5004037b26acc12c45c9e63158123a16b49。
- 该轮1份NOT_REPRODUCED verdict带passing_outputs；完整sdk_agent_input 3471708b9c41be7d（13813B）保留这段反馈，sdk_model_input中也能看到；wire_request正文该阶段未完整保留。额外真实SDK+Mock HTTP回归确认输出文本、run_id、probe hash进入实际HTTP消息体。
- 不能仅据n=1把成功归因新输出反馈：无同版本重复对照，模型路径不同。这是独立样本通过，不会并入上面的五例分母，也不能合并旧003/004结果宣称新的整体通过率。

自动审批曾认为未覆盖本次8906外发而拒绝；核对sealed问题条目、绑定及规范化模型配置与已授权五例完全一致，并附用户“你跑完之后，你直接跑哈”的现有授权证据后，复核允许执行。没有借Claude Code绕过拒绝。

## 8627：历史候选的独立环境诊断，不算本轮成功

当前新候选未确认，不能换用历史候选拿分。原003布局不符合旧显式rejudge工具的固定tests/test_repro.py接口，准备阶段已拒绝，未放宽闸门。

使用已支持的002冻结候选只读重放，诊断位于E:/ReproAgent/repro-results/repair-8627-002-revalidated-20261016。两个一次性容器完成，pre失败/post通过，cleanup与rollback通过；新validator重验真实日志、脚本、镜像、依赖档案与输入绑定后返回candidate_differential_confirmed。对原002 round.json前后hash校验一致，没有覆写历史或再次挂接已有记录。official_effect=none。

## 离线验证与版本边界

- c6690cd全量：1324 passed / 10 skipped，exit0，1219.70秒。
- 有界输出反馈版本全量：1348 passed / 10 skipped，exit0，973.81秒；SDK→HTTP送达测试另1 passed /5.98秒。
- 输出反馈关联50项通过；截断、篡改hash、错误/损坏类型、fixed排除、旧记录兼容、JSON引号/反斜杠/换行秘密脱敏、U+2028/U+0085物理分行均有回归。两次独立非Astra审查发现的问题均以RED→GREEN修复。
- probe每个passed call stdout/stderr各最多4096 UTF-8字节；反馈每stream再限制1024B、最多两条。标明摘录/截断/脱敏/省略数，整个反馈仍遵守8KiB。不声称保留无限完整输出，不新增模型请求、不改变验收规则。

## 尚未完成的产品能力

8365需要真实目标平台执行，单靠提示或静态skip扫描不够。8474缺具体触发材料且最后一轮仍在修契约。5221 generator地址是已定位机械缺陷，另行小补丁；RunResult和quoted stdout时间仍严格保留。独立语义审查也会波动，应继续用受控重复实验验证，不能靠降低门禁让结果好看。
