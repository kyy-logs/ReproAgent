# SymPy 15678：004 单例官方结果复核

日期：2026-10-15。轮次：holdout-diag-004-15678。本次复核已有运行记录，没有重新调用模型、运行候选、执行官方判分、改写补丁或读取参考修复源码。

## 已确认结果

004 单例官方结果为 **resolved=true、added_f2p=1**，官方状态 final，有判决 1 例、成功 1 例。它证明 f(x) 场景产出了被本轮固定 harness 计入的差分测试，不等于 Issue 的所有场景均完成。

实际 tool_source.git_commit 是 **aac87160e22f7c44cf9d0bd0b6d7f5d2d029e807**，包含代码修复 161086d；不能把代码提交简称当成本轮记录的 HEAD。该记录另有 git_dirty_count=7 和逐文件哈希，精确工具来源以完整 tool_source 为准，source_hash 为 f1af0d343d8f22f479c35db20815e11a48e906685ac911c2e85c1c93ccd5785f。

| 项目 | 实际记录 |
| --- | --- |
| 本地 | DONE / REPEATED_OBSERVATION |
| 本地修复版检查 | failed；local_differential=0 |
| 成功发布候选 | 2；第一份原版通过，被判 NOT_REPRODUCED；第二份被重复确认 |
| 接受的候选 | candidate-5244766d4ba34bb08a29ae59f9772769 |
| 最终候选本地执行 | 原版两次、修复版一次 |
| 探索调用 | 7 次，结束时剩余 13 步 |
| 本地耗时 | 208.812 秒 |
| 模型使用量 | 11 次 HTTP 尝试、73,569 tokens；不据此推算未知费用 |
| 官方进程 | exit 0；72.822 秒 |
| 官方 harness | 330a649a764fab2fadaea632776eeae87272f74b |

## 按测试节点区分结果

| 节点 | 本地原版（两次） | 本地修复版 | 官方 tests_pred |
| --- | --- | --- | --- |
| test_idiff_function_dependent | ValueError / 失败 | 通过 | FAIL_TO_PASS |
| test_idiff_symbol_control | 通过 | 通过 | PASS_TO_PASS |
| test_idiff_eq | IndexError / 失败 | IndexError / 失败 | UNMATCHED |

官方 tests_pred 的 FAIL_TO_FAIL 和 PASS_TO_FAIL 均为空。本地 Eq 两侧失败的观察，不应擅自改写成官方 FAIL_TO_FAIL：官方提供的分类是 UNMATCHED。

本地整体差分要求候选全部测试在修复版通过，本例因 Eq 仍失败而未满足。官方规则将 f(x) 的 FAIL_TO_PASS 计入成功。两者回答的问题不同：官方结果决定本轮 SWT 得分，本地结果仍然准确说明完整候选没有全部通过修复版。不能以官方成功覆盖或删除本地失败。

## 这轮走通了哪一段

003 初始契约将“缺少确切表达式”列入 missing_information，第 7 步发布被拒，第 8 步请求信息后结束。

004 只有契约 v1，**初始 missing_information=[]**；有关 f(x) 返回形式的非必要细节放在 assumptions，允许使用符号形式替换后的等价关系。模型随后发布候选，第一次原版通过后又生成第二份，最终完成原版重复确认。

004 **没有 GROUNDED_CONTRACT_REQUIRED 拒绝，也没有 revise_contract 调用**。因此本轮观察支持“没有再次被初始契约误阻塞，能生成并验证候选”，不能写成“真实验证了拒绝后修订契约的恢复路径”。后者目前仍主要由离线 SDK 链路回归覆盖。

一次样本也不能把改进完全归因给某一句提示词或拒绝反馈；本轮未触发的新拒绝反馈尤其不能被算作已验证的收益。

## Eq 不能仅据参考修复结果判为过度断言

原始 Issue 明确要求 idiff 支持 Eq 和 f(x)，分别给出了触发输入。因此，“Eq 在修复版仍失败”不自动证明“测试要求超出 Issue”。更准确的当前结论是：**Eq 属于报告明确要求但本地修复版仍失败、且本轮官方未计入 F2P 的场景**。

如要判定某条具体断言过度约束，仍应检查它是否添加了没有来源的打印形式、内部结构或数学关系。不能用隐藏参考修复的实际覆盖范围倒推用户期望，也不能为了提高分数删掉 Issue 中尚未解决的场景。本次没有读取参考修复源码，所以不据结果反推补丁具体改了哪些分支。

## 不跨版本合并分母

| 轮次 | 官方状态 | 已判 / 选定 | 成功 | 工具提交 |
| --- | --- | --- | --- | --- |
| holdout-diag-003 | pending | 9 / 10 | 4 | 0c17d53 |
| holdout-diag-004-15678 | final | 1 / 1 | 1 | aac8716（含 161086d） |

可报告“003 的 9 个已判实例中成功 4 个；15678 在更新工具版本的 004 单例轮独立通过”。不能把这两轮拼成同版本的 5/10，也不改变 003 的 pending 状态或其缺失判决。

## 本次独立复核范围

- WSL 原始 report.json 的 SHA-256 为 b073520baaa353661fbb50a2a52fed894ece08eef2d4cfcd620080c89ee62703，与官方执行回执一致；原始报告和 Windows 导入副本的 JSON 值完全相同。
- 预测文件字节哈希与执行回执、round.json 一致，manifest_hash 关联一致。导入报告会被规范化重写，不能直接把其重排后的字节哈希与原件哈希比较。
- 核对了本地 task、契约、事件和最终候选三次执行的 call 阶段节点结果，未重新执行或修改记录。
- 机器核验脚本为 .local/15678-004-verification/verify.py，回执为同目录 facts.json；不会重跑模型或判分。

资料：[单例轮汇总](E:/ReproAgent/repro-results/holdout-diag-004-15678/report.md)、[官方报告](E:/ReproAgent/repro-results/holdout-diag-004-15678/official/sympy__sympy-15678/b073520baaa353661fbb50a2a52fed894ece08eef2d4cfcd620080c89ee62703.json)、[官方回执](E:/ReproAgent/repro-results/holdout-diag-004-15678/official-execution.receipt.json)、[本地事件](E:/ReproAgent/repro-results/holdout-diag-004-15678/cases/sympy__sympy-15678/task/events.jsonl)。

## 后续顺序建议

先检查 8365 的本地与官方执行平台约束是否完整送达模型，以及目标节点是否实际执行；不要一律禁止合法的 skipif。随后分别诊断 8906、5221、8474 的耗尽原因，不能仅凭相同终态将其归为同一问题。任何产品修改和新评分仍需新轮次、独立输出目录与完整来源记录。
