# 附加断言精度审查修复与验收

日期：2026-10-17。工作树：`C:/Users/Dell/.codex/worktrees/holdout-execution-reliability/ReproAgent`，分支 `codex/holdout-execution-reliability`，基线 `f659551`，包含前一轮未提交修复。本次没有合并、push，也没有更改主检出中其它代理的修改。

## 问题与本次范围

独立验证 `verify-8474-20261017` 的原版运行在警告断言失败，后续 LaTeX 精确输出断言未被验证；修复版警告消失，附加格式断言却因连字符转义不同而失败。两次相同的原版失败不证明全部断言正确。

现有程序已经有逐断言清单、逐项引用与 oracle quote 校验，因此本次复用原有审查协议，不另建评判器。修改五个产品文件：

- `core/assertion_audit.py`：为每个断言加入程序观测标记。
- `core/oracle_audit.py`：为普通字符串比较加入独立精度问题；约束新解析器的资源展开。
- `core/review_context.py`：把程序观测和新审查问题送入真实验收输入。
- `prompts/explore.md`：生成时区分必需行为与附加显示细节。
- `prompts/review_evidence.md`：逐项核查精确表示在当前输入、builder、配置下的必要性与来源。

新增 `tests/unit/test_assertion_precision.py`、`tests/integration/test_assertion_precision_wire.py`。

未修改完成门禁、模型调用预算、验收容量、候选源码、历史 verdict、官方计分规则。修复版和金标仍不进入探索上下文，没有根据修复版输出手改候选。

## 程序保证与语义边界

`assertion_sites[*].execution_observation`：

- `failure_location_overlap`：失败 phase 的 frame/crash 行号与唯一断言行范围相交，`phase_indexes` 指向 `observation.tests`。只证明位置相交，连断言入口、操作数求值或比较执行都不保证；同一行前面的普通语句也可能先失败。
- `not_established`：没有上述唯一位置证据。不能解释成已通过、被跳过或必定未执行。缺帧、其它路径、同一行多断言均保持此状态。

新 `literal_comparison` 问题包含字符串、操作符、位置与精度问题，覆盖 `==` / `!=` / `in` / `not in`、简单字面量绑定和拼接。每个问题必须得到现有 `oracle_reviews` 中的独立回答；漏项不能靠全局肯定绕过，明确否定返回 `CANDIDATE_ORACLES_UNCONFIRMED`。支持回答继续要求原版期望来源、合法引用及确实存在于所引范围的 quote。

程序不证明约束的语义正确性或必要性。审查模型仍可能误判；本次不能承诺真实模型一定拒绝 8474 的旧格式断言。扫描也不是任意 Python 的完备分析，精确输出本身不是自动拒绝条件。

新字面量解析采用缓存，最多 512 次新节点解析、32 层深度、每节点 16 个字符串值，中间字符串最多保留 241 字符后才继续拼接。超出解析限制或展示截断时提供 `analysis_unknown`。这些限制仅约束本次新增解析，不代表整个既有扫描器具有固定耗时上限。

## 已执行的回归

- `red.xml`：初始 11 失败、2 通过，确认新问题及程序观测缺失。
- `protocol-red.xml`：修正测试夹具多余字段后，两项“全局批准不能绕过精度审查”均按预期失败。
- `first-green.xml`：51 项通过。
- `ambiguity-red.xml`：同一行多断言的歧义反例先失败，后修正。
- `wire-green.xml`：38 项通过，包含实际 SDK 请求和控制器恢复链路。
- `review-red.xml`：独立审查反例 2 失败、1 通过；使用小规模输入验证展开上限，未制造 GiB 级分配。
- `final-targeted.xml`：58 项通过，54.82 秒。包括反例、扫描器、验收协议、逐断言审查与实际 SDK 反馈。

以上测试存在交集，数量不能相加当作唯一总数。脚本审查器的测试证明协议和控制流，不证明真实模型的判断质量。

## 真实 8474 原版记录的零调用回放

原始来源：`E:/ReproAgent/repro-results/verify-8474-20261017/cases/sphinx-doc__sphinx-8474/task`。

新材料 `8474-original-review-payload.json` 中：

| 断言 | 行号 | 程序观测 | 新精度问题 |
| --- | --- | --- | --- |
| A1：latex 警告 | 9 | failure_location_overlap | O1 |
| A2：精确 LaTeX 引用格式 | 12–13 | not_established | O3 |
| A3：singlehtml 警告 | 21 | failure_location_overlap | O5 |

最终投影 34,562 B / 65,536 B。新增模型调用 0、候选执行 0；未使用修复版运行作为审查材料，历史输入哈希不变。回放不会重新裁定旧候选、修改旧任务状态或制造官方分数，旧任务仍为 DONE / REPEATED_OBSERVATION / fix_validation failed。

## 独立审查

只读审查提出两项 P2：字面量递归可能放大内存、行号相交不证明断言进入。两项均落实反例及修复，复审未发现新增重要问题。复审明确：逐项引用检查是程序保证，必要性和跨场景等价仍是模型判断。

## 全量与最终状态

完整串行全量已结束：收集并执行 1,515 项，1,505 通过、10 跳过、0 失败、0 错误，exit 0，用时 1,823.516 秒。`full-suite/summary.json` 的 complete=true、passed_all_checks=true；产品源码 source_unchanged=true，测试源码 tests_source_unchanged=true。完整回执为 `full-suite/summary.json` 与 `full-suite/all.xml`。10 项跳过来自宿主 Windows 符号链接权限或当前解释器没有通过链接启动，没有为本次修复新增跳过条件。

没有新增真实模型运行、没有官方判分、没有使用 Astra 验收。尚未提交、合并或 push。
