# Holdout 003：SymPy 15678 无候选诊断

日期：2026-10-15。检查的产品提交：003 为 `0c17d53`，002 为 `cf23ae0`。本次只检查已有记录和原版源码，没有修改产品、候选或评测结果，没有调用模型或重跑官方评测。

## 结论

003 的停点是初始契约中的预期行为阻塞项，不是步数耗尽或运行环境失败。

初始分析将“两种输入没有给出确切返回表达式”列入 `missing_information`。探索模型仍尝试提交包含三个测试的草稿，但程序按当前契约拒绝发布。下一轮模型请求补充信息，Controller 按该动作结束任务。

“没有候选”准确指没有成功发布的候选，不代表模型没有生成测试。它在第 7 次探索调用已经生成过草稿。

## 事实链

| 阶段 | 已核对事实 | 位置 |
| --- | --- | --- |
| 输入 | 002/003 的 Issue 字节完全相同；原版 util.py 内容哈希相同 | 两轮 task/input/issue.md；各轮首次 snapshot.created 对应原版清单 |
| 初始分析 | 003 契约 v1 同时写了“支持 Eq/f(x)”与“缺少确切表达式，无法判断正确” | contracts/contract-c1e889b546e74dacaab4a5fe8faeb9ff-v1.json |
| 第 1 步 | MULTIPLE_TOOL_CALLS，协议纠错耗一步 | events.jsonl seq 13 |
| 第 2、3 步 | 先用相对路径，再用错误的 /sympy/... 绝对路径，Read 均失败 | trace.json 的 SDK 工具输入与返回 |
| 第 4、5、6 步 | Glob 找到快照文件、Read 原版 idiff、Grep 原版测试 | events.jsonl seq 22/25/28 |
| 第 7 步 | write_candidate 被 GROUNDED_CONTRACT_REQUIRED 拒绝；剩余 13 步 | events.jsonl seq 31/32；trace content 5c0e8c9889008937 / 03e6f94b36378dbe |
| 第 8 步 | request_information；剩余 12 步 | events.jsonl seq 35—38 |
| 最终 | NEEDS_INFORMATION / MISSING_INFORMATION / NONE；发布 0、执行 0、契约修订 0 | task.json、events.jsonl |

本地总时长 47.485 秒。环境预检成功，候选没有进入 Runner，因此本例的本地停点不可能是候选命令超时。官方 pre 臂 1800 秒超时属于另一个阶段，不能用来解释这次本地退出。

## 程序与模型各自做了什么

1. **契约模型**产出 `missing_information`。这是模型对“哪些信息必不可少”的语义判断。
2. **CandidateService.publish** 确定性检查：expected 非空、sources 非空、missing_information 为空。最后一项不满足，就拒绝发布。
3. **工具反馈**返回 `missing=["missing_information"]`，建议用原版证据修订契约；信息确实不可得时再 request_information。
4. **探索模型**选择直接请求补充信息，没有调用 revise_contract。
5. **Controller** 收到 request_information 后，确定性转入 NEEDS_INFORMATION 并退出。

程序没有独立判定“确切数学表达式是否必要”，也没有把模型声明完成当成完成。闸门按现有契约工作正常；问题在契约语义判断与后续恢复路径。

拒绝信息确实出现在下一次实际 wire 请求保留下来的片段里；该次 wire 正文其余部分有摘录标记，不能宣称全量保留。相应 SDK 输入完整保留。这不是“工具反馈丢失，模型没有收到”的案例。

## 为什么需要复核这个阻塞项

原始 Issue 不只是说“崩溃”，还明确要求支持 Eq 和 f(x)，并给出了符号形式的输入与结果。探索已阅读的原版 util.py 第 528—559 行进一步说明 idiff 的语义是对方程求隐函数导数。

因此，“未逐字列出两种返回表达式”不自动等于“无法建立正确性判断”。Eq 形式和原版已工作的 lhs-rhs 形式可以建立等价关系；f(x) 场景需要正确替换因变量并检查导数语义，不能直接照搬含 y 的结果。这里应让契约分析/修订明确哪些关系由 Issue 和原版语义支持，而不是自动清空 blocker，也不是只检查函数没有抛异常。

003 未发布草稿实际上已经尝试使用这种关系：Eq 返回值与 lhs-rhs 调用对比；f(x) 返回值与符号结果作替换后对比。但这些关系尚未通过契约修订确认，不能跳过闸门。

这也解释了为什么“加到 30 步”不是这条记录的直接修法：退出时尚有 12 步，没有用完预算。

## 草稿还有一个被遮住的问题

未发布草稿的 test_idiff_function_form 定义了 x 和 f，却在 expected 计算中使用了未定义的 y：

```python
expected = idiff(y*exp(y) - x*exp(x), y, x).subs(Symbol('y'), f(x))
```

模块没有定义或导入 y；其他测试函数中的局部 y 不会进入这个函数。Python AST 与符号表检查确认了这个问题。它还没有成为实际运行异常，因为草稿未发布，而且原版目标调用本身会先抛 ValueError；如果目标调用在修复版成功，后续计算可能因 NameError 失败。

不能手改这个基准候选再替 Agent 计分，也不能把“允许发布”当成“能通过官方”。

## 002 不是应该原样恢复的答案

002 的契约 missing_information=[]，但 observable_checks 将 f(x) 场景也写成了与含 y 的符号结果完全相同的表达式；assumptions 又承认该场景期望不明确。其本地 fix_validation_status=failed，仅达到 REPEATED_OBSERVATION。旧的官方成功不能证明旧契约的每个场景都正确。

002 与 003 的初始分析提示词和探索提示词都有变更，模型输出也不同。一次运行只能定位这次的阻塞链，不能独立证明某一句新提示导致回退，或某一句提示已被普遍证明无效。需要保留真实 blocker 与可推导关系两组回归，并用新轮次检验。

## 建议的最小修复目标

- 预期行为分析区分“必须逐字给出的常量/接口”和“可由来源验证的语义关系”，并明确关系的依据及尚未确认的部分。不要按案例编号打补丁。
- 保留 grounded-contract 闸门。存在真实歧义时仍然阻塞；已有原版依据时应能经 revise_contract 修订，不能由程序删除字符串式缺口。
- 把这份草稿的未定义变量作为测试生成质量的反例，验收必须覆盖目标调用之后的断言计算；不替换本轮候选或分数。
- 先只重跑 15678；本地候选成功发布、两个独立场景分别执行并完成验收后，再进入新的官方评测。没有必要立即再花一轮全十例费用。

这些是诊断后的建议，本轮尚未实现任何产品修复。

## 总分边界与材料

003 summary.json 核对：official_successes=4、official_graded=9、official_not_verified=1、official_grade_status=pending、official_rate=null、official_total_rate=0.4。当前可以说“确认成功数仍为 4”，不能写成最终官方率。

本次机读核验回执位于 `.local/15678-003-investigation/facts.json`，检查脚本为同目录 validate_facts.py；详细摘录与提示词差异为 evidence.txt、details.txt。回执只核对记录和静态代码事实，不生成候选、不执行目标测试，也不更新官方分数。
