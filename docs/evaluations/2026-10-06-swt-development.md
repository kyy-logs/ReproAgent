# ReproAgent 真实 Bug 评测：2026-10-06

第三轮在固定 20 例中，本地差分确认 **0/20**，交付包在全新原版和修复版中独立差分重跑成功 **0/20**。
20 例中 8 例实际调用模型，12 例环境阻塞。在参考对照可以区分原版与修复版的 5 例中，有效交付为 **0/5**；当前配置的完整自动交付流程还不可靠。
这是开发诊断结果；官方 SWT-Bench Docker 判分尚未运行，不能作为全量榜单成绩。人工语义审查仍待完成。

## 本次如何测

- 使用固定 SWE-bench Lite 数据快照和同一 20 例开发清单，原始 Issue 不改写。
- 生成阶段只看到公开原版代码与 Issue；数据集修复补丁、参考测试和提示不进入生成上下文。
- 原版与修复版分开验证；交付包还在新副本里独立重跑。
- 每轮完整保留 20 例，包括未准备、环境阻塞、超时和失败；不将多轮最佳结果合并。
- 模型 `deepseek-flash`，模型/Agent 后端均 `native`，输出上限 4096 token。
- 每例 20 个动作，单命令 60 秒，任务 900 秒。
- 同一公开数据已参与本项目诊断，样本偏向五个仓库；结果不代表新 Bug 的一般成功率。

第三轮运行期间，工作区另出现 CI、开发依赖与 venv 解释器路径处理的并行变更。本评测没有覆盖这些变更；本轮视作保留原始记录的本地开发诊断。正式对比前还需冻结干净的代码提交和依赖。

## 数字怎么看

| 指标 | 第二轮 | 第三轮 |
| --- | --- | --- |
| 固定样本 | 20 | 20 |
| 通过导入预检 | 8 | 8 |
| 实际有模型调用 | 2 | 8 |
| Agent 标记 DONE | 1 | 1 |
| 本地差分确认 | 0 | 0 |
| 独立差分重跑成功 | 0 | 0 |
| HTTP 尝试 | 41 | 165 |
| 记录 token | 305843 | 1475156 |
| 官方判分 | 未运行 | 未运行 |
| 人工审查 | 待完成 | 待完成 |

费用未配置费率，仍为 unknown。汇总 JSON 中 official_total_rate=0 只是尚未完成判分时的暂定下界，不能解释为正式 0% 成绩。

实际调用模型样本的中位耗时：122.1 秒（含耗尽预算样本）。

## 环境参考对照

在独立副本里应用数据集参考测试，仅用于评估环境，不交给 Agent。对照使用当前第三轮目标解释器和依赖。

| 样本 | 参考测试原版退出码 | 修复版退出码 | 区分原版与修复版 |
| --- | --- | --- | --- |
| pallets__flask-4992 | 1 | 0 | 是 |
| pallets__flask-5063 | 1 | 0 | 是 |
| sphinx-doc__sphinx-10325 | 1 | 1 | 否 |
| sphinx-doc__sphinx-8435 | 1 | 1 | 否 |
| sphinx-doc__sphinx-8506 | 1 | 0 | 是 |
| sphinx-doc__sphinx-8595 | 1 | 0 | 是 |
| sphinx-doc__sphinx-8721 | 0 | 0 | 否 |
| sphinx-doc__sphinx-8801 | 1 | 0 | 是 |

8 例通过基础导入预检，其中 5 例的参考测试在本次对照中呈现原版失败/修复版通过。在这 5 例中，Agent 独立差分重跑成功 0/5。这是环境对照诊断，完整开发清单仍为 20 例。
其余三例分别出现 Sphinx 扩展导入错误、类型注解输出断言不匹配、原版与修复版均通过。依赖或平台原因还需逐例核对，不能把它们的生成失败全部归因于 Agent。

## 逐例结果

| 样本 | 准备 | 最终状态 | 候选数 / 原版执行数 | 证据等级 | 独立差分重跑 |
| --- | --- | --- | --- | --- | --- |
| pallets__flask-4992 | ready | DONE | 1 / 2 | REPEATED_OBSERVATION | 未通过 |
| pallets__flask-5063 | ready | EXHAUSTED | 4 / 3 | NONE | 未交付测试 |
| psf__requests-3362 | blocked | NOT_PREPARED | 0 / 0 | NONE | 未交付测试 |
| pytest-dev__pytest-5413 | blocked | NOT_PREPARED | 0 / 0 | NONE | 未交付测试 |
| pytest-dev__pytest-5495 | blocked | NOT_PREPARED | 0 / 0 | NONE | 未交付测试 |
| pytest-dev__pytest-5692 | blocked | NOT_PREPARED | 0 / 0 | NONE | 未交付测试 |
| pytest-dev__pytest-7168 | blocked | NOT_PREPARED | 0 / 0 | NONE | 未交付测试 |
| pytest-dev__pytest-7373 | blocked | NOT_PREPARED | 0 / 0 | NONE | 未交付测试 |
| pytest-dev__pytest-7432 | blocked | NOT_PREPARED | 0 / 0 | NONE | 未交付测试 |
| sphinx-doc__sphinx-10325 | ready | EXHAUSTED | 0 / 0 | NONE | 未交付测试 |
| sphinx-doc__sphinx-8435 | ready | EXHAUSTED | 0 / 0 | NONE | 未交付测试 |
| sphinx-doc__sphinx-8506 | ready | EXHAUSTED | 1 / 0 | NONE | 未交付测试 |
| sphinx-doc__sphinx-8595 | ready | EXHAUSTED | 1 / 0 | NONE | 未交付测试 |
| sphinx-doc__sphinx-8721 | ready | EXHAUSTED | 0 / 0 | NONE | 未交付测试 |
| sphinx-doc__sphinx-8801 | ready | EXHAUSTED | 2 / 1 | NONE | 未交付测试 |
| sympy__sympy-15308 | blocked | NOT_PREPARED | 0 / 0 | NONE | 未交付测试 |
| sympy__sympy-18698 | blocked | NOT_PREPARED | 0 / 0 | NONE | 未交付测试 |
| sympy__sympy-19254 | blocked | NOT_PREPARED | 0 / 0 | NONE | 未交付测试 |
| sympy__sympy-20154 | blocked | NOT_PREPARED | 0 / 0 | NONE | 未交付测试 |
| sympy__sympy-21055 | blocked | NOT_PREPARED | 0 / 0 | NONE | 未交付测试 |

## 已确认的问题与修正边界

1. 第二轮 6 例 Sphinx 在快照阶段触发 Windows 路径长度限制，0 次模型调用。第三轮给输出路径使用 Windows 长路径前缀，未修改 Agent 代码。
2. 旧 Sphinx 环境缺 pkg_resources，浮动安装的最新扩展要求 Sphinx 5。第三轮新建独立环境并固定兼容依赖；原环境与旧轮次不改写。但不同 Sphinx commit 仍需要不同依赖配置。
3. 第二轮 Flask-4992 从 Issue 提案采用 mode="b"，原版和修复版都失败；DONE/REPEATED_OBSERVATION 不计为有效差分复现。
4. 第二轮 Flask-5063 的候选已执行，但语义核验连续遇到截断、空或不完整模型响应，最终 EXHAUSTED。需分别验证模型输出配置与核验协议。
5. 12 例未准备：旧 Requests/Python 不兼容、SymPy 旧标准库依赖缺失、pytest checkout 缺构建生成文件。目标源码未为兼容而修改。
6. 参考对照的首个临时脚本被外层 Git 忽略规则影响，补丁静默未应用。已改为每个副本独立 Git 并保留旧记录；controls-002 不用于结论。

第三轮 Sphinx-8721、8435、10325 未发布候选；8506、8595 已发布候选但未执行，均达到动作预算。除核验外，探索和候选执行的效率也需要修正。现有事件日志不足以精确重构每一次无效动作，不能将这些案例都归因于响应截断。

## 候选生成与最终交付要分开判断

第三轮 Sphinx-8801 未被接受的两个候选，在全新原版/修复版副本中均为退出码 1/0。测试真实构建 Sphinx 文档，检查继承来的只有类型注解的成员及其注释是否出现在输出中。
但 Agent 的语义核验报 semantic evidence exceeds bounded context，任务最终 EXHAUSTED，没有交付 accepted 复现包。这说明至少此例能生成有差分效果的测试，完整核验与交付链路仍不可靠。
执行记录中的 observation 约 35,079 字节，仅 target_origins 的绝对路径映射占 26,309 字节。完整 payload 还含契约与测试，超过 32,768 字节限制。来源完整性可以在确定性检查中保留，模型核验上下文需要更紧凑的证据表示。
该诊断读取全部两个已有候选，未用参考测试改写候选；结果仅用于定位，不加入本地交付或 SWT 成功数。证据为 `.local/swt-bench/candidate-diagnostics-003/verification.json` 及逐候选原版/修复版日志。

## 判断与下一步

先按独立差分重跑结果判断当前配置是否能交付，不按 DONE 数或离线测试数判断能力。即便有成功样本，也仍需人工检查测试断言与 Issue 的对应关系。
后续应先让核验证据紧凑且仍可引用、解决模型核验响应失败，并让动作预算覆盖候选执行；固定逐例依赖后，再用另一份未参与调试的冻结清单评测。官方 SWT 判分需要可用的 Linux/Docker 环境；本机没有可用 Docker 或 WSL 发行版。

## 证据位置

- 第二轮：`repro-results/swt-bench/dev20-native-002/round.json`、`summary.json`、`export-replay.json`。
- 第三轮：`repro-results/dev20-native-003/round.json`、`summary.json`、`export-replay.json`。
- 第三轮绑定：`.local/swt-bench/bindings-eval003.json`，目标环境：`.local/swt-bench/envs/sphinx-eval003`。
- 第三轮环境参考对照：`.local/swt-bench/reference-controls-005/verification.json` 与逐例 stdout/stderr。
- 每例输入、契约、模型事件、测试、重复/修复版记录和用户报告位于各轮 `cases/<instance_id>/task`。
- 固定数据、来源哈希、筛选与复现命令见 [SWT 评测说明](../swt-bench.md)。

[公开数据集](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite) · [固定 SWT harness](https://github.com/logic-star-ai/swt-bench/tree/330a649a764fab2fadaea632776eeae87272f74b)
