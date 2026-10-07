# AgentScope 基础设施迁移评测：2026-10-07

**结论：迁移代码验证通过，能力验收未通过。**

迁移后的代码通过完整离线门槛（整链路真实 SDK + MockTransport），但本任务要求的真实模型验收
**一步都没有执行**：本环境没有模型 API 凭据，也没有可用的 Linux/Docker。因此本次没有任何真实模型
结果、没有可用于对比的 HTTP/token 计数、没有官方判分。下面逐项说明执行了什么、没有执行什么，以及
为什么。

## 环境与冻结

| 项 | 值 |
| --- | --- |
| 执行日期 | 2026-10-07 |
| 平台 | Windows 11（win32），Git Bash |
| 工作区 | git worktree，分支 `agentscope-infrastructure` |
| 实现冻结提交 | `8aa6ae58ccaed54a9dc58639f7b24600962a432a`（本轮文档与测试改动之前的 HEAD） |
| 工具解释器 | CPython 3.12.14 |
| agentscope | 2.0.9（主依赖） |
| httpx / openai / pydantic | 0.28.1 / 3.26.0 / 2.13.5 |
| pytest | 9.1.1 |
| 模型配置 | `examples/model.deepseek.non-thinking.json`（`thinking_mode=disabled`，`max_output_tokens=4096`）；**本轮一次都没有实际调用** |
| 默认预算 | 20 个探索决策、单命令 60 秒、任务 900 秒、工具响应 32768 字节 |

凭据检查（执行前，逐项确认为空）：`REPROAGENT_API_KEY`、`DEEPSEEK_API_KEY`、`OPENAI_API_KEY` 均未设置。
`pip check`：`No broken requirements found.`

## 逐项执行状态

计划的七项验收逐项如下。"EXECUTED" 表示本次真的运行并留下了证据；"NOT EXECUTED" 表示没有运行，
且**没有**用 MockTransport 的结果或旧轮次抵扣。

| # | 计划项 | 状态 |
| --- | --- | --- |
| 1 | 整链路真实 SDK + MockTransport 测试 | **EXECUTED** |
| 2 | 离线验收（pytest / pip check / importorskip） | **EXECUTED** |
| 3 | 已知候选确定性回归 + Sphinx-8801 全新 SDK/DeepSeek 定点任务 | 回归 EXECUTED；真实定点 **NOT EXECUTED** |
| 4 | 冻结实现后重跑 dev20、已交付包独立验证、冻结并执行 10 个未调试样本 | 冻结保留集 EXECUTED；重跑与执行 **NOT EXECUTED** |
| 5 | 官方 Linux/Docker harness 判分 | **NOT EXECUTED** |
| 6 | 更新文档描述最终结构 | **EXECUTED** |
| 7 | 提交、不自动 push、明确能力验收结论 | **EXECUTED** |

### 1. 整链路测试（EXECUTED）

`tests/integration/test_agentscope_backends.py::test_the_whole_chain_runs_from_snapshot_search_to_exported_replay`
在一个任务里走完整条链：探索阶段通过真实 SDK `Agent` + 真实 phase toolkit 依次发出
Glob → Grep → Read → `write_candidate`（每个响应一个工具调用，因为多于一个会被整份拒绝），随后
Controller 自动执行原版候选、做结构化语义核验、独立重复一次、在给定固定版执行同一候选并导出。

该测试断言：

- 探索 wire 的每个请求都携带 phase toolkit，且工具名恰好是六个阶段工具；
- 每个请求只有一个工具的选择，工具的**真实结果**作为 SDK 的 tool 消息进入下一个请求
  （Glob 的文件清单、Grep 的匹配行、Read 带 `<evidence>` 边车的响应）；
- 领域请求是**无 tools** 的结构化请求，`response_format={"type": "json_object"}`，响应种类恰好是
  `['contract', 'verdict', 'verdict']` —— 模型没有被要求做任何 run/submit 决策；
- Controller 的执行业务步骤记为 `action.completed` 的 `run_candidate`/`submit_candidate`；
- 一次候选被执行三次（`original`/`original`/`fixed`），被核验两次；
- 隐藏修复版路径不出现在任何请求里；
- 导出包在**新的**原版副本退出 1、在**新的**修复版副本退出 0。

`test_a_phase_that_only_claims_success_publishes_and_runs_nothing` 断言同一 wire 下，把最后一个回合
换成"这是成功"的自然语言时：阶段是 `no_candidate`，任务以 `NEEDS_INFORMATION` 结束，没有候选、没有
执行、没有复现包，只有诊断包。

这些结果证明的是**产品行为**（边界、预算、证据、装配、交付），不是模型能力。

### 2. 离线验收（EXECUTED）

```text
.venv/Scripts/python.exe -m pytest tests/unit tests/integration -q -rs
437 passed, 4 skipped in 408.46s (0:06:48)          # exit code 0, default basetemp
.venv/Scripts/python.exe -m pip check
No broken requirements found.
```

4 个 skip 全部与 SDK 无关，它们是本机账户缺少符号链接权限与 venv 链接不可达：
`test_paths.py:137`、`test_workspace.py:48`、`test_windows_long_paths.py:474`
（`link creation needs privilege on Windows`）与 `test_windows_long_paths.py:501`
（`this interpreter is not reached through a link`）。没有任何一条是 `importorskip` 造成的。

**basetemp：使用默认 basetemp，未使用计划里的 `--basetemp=.tmp/agentscope-infra-final-01`。**
计划给出的路径在仓库内，已核实两种后果：(a) `pytest --basetemp` 不会创建缺失的父目录，`.tmp/` 不存在
时会得到一整屏 setup 错误；(b) 即使 `.tmp/` 存在，仍有 8 个测试失败——`test_swt_provenance.py` 会看到
外层 worktree 仓库，而它的 fixture 需要一个非仓库目录；`test_swt_controls.py` 的嵌套 pytest 会经由
rootdir 发现 worktree 的 `pyproject.toml`、套用 `testpaths=["tests"]`，从而收集不到目标测试。这两个
文件在默认 basetemp 下独立运行均通过。

必需 SDK 测试不再使用 module 级 `pytest.importorskip`：`agentscope==2.0.9` 是主依赖，缺失时应当像产品
一样报安装错误，而不是让 SDK 测试模块静默跳过。唯一保留的是 `test_windows_long_paths.py` 内 helper 级的
一处，因为该模块同时承载不依赖 SDK 的长路径与 venv 回归，在 helper 里延后导入是刻意的设计。

### 3. Sphinx-8801 定点任务（NOT EXECUTED）

计划要求用 Sphinx-8801 的原始 Issue 发起一个全新的 SDK/DeepSeek 任务（预算 20/60/900，
`examples/model.deepseek.non-thinking.json`，独立 1/0）。**没有执行**：`DEEPSEEK_API_KEY` 未设置，
任务在 `cli.run_command` 的准备阶段就会以 `missing API key environment variable: DEEPSEEK_API_KEY` 退出，
不会产生任何可解释的结果。

- 已知诊断候选没有用来抵扣成功：它只保留了确定性回归（离线整链路测试），没有被算作任何真实评测结果。
- 没有代填候选、没有用旧诊断轮转写成本轮结果。
- 旧轮次的诊断材料（Sphinx-8801 的两个未接受候选，独立执行 1/0 但被核验证据体积挡住）仍然只作为
  诊断保留，见 [上一轮评测](2026-10-06-swt-development.md)。
- 原先"不马上花整轮预算"的要求无从适用：本轮一次模型请求都没有发出。

### 4. dev20 重跑与冻结保留集（部分 EXECUTED）

**冻结保留集：EXECUTED（离线可达部分）。** `select_holdout` 是 Task 10 的固定策略，一个只读公开
catalog 的纯函数，不需要模型：

```python
select_holdout(catalog, excluded_ids, repos=(...), count=10)
```

本次用它冻结了 10 个未调试样本（五个仓库 × 2），仓库全部取自开发轮未使用的项目，排除集为 dev20 的
20 个 ID：

| # | instance_id | repo |
| --- | --- | --- |
| 1 | django__django-11283 | django/django |
| 2 | matplotlib__matplotlib-24149 | matplotlib/matplotlib |
| 3 | scikit-learn__scikit-learn-25500 | scikit-learn/scikit-learn |
| 4 | pydata__xarray-4248 | pydata/xarray |
| 5 | mwaskom__seaborn-3407 | mwaskom/seaborn |
| 6 | django__django-14787 | django/django |
| 7 | matplotlib__matplotlib-23314 | matplotlib/matplotlib |
| 8 | scikit-learn__scikit-learn-14894 | scikit-learn/scikit-learn |
| 9 | pydata__xarray-4493 | pydata/xarray |
| 10 | mwaskom__seaborn-2848 | mwaskom/seaborn |

- manifest 哈希 `d2f91341d5b718eaffa88ca1d1401acd798af3937c389c18d6fdd0798525b4dc`
- catalog 哈希 `06921b5b10ab3295b506b72e66cdbc12b1a7d197c36900c1fa44e2b47d1b6bb6`
- 两次调用结果相同（固定 seed 可重现），manifest 封印校验通过
- 落盘位置：worktree 内 `.local/swt-bench/holdout10.json`（`.local/` 被 gitignore，不进入提交）

**执行这 10 个样本：NOT EXECUTED**——与第 3 项同一个原因，没有凭据。没有产生任何轮次记录，
环境阻塞**没有**出现在任何分母里，因为根本没有轮次。

**重跑 dev20：NOT EXECUTED。** 没有真实模型轮次，因此：没有"每例有记录"，没有已交付包的独立验证
（本轮唯一的独立重跑验证是离线整链路测试里的 MockTransport 导出包，它不能被表述为真实模型交付），
也没有可比的 HTTP/token 计数。

### 5. 官方 Linux/Docker harness（NOT EXECUTED）

本机没有可用的 Linux/Docker，官方 harness 没有执行，因此**没有官方成绩**。缺失的官方结果不写成官方
0%，也不写成成功；官方状态保持未运行，与此前的 [上一轮评测](2026-10-06-swt-development.md) 一致。

## 本次没有产生的数字

- **真实模型轮次**：0（没有轮次，不是"0 次成功"）。
- **HTTP 尝试 / token / 耗时 / 费用**：没有真实模型调用，因此没有可记录的尝试数或 token 数；
  费用保持 unknown，不写 0。
- **复现率**：不给出。MockTransport 的成功不是模型能力，也不预示更高的复现率。
- **官方判分**：未运行，保持 pending。
- **人工评价**：未提供，保持 pending，且不作为运行的必需步骤。

## 与历史记录的关系

- 历史任务包与 native 评测结果保持原样，本文件不改写任何旧轮次。
- 上一轮真实评测（[2026-10-06](2026-10-06-swt-development.md)：20 例中 8 例调用模型、有效差分交付 0）
  记录的是迁移前的 native 路径，本轮迁移没有重跑它，两者不可相互抵扣。
- 本轮冻结的实现提交、导入路径、依赖与配置见上表；正式版本对比仍需在干净提交上重新执行，而这一轮
  没有产生可用于版本比较的轮次（也没有源改变的轮次）。

## 离线证据在哪

- 整链路测试：`tests/integration/test_agentscope_backends.py`（本文件提及的两个测试）。
- 离线门槛与 `pip check` 的命令与输出记录在
  `.superpowers/sdd/2026-10-07-reproagent-agentscope-infrastructure/task-11-report.md`。
- 冻结保留集 manifest：`.local/swt-bench/holdout10.json`（gitignore，哈希见上）。
