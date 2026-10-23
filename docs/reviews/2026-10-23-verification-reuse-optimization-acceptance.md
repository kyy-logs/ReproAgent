# 独立重跑与语义审查复用验收

日期：2026-10-23。工作区：`C:/Users/Dell/.codex/worktrees/reproduce-bug-skill/ReproAgent`；分支：`codex/reproduce-bug-skill`。本次修改尚未提交、push 或合并。

## 问题与实现

### 重跑因非业务耗时差异被误拒

pytester 的 LineMatcher 失败消息包含子 pytest 汇总，例如 `1 passed in 0.06 seconds`。同一候选第二次运行变成 `0.08 seconds`，此前会让重跑失败签名不一致。

- 新探针为异常帧保存 `function`；只有来源路径与 `_pytest.pytester` 实际导入路径一致、函数为 `fnmatch_lines`、异常和消息结构均被确认时，才识别汇总耗时。
- 只归一化失败消息内严格格式的 `N passed in X seconds` 中的 X；测试数量、状态、待匹配模式和其他文本保留。
- 若期待模式可能匹配该汇总（包括数字、通配符或字符类），则保持严格比较。涉及 passed/failed/seconds/duration 等语义也保持严格。
- regex matcher、旧探针没有函数信息、来源不明或形状不符时不归一化。原始证据文件不改写。
- trace 通过 `replay.metadata_normalized` 明确记录归一化发生的字段与规则。

### 第二次独立执行重复语义审查

首次真正通过的语义审查可以在同一 Verifier 实例内，为紧邻的另一次原版独立执行复用一次。

- 两次原版执行仍然真实发生。第二次仍运行完整性、源码保护、候选哈希、环境绑定、证据和引用检查。
- 复用键覆盖完整已校验审查材料、契约、候选、快照、环境、退出结果、提示词、有效模型配置、脱敏与容量策略。
- 材料只忽略经过校验的本次 run_id 和运行目录身份。语义复用不忽略耗时文本、调用次数、断言值或其他业务内容；这些不同都会回退到完整审查。
- 只缓存首次请求直接批准的结果；纠错后批准、负向结论、未知证据、带额外前置条件的候选不缓存。复用结果不再形成新的复用链。
- 旧引用和重绑定后的当前引用都重新校验，并要求覆盖预期依据与本次失败。
- 审计写入失败则回退审查；写入前后均检查取消和预算。
- 持久事件与 trace 记录 `verification.semantic_reused`，包含首次与当前 run_id、材料键和依据 verdict 哈希；`review_origin=reused_provider_claim`、`new_model_call=false`，不伪装成第二次模型独立批准。
- Controller 的独立重跑、修复版验证、完成门禁和权限未削弱。本次不修改模型重试上限，不新增工具。

## 修改文件

- `src/reproagent/core/failure_signature.py`：有来源与模式保护的 pytester 汇总耗时归一化。
- `src/reproagent/adapters/languages/python_pytest/probe/reproagent_pytest_probe.py`：异常帧函数名。
- `src/reproagent/core/semantic_reuse.py`：完整材料和策略指纹、批准记录及引用重绑定。
- `src/reproagent/core/verifier.py`：一次复用、硬检查、审计与保守回退。
- `tests/unit/test_replay_summary.py`、`test_semantic_reuse.py`：边界与反例。
- `tests/unit/test_controller.py`：继续断言两次执行，符合条件时只发一次语义审查请求。

分支同时保留此前 Skill 接入改动；其提示词加载方式是另一个变更的范围，不计作本次验收优化。

## 验证

- 首轮新增测试：9 failed / 8 passed，失败覆盖未实现的归一化与重复语义请求。
- 独立只读审查发现数字型预期误归一化、纠错批准错误缓存、包装器内部模型配置遗漏三处问题；补充反例后先看到失败，再修复。复审未发现新的可证缺陷。
- 定向最终测试：`test_replay_summary.py` 与 `test_semantic_reuse.py`，**32 passed in 47.66s**。
- 相关回归曾为 118 passed / 1 failed；失败来自测试把候选 selector 改成原值的夹具错误，已改为确有变化的 node selector，定向测试确认通过。产品代码没有为迁就该夹具修改。
- 最终整组相关回归结果见下方补充记录。
- pytest 使用本分支源码和仓库外 basetemp；MockTransport 测试没有真实模型调用。

覆盖包括：一次语义请求与两次独立执行、当前引用重绑定、负向与纠错不复用、旧证据篡改、同 run 不复用、契约/候选/环境/观测变化、真实包装器内部策略变化、审计失败回退及取消/超时传播。

## 公开旧候选的程序重放

未改写 #5227 已保存的首个模型候选，使用原版公开仓库与新探针，在新输出目录独立执行两次；没有调用模型，也未读取或执行金标测试/参考修复。

回执：`E:/ReproAgent/repro-results/verification-opt-5227-program-20261023-233742/program-replay.json`。

- 两次退出码均为 1，探针完整，源码保护通过。
- 子进程汇总分别 `1 passed in 0.06 seconds` / `1 passed in 0.05 seconds`。
- 已确认真实 `fnmatch_lines` 来源；归一化后的失败消息相同。
- model_calls=0，fixed_runs=0。

这证明了真实候选中的耗时漂移可以按新规则识别；不是新的语义批准、差分复现、官方判分或 A/B 实验。当前没有证明真实成功率提高或总体 token 节省，严格复用也不保证每例都命中。

## 最终整组回归

修正 selector 夹具后，重新完整运行同一组相关测试：**119 passed in 236.77s，exit 0**。

范围：`test_replay_summary.py`、`test_semantic_reuse.py`、`test_verifier.py`、`test_verifier_issue_branches.py`、`test_failure_signature.py`、`test_controller.py`、`test_agentscope_explorer.py`、`test_reproduction_skill_delivery.py`、`test_reproduction_skill_package.py`。`git diff --check` 通过；独立导入确认 Verifier 与 semantic_reuse 来自上述隔离工作区。
