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

8365需要真实目标平台执行，单靠提示或静态skip扫描不够。8474缺具体触发材料且最后一轮仍在修契约。5221 generator地址已按下面的专项修复并验证；RunResult和quoted stdout时间仍严格保留。独立语义审查也会波动，应继续用受控重复实验验证，不能靠降低门禁让结果好看。


## Generator专项：机械问题已修，新5221候选仍未过官方

小补丁源指纹5f773aea3dadd3a5fadda6523aab7de0c38216dd32d2467837c1dd5baa9c367b。只观察AssertionError最内层frame前256个局部变量名中的pytest重写临时值，沿既有64 IDs/256visits预算；不保存locals内容、不调用repr/生成器迭代，不把探针自己创建的辅助列表身份放入白名单。collector拒绝畸形或超过64项的ID。正则仅扩展<locals>/<genexpr>；二元比较仍使用左右各自的operand IDs，时间、函数名、实际业务值和引号保护保持原样。

目标回归先RED，再有35项generator/probe/expanded/private-temp关联测试通过；真实pytest重写断言的两次执行可确认。独立非Astra最终只读复核通过。最终全量1362 passed / 10 skipped，exit0，1027.70秒；日志E:/ReproAgent/.local/holdout-five-20261015/generator-full.log。

### 新Agent轮次

E:/ReproAgent/repro-results/generator-replay-5221-20261016：本地DONE / REPEATED_OBSERVATION / fix_validation_status=failed，结束剩7步；99.765秒、16次HTTP、204,441 tokens。官方exit0，119.584秒，resolved=false，added_f2p=0，唯一候选测试FAIL_TO_FAIL。预测hash076c7b35131e8eef1a1ab9aa09a051e3f357c09fd1453f58a1e1817b19ab9a6d。

这次新候选使用fnmatch_lines，要求默认func_fixture同行出现function，再匹配session fixture；默认scope这一要求在修复版仍不成立。同一个测试中的前一断言先失败，后一场景无法独立贡献差分。仅凭这点不能断言是数据集错误：候选可能过度规定显示格式，也可能覆盖了参考修复未解决的需求面，应继续按原版依据界定边界。不能按参考修复改写候选让分数通过。

本轮没有触发generator断言显示，因此不能把DONE进度变化归因generator修复。本地DONE仅证明重复观察，证据等级和修复版失败已如实保留，不等于差分验收通过。

### 历史生成器候选的机械重放（0模型调用）

E:/ReproAgent/repro-results/historical-generator-5221-replay-20261016/receipt.json。

复用旧Agent生成且未改字节的candidate-bbd8ea3197574c88b5e4be3e030bf1ec，manifest hash9ad2da3f1868615e0c513bd36b99d7e64840781ca5fba62558ae9f94fdf2d559。用原冻结Python3.9 / pytest4.4环境，在新任务输出目录真实执行两次；两个original run均完整退出、保护/清理/探针检查正常。

原始签名不一致；仅加探针实际观测ID的地址归一化后签名一致。旧task.json前后hash相同，旧候选和快照每文件hash重新校验。没有从旧显示地址反填ID，没有新语义审阅、没有复用旧语义批准当新验收；receipt明确agent_success=false、fresh_semantic_review=false、official_score_effect=none。它证明机械修复生效，不把历史失败追认成新的Agent成功。

## 本次成本与分支

三个真实Agent轮次合计173次DeepSeek HTTP尝试、3,650,760 tokens；这是调用量总和，不是跨版本成功率。金额因价格配置为空而未知。官方判分、两类历史诊断和离线测试不调用模型。独立代码审查属于Codex工作流，不在这个产品调用量内。

所有实现位于codex/holdout-execution-reliability隔离分支；主工作区其他代理改动未覆盖。本次尚未合并或push。


## 最终交付校验

当前源指纹再次核对与generator-replay-5221-20261016一致。最终全量1362通过、10跳过、无失败；真实SDK→HTTP送达回归已纳入最终全量。generator、二元比较、业务值、private-temp、真实旧pytest环境重放均检查。实现经过独立非Astra审查，没有降低独立语义门禁，没有增加模型预算，没有修改受评候选或官方计分规则。

本次实跑尚未证明8365跨平台能力与8474缺信息场景已解决，也未证明5221候选质量已稳定；不能称所有五例修好。可证实的新成功仅是8906独立单例；8627历史环境修复与5221历史机械重放均属于诊断证据。
