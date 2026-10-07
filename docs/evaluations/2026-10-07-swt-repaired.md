# ReproAgent 真实 Bug 评测：2026-10-07（修复后轮次）

冻结实现版本后，先用原 dev20 复跑修复后的 Agent，再用同一 catalog 冻结的 10 例未参与本项目调试的新样本评测。
dev20 修复轮：20 例中 8 例准备可用并实际调用模型，本地差分确认 **0/8**，来源状态 verified。
新样本轮：10 例中 3 例准备可用并执行，其中 `sphinx-doc__sphinx-11445` 达到 **DIFFERENTIAL_VALIDATED**，导出包在全新原版/修复版副本独立重跑为 **1/0**。
计划中命名的已知定点 Sphinx-8801 **本轮仍未达到** DIFFERENTIAL_VALIDATED；报告给出定位到的两个原因，均为记录，不是修复。
两轮合计覆盖 30 个真实样本（20 + 10），其中实际调用模型 11 例（8 + 3）；168 次 HTTP 尝试、1338670 token，费用 unknown，人工判断待完成。
这是小样本诊断；官方 SWT-Bench Docker 判分尚未运行，任何本地比例都不是官方成绩，也不能推算一般复现率。

## 冻结配置

本轮冻结回执：`.local/swt-bench/task8-freeze.json`。两轮共用同一冻结配置，逐例失败不替换。

- 实现提交 `372a6b7`（worktree `E:/ReproAgent/.superpowers/worktrees/evaluation-repairs`），实际导入路径记入回执。
- 模型 `examples/model.deepseek.non-thinking.json`，sha256 `325491f4…`；`deepseek-flash`，`max_output_tokens` 4096，输出字段 `max_tokens`，`thinking_mode: disabled`。
- 模型后端与 Agent 后端均为 native。
- 预算为 BudgetLimits 默认：每例 20 动作、单命令 60 秒、任务 900 秒、单次工具响应 32768 字节。
- 未配置计价费率，因此每一例费用都是 unknown。

来源完整性在轮次开始时记录指纹、结束时复检；一轮内来源变化则该轮不可用于版本对比。两轮的 `source_comparison_status` 都是 `verified`，这正是它们可用于版本对比的前提。

## 两轮结果

| 指标 | dev20 修复轮 | 新样本修复轮 |
| --- | --- | --- |
| 选定样本 | 20 | 10 |
| 准备可用 | 8 | 3 |
| 实际执行 | 8 | 3 |
| 来源状态 | verified | verified |
| 本地重复确认 | 4 | 1 |
| 本地差分确认 | 0 | 1 |
| 修复版对照（通过/未通过/受阻/未提供） | 0/4/0/16 | 1/0/0/9 |
| 独立交付确认 / 待独立重跑 | 0/4 | 0/1 |
| HTTP 尝试 | 121 | 47 |
| 记录 token | 953215 | 385455 |
| 耗时 p50 / p90 | 159 秒 / 204 秒 | 152.6 秒 / 152.6 秒 |
| 中断案例 | 2 | 2 |
| 官方判分 | 待判分 | 待判分 |
| 人工审查 | 待审查 20 | 待审查 10 |

费用未配置费率，仍为 unknown。本地的重复确认与差分确认分开列出，互不代偿：原版重复确认只说明报告的失败再次出现，修复版未通过不计作差分成功；本地 DONE 不能代替 SWT 官方判分。表中“中断案例”指轮次结果状态为 EXHAUSTED 或 CANCELLED 的案例（`evals/swt_bench/results.py` 的 `interrupted_tasks`）；新样本轮的 2 例即两例 EXHAUSTED 的 Sphinx 案例。

## dev20 修复轮逐例结果

| 样本 | 准备 | 最终状态 | 证据等级 | 修复版 |
| --- | --- | --- | --- | --- |
| pallets__flask-4992 | ready | DONE | REPEATED_OBSERVATION | 未通过 |
| pallets__flask-5063 | ready | DONE | REPEATED_OBSERVATION | 未通过 |
| sphinx-doc__sphinx-8435 | ready | DONE | REPEATED_OBSERVATION | 未通过 |
| sphinx-doc__sphinx-8595 | ready | DONE | REPEATED_OBSERVATION | 未通过 |
| sphinx-doc__sphinx-10325 | ready | EXHAUSTED | SINGLE_OBSERVATION | 未提供 |
| sphinx-doc__sphinx-8721 | ready | EXHAUSTED | NONE | 未提供 |
| sphinx-doc__sphinx-8506 | ready | BLOCKED | NONE | 未提供 |
| sphinx-doc__sphinx-8801 | ready | BLOCKED | SINGLE_OBSERVATION | 未提供 |
| 其余 12 例（requests / pytest / sympy） | blocked | NOT_PREPARED | NONE | 未提供 |

8 例准备可用并实际执行；4 例达到 REPEATED_OBSERVATION 但修复版未通过，因此本地差分确认为 0。两例执行后判为 BLOCKED 的案例（`sphinx-doc__sphinx-8506`、`sphinx-doc__sphinx-8801`）记录的 `stop_reason` 都是 ENVIRONMENT_BLOCKED。12 例仍为“目标 Python/pytest 不可用”，保留在分母里；它们的环境工作按计划另立专项，不算 Agent 能力结论。

## Sphinx-8801：已知定点未达到，原因已定位

计划的已知真实定点是 Sphinx-8801 完成 DIFF 并在全新副本 1/0，且失败不能用诊断候选抵扣。**本轮未达到**：该例以 BLOCKED / ENVIRONMENT_BLOCKED 结束，证据等级仅 SINGLE_OBSERVATION，修复版未提供，没有交付包。

修复本身是可见的：本轮该例产生了 4 个候选、6 次执行，**三个判决（verdict）返回 REPRODUCED**（同一候选 `candidate-bc984a8f…` 的三次运行）。作为对照，在记录的第三轮，该例因核验证据 payload 超过 32 KB 上限，从未拿到有效判决。但两轮不能读成纯算法对照：第三轮的模型选项与本轮不同（本轮显式 `thinking_mode: disabled`，配置哈希 `b6097961…` 与本轮 `6641b4e2…` 不同），dev20 两轮的绑定文件相同，新样本轮另用一份新的绑定。也就是说，挡住交付的不再是“生成不出有差分效果的测试”。

Task 4 的强制生命周期在真实数据上触发了：第一个发布的候选 `candidate-bc984a8f…` 在下一步就被执行，其得到 REPRODUCED 判决后 `submit_candidate` 被强制两次，两次都以 `action.rejected / REPLAY_UNCONFIRMED` 结束（`result_code: REPLAY_UNCONFIRMED`）。两个定位到的原因都未修复：

- 原因 A（已定位，未修复）：独立重放没有为一个被语义审查判为 REPRODUCED 的候选确认同一失败签名。签名稳定器只对 run 根做了相对化，没有覆盖 run 之外的逐次运行状态；一条携带 `runs/<run_id>/tmp` 下路径的消息会因为 `<run_id>` 不同而在两次运行间不一致。这是定位到的假设，不是已证明的根因；本轮为一个候选留下了两次 REPLAY_UNCONFIRMED 事件。
- 原因 B（已定位，未修复）：最后一个候选 `candidate-f7e3e9ad…` 触发候选级环境不兼容——`AttributeError: 'WindowsPath' object has no attribute 'makedirs'`，位置 `sphinx/testing/util.py:118`，是 Sphinx 自带的 py.path 测试夹具垫片在 Python 3.12 下对 pathlib 路径的调用失败——Controller 随后把整个任务判为 BLOCKED，尽管此前已有三个判决为 REPRODUCED。

因此该例仍不足以计入 DIFF；这两个原因各自的后续修复，不属于本轮。

## 新样本修复轮逐例结果

清单 `E:/ReproAgent/.local/swt-bench/holdout-task8/holdout.json`：`select_holdout` 在同一 catalog 上按固定 seed `reproagent-swt-holdout-v1` 确定性选出 10 例——对已支持的仓库做轮询，仓库内按 `sha256(seed + ':' + instance_id)` 排序取最小；排除 dev20。本项目自身历史案例记录的 20 个 id（`evals/cases/cumulative-historical-results.json` 的 `case_ids`）与该 catalog 无交集。该清单只说明“本项目未调试过”，不保证模型训练未见过这些公开数据。

| 样本 | 准备 | 最终状态 | 证据等级 | 修复版 | token |
| --- | --- | --- | --- | --- | --- |
| sphinx-doc__sphinx-11445 | ready | DONE | DIFFERENTIAL_VALIDATED | 通过 | 68267 |
| sphinx-doc__sphinx-8474 | ready | EXHAUSTED | NONE | 未提供 | 166782 |
| sphinx-doc__sphinx-8627 | ready | EXHAUSTED | NONE | 未提供 | 150406 |
| pytest-dev__pytest-5221 | 未准备 | NOT_PREPARED | NONE | 未提供 | — |
| pytest-dev__pytest-5227 | 未准备 | NOT_PREPARED | NONE | 未提供 | — |
| pytest-dev__pytest-8365 | 未准备 | NOT_PREPARED | NONE | 未提供 | — |
| pytest-dev__pytest-8906 | 未准备 | NOT_PREPARED | NONE | 未提供 | — |
| sympy__sympy-15678 | 未准备 | NOT_PREPARED | NONE | 未提供 | — |
| sympy__sympy-18621 | 未准备 | NOT_PREPARED | NONE | 未提供 | — |
| sympy__sympy-24213 | 未准备 | NOT_PREPARED | NONE | 未提供 | — |

`sphinx-doc__sphinx-11445` 是唯一达到 DIFFERENTIAL_VALIDATED 的样本：修复版对照通过，已导出复现包。其导出包随后由控制器在全新副本上独立重放：

```text
# 原版新副本
python <复现包>/replay.py \
  --repo .local/swt-bench/export-replay-holdout/buggy-fresh \
  --python <sphinx-11445 绑定解释器> \
  --output .local/swt-bench/export-replay-holdout/out-buggy --install

# 修复版新副本
python <复现包>/replay.py \
  --repo .local/swt-bench/export-replay-holdout/fixed-fresh \
  --python <sphinx-11445 绑定解释器> \
  --output .local/swt-bench/export-replay-holdout/out-fixed --install
```

两个结果：原版副本退出码 1，生成的测试 `tests/test_repro.py::test_rst_prolog_domain_directive_heading_in_toctree` 失败，断言 `'mypackage2' in content`；修复版副本退出码 0，收集 1 例、1 例通过。绑定解释器为 `.local/swt-bench/envs-pinned/sphinx-doc__sphinx-7.1/Scripts/python.exe`（Python 3.12.14、pytest 8.3.5）。

`sphinx-doc__sphinx-8474`（166782 token）与 `sphinx-doc__sphinx-8627`（150406 token）均为 EXHAUSTED，各留下一份诊断包。另有 7 例（4 个 pytest-dev、3 个 sympy）没有准备好的环境，保留在分母里，其环境工作正是计划推迟的专项。准备回执为 `E:/ReproAgent/.local/swt-bench/holdout-task8/preparation.json`；为 Sphinx 7.1 新建了一个环境，位于 `.local/swt-bench/envs-pinned/sphinx-doc__sphinx-7.1`。

`sphinx-doc__sphinx-8627` 需披露一个既有环境限制：其 FAIL_TO_PASS 节点 `tests/test_util_typing.py::test_restify` 在任何可用解释器上都无法通过——Python 3.11+ 把 `typing.Any` 变成类，因而渲染为 `:class:` 而非 `:obj:`；它的原版副本 2/2 失败、修复版副本 1/2（`test_restify` 通过不了），因此它在本环境里不能充当可区分版本的对照。这不是准备缺陷。

## 官方判分

两轮的 `official_grade_status` 都是 `pending`，`official_rate` 为 `null`，两轮合计 30 例未核验。本机没有可用的 Docker 或 WSL，官方 SWT harness 未实际运行。官方成绩只来自独立 SWT harness 的 unit_test 模式；本地 DONE、原版重复或本地比例都不能替代，也不把本地比例写成官方成绩。旧口径字段 `official_total_rate` 的暂定下界含义保留，但本报告不引用它。

## 这些数字支持与不支持什么

- 在未参与本项目调试的新样本上，修复后的 Agent 交付了**一个**经独立重放验证的差分复现（`sphinx-doc__sphinx-11445`），其导出包在全新副本上复现 1/0。在此之前经过调试的 dev20 上，本轮**没有**交付任何差分复现，计划命名的已知定点（Sphinx-8801 达到 DIFFERENTIAL_VALIDATED）也**未**达到。
- 这是小样本：holdout 只有 3 例实际执行，dev20 为 8 例。它不是一般复现率，计划也禁止把它当作复现率呈现；本报告不给出任何暗示能力估计的百分比。
- 公开数据集可能已在模型训练数据中。holdout 只保证“本项目未调试过”，这比“模型未见过”弱。
- 两个 Sphinx-8801 定位原因（原因 A 的失败签名稳定器、原因 B 的候选级环境不兼容）与 8627 的 typing 环境限制，都是有各自后续工作的开放项；本轮**只记录，未修复**。
- 两轮的来源状态都是 `verified`，这正是它们可用于版本对比的依据（同一冻结实现、同一模型配置、同一预算）。

## 证据位置

- 冻结回执：`.local/swt-bench/task8-freeze.json`。
- dev20 修复轮：`repro-results/swt-bench/dev20-repaired-001/round.json`、`summary.json`、`report.md`，逐例记录在 `cases/<instance_id>/task`。
- 新样本修复轮：`repro-results/swt-bench/holdout-repaired-001/round.json`、`summary.json`、`report.md`。
- 新样本清单与准备：`.local/swt-bench/holdout-task8/holdout.json`、`preparation.json`、`bindings.json`；新建环境 `.local/swt-bench/envs-pinned/sphinx-doc__sphinx-7.1`。
- sphinx-11445 导出包独立重放：`.local/swt-bench/export-replay-holdout/buggy-fresh`、`fixed-fresh` 及 `out-buggy/probe.jsonl`、`out-fixed/probe.jsonl`。
- Sphinx-8801 逐候选判决与事件：`repro-results/swt-bench/dev20-repaired-001/cases/sphinx-doc__sphinx-8801/task/verdicts`、`events.jsonl`。
- 固定数据、来源哈希、筛选与复现命令见 [SWT 评测说明](../swt-bench.md)。

[公开数据集](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite) · [固定 SWT harness](https://github.com/logic-star-ai/swt-bench/tree/330a649a764fab2fadaea632776eeae87272f74b)
