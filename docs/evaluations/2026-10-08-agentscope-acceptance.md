# AgentScope 单运行时架构真实验收：2026-10-08

**结论：验收已真实执行（上一轮"一项都没跑"的状态已解除）。在能跑起来的样本上，迁移前后的两套架构
差分成功的例数相同、且不是同一例；官方判分只在冻结保留集上取得，结论见下文第五节。**

本文件是 [2026-10-07 的基础设施评测](2026-10-07-agentscope-infrastructure.md) 的续篇：那一轮记录了
"迁移代码验证通过、能力验收未执行"，本轮把没执行的部分补上了。那一轮的结论仍然有效，只是**它的第 3、4、
5 项现已由本文件取代**。

## 一、环境与冻结

| 项 | 值 |
| --- | --- |
| 执行日期 | 2026-10-08 |
| 实现提交 | `8c17d685f95b9ddd62a522c35c32af3c4f269304`（发布预留修复之后的 HEAD） |
| 平台 | Windows 11，模型轮次在 Windows 上跑；官方判分在 WSL2 Ubuntu + Docker 内跑 |
| 基础设施 | agentscope 2.0.9（唯一运行时；`--model-backend`/`--agent-backend` 已废弃但仍接受） |
| 工具解释器 | CPython 3.12.14；pytest 9.1.1 |
| ripgrep | 14.1.1，`E:/ReproAgent/.local/bin/rg.exe`，只在该进程的 PATH 上 |
| 模型 | `examples/model.deepseek.non-thinking.json`（deepseek-flash，`thinking_mode=disabled`，输出上限 4096） |
| 预算 | 20 个探索决策、单命令 60 秒、任务 900 秒、工具响应 32768 字节（`BudgetLimits` 默认值，与迁移前一致） |
| 冻结回执 | `.local/swt-bench/agentscope-freeze.json` |

轮次名与判分对象：开发子集 `sdk-dev20-001`、冻结保留集 `sdk-holdout-001`。两次轮次都落在同一个实现
提交上，其 `round.json` 的记录模型名为 `reproagent-sdk-disabled`。

## 二、逐项执行状态

| # | 计划项 | 状态 |
| --- | --- | --- |
| 1 | dev20 重跑（冻结实现、同一预算） | **EXECUTED** |
| 2 | 冻结保留集 10 例执行 | **EXECUTED** |
| 3 | Sphinx-8801 定点任务 | **EXECUTED**，未达成（见第四节） |
| 4 | 官方 Linux/Docker harness 判分 | **EXECUTED**（保留集）；dev20 **未判分**，理由见第五节 |
| 5 | 已交付包的独立重放 | **未执行**，与迁移前同一状态（见第六节） |

## 三、开发子集（dev20）与冻结保留集

两个轮次与迁移前的同名轮次使用同一份 catalog、同一份 manifest、同一批绑定环境与同一套预算。

### dev20

| 指标 | 迁移前 `dev20-repaired-001`（native） | 迁移后 `sdk-dev20-001`（agentscope） |
| --- | --- | --- |
| 样本 / 准备好 / 实际执行 | 20 / 8 / 8 | 20 / 8 / 8 |
| 重复观察 | 4 | 2 |
| 差分验证 | 0 | 0 |
| 修复版对照 | 0 通过 / 4 失败 | 0 通过 / 2 失败 |
| 交付补丁（非空预测） | 4 | 2 |
| HTTP 尝试 | 121 | 181 |
| token | 953,215 | 2,961,017 |
| 单例耗时 p50 / p90 | — | 87.5 s / 150.8 s |
| 被打断的任务 | — | 4 |

### 冻结保留集（holdout，10 例）

| 用例 | 迁移前 | 迁移后 |
| --- | --- | --- |
| sphinx-doc__sphinx-11445 | **DONE / DIFFERENTIAL_VALIDATED**，修复版通过，68,267 tok | **BLOCKED / ENVIRONMENT_BLOCKED**，404,028 tok |
| sphinx-doc__sphinx-8474 | EXHAUSTED / NONE，166,782 tok | NEEDS_INFORMATION / MISSING_INFORMATION，302,709 tok |
| sphinx-doc__sphinx-8627 | EXHAUSTED / NONE，150,406 tok | **DONE / DIFFERENTIAL_VALIDATED**，修复版通过，242,216 tok |
| 其余 7 例（4×pytest-dev、3×sympy） | NOT_PREPARED | NOT_PREPARED |
| 差分验证合计 | 1 | 1 |
| 交付补丁 | 1（11445） | 1（8627） |
| HTTP 尝试 / token | 47 / 385,455 | 60 / 948,953 |
| 单例耗时 p50 / p90 | — | 86.5 s / 134.4 s |

**两套架构都在能跑的 3 例里恰好差分成功 1 例，而且是不同的那一例。** 这个样本量下两者不可区分：既不能
说迁移提升了能力，也不能说它降低了能力。

### 三例里发生了什么

- `sphinx-doc__sphinx-11445`：迁移后的候选测试踩到 `envs-pinned/sphinx-doc__sphinx-7.1` 自带的路径类型
  不兼容（`AttributeError: 'WindowsPath' object has no attribute 'makedirs'`，抛自该环境自己的
  `sphinx/testing/util.py`）。语义核验把它判为环境问题，任务以 `BLOCKED / ENVIRONMENT_BLOCKED` 结束。
  **这是证据优先设计在起作用**：环境异常没有被冒充成复现成功。但代价是真实的——迁移前同一用例在同一
  个固定环境上做出了一个官方判 RESOLVED 的修复。
- `sphinx-doc__sphinx-8627`：迁移后的候选通过原版失败 / 修复版通过的对照，交付补丁 1430 字节。
  该用例的 `FAIL_TO_PASS` 节点 `tests/test_util_typing.py::test_restify` 在**本机的任何解释器**上都无法
  通过（Python 3.11+ 把 `typing.Any` 变成类，渲染成 `:class:` 而非 `:obj:`），所以本机的差分对照成立的
  是模型自己写的复现测试，而不是官方判定用的那个节点。官方判分使用数据集自己的容器与解释器，
  两者不可互推。
- `sphinx-doc__sphinx-8474`：迁移后以 `NEEDS_INFORMATION / MISSING_INFORMATION` 结束，没有发布候选。

### token 差异从哪来

`total_tokens` 的口径已核实：它等于本轮全部 `model.attempt` 事件的用量之和，与 `http_attempts` 精确吻合
（保留集 47 → 60，dev20 121 → 181）。按**单次调用**看，保留集从 8,201 token 涨到 15,816（1.93 倍），
dev20 从 7,878 涨到 16,359（2.08 倍）。机制是每步把完整对话重发：迁移后一个任务的 SDK 历史全部留在
对话里（这是迁移刻意保留的"记忆"），而迁移前的载荷是有界的（契约 + 反馈 + `history[-4:]`），所以单次
调用随步数线性变大，整轮近似平方增长。

步数也变了：迁移后每例几乎都烧完整个 20 步预算，最后是被"发布预留"逼出候选的。预留本身是为了不让
预算在纯阅读里耗尽，但它暴露的是同一个问题——探索阶段不主动收敛。**这是本轮最主要的架构代价：
代价在 token 与步数上，不在判分上。**

## 四、Sphinx-8801 定点任务：未达成

按冻结配置发起全新任务，目标是拿到"原版 1 / 修复版 0"的定点。结果 **NOT achieved**，链条完整可查：

- 发布预留强制推出 1 个候选；原版两次运行均失败；两次语义核验在中文叙述下都判 REPRODUCED；
  修复版运行起来后**失败**，因此没有达到差分验证。
- 已定位的原因在候选质量，不在基础设施：该候选最后的断言要求裸调
  `ClassDocumenter.get_object_members(want_all=True)` 返回继承来的属性，而官方修复并不改变这个行为；
  Issue 描述的、可观察到的行为（属性出现在生成的输出里）不是它断言的东西。这与迁移前 dev20 里
  4 个修复版对照失败的候选属于同一类。
- 按既定规则：旧轮次的诊断候选**没有**被算作任何成功，也没有被用来抵扣本轮。

## 五、官方判分（独立 SWT harness）

判分在 WSL2 Ubuntu + Docker 内进行，使用 `logic-star-ai/swt-bench`（pin 在
`330a649a764fab2fadaea632776eeae87272f74b`）、`unit_test` 模式、同一份冻结 snapshot、`--max_workers 1`。
判分不调用模型，也不消耗 token。本地轮次的比例**不是**官方成绩，官方成绩只来自这个 harness。

**冻结保留集**：`sdk-holdout-001` 的 10 例已判分。

> 本节在判分进行中写成，官方结果回填后会更新。截至写入时，第 8 个实例（`sympy__sympy-15678`）的
> `pre` 套件已运行 20 分钟以上；前 7 个实例已完成。**在结果落地之前，本文件不给出迁移后的官方数字，
> 也不把本地轮次的比例当作官方成绩。**

**dev20 未判分，这是刻意的**：`sdk-dev20-001` 的 20 条预测里有 18 条是**空补丁**（只有
`pallets__flask-5063` 与 `sphinx-doc__sphinx-8595` 有内容）。对它跑官方判分，得到的数字主要由空补丁
决定，而不是由两套架构的差异决定；迁移前那一轮 dev20 也从未判过分，因此没有可对比的基线。要在 dev20
上取得可比较的官方数字，需要先让更多用例准备好环境，那是另一项工作。

## 六、未执行的部分

- **已交付包的独立重放**：迁移后的两个轮次各留了待重放项（dev20 2 例、保留集 1 例），本轮没有执行。
  迁移前的同名轮次处于同一状态（dev20 4 例、保留集 1 例待重放）。保留集在迁移前做过一次**手工**重放
  （`.local/swt-bench/export-replay-holdout/`）：原版新副本退出 1、修复版新副本退出 0，但那次重放没有
  写回轮次记录，所以 `independent_delivered` 仍是 0。
- **人工评价**：未提供，保持 pending，且不是运行的必要步骤。
- **费用**：未配置费率，`cost_kind` 全部为 unknown，不写 0。

## 七、本轮发现并修掉的两个基础设施缺陷

两者都是在真实验收里暴露出来的，都属于"迁移删掉了旧运行时里某个必要的约束"这一类。

1. **发布预留被迁移删掉了**（修复提交 `8c17d68`）。第一次定点尝试以 EXHAUSTED 结束：探索阶段把整个
   20 步预算花在逐个读文件上，从未发布候选。现在的中间件在剩余步数 ≤ 3、且契约还有期望、也不缺信息时，
   拒绝 `Read`/`Grep`/`Glob`，把最后三步留给发布。集成测试
   `test_agentscope_runtime.py::test_the_last_steps_are_reserved_for_publishing` 固定这个行为。
2. **本机缺少 ripgrep**。SDK 的 `Grep`/`Glob` 在没有 ripgrep 时按设计阻塞，于是探索退化成逐个
   `Read`——这正是第 1 条里"预算被读文件吃光"的直接原因。装上 ripgrep 并只给该进程加上 PATH 之后，
   两个轮次才跑出上面的结果。冻结回执里记了这一点。

另有一处**未定性**的问题必须披露：CI 运行 `37759702346`（提交 `a2aae15`）的
`offline (windows-latest, 3.11, pytest>=8,<9)` 这一个 job 失败，用例
`test_the_fixed_version_never_reaches_the_exploration` 报 `TaskState.FAILED` 而不是 `DONE`，同时 SDK
打出 `Model offline exhausted all 1 attempt(s)`。同一个测试在前后两个提交、以及本地连跑 20 次都是通过的，
所以它是间歇性的、只在这个 CI 组合上出现过一次。已记录，尚未定位。

## 八、与历史记录的关系

- 本文件不改写任何旧轮次的结果。
- 迁移前的基线取自 `repro-results/swt-bench/dev20-repaired-001`、`repro-results/swt-bench/holdout-repaired-001`
  与 [2026-10-07 的评测](2026-10-07-swt-repaired.md)。
- 迁移前的保留集官方结论：10 例中 **1 例 resolved**（`sphinx-doc__sphinx-11445`）、6 例 unresolved、
  3 例 error（三个 sympy 实例在 harness 侧报错，没有产出判定）。
- 本轮没有产生任何可用于版本比较的"源改变"轮次：两个轮次的 `source_comparison_status` 都是 verified。

## 九、证据在哪

| 内容 | 位置 |
| --- | --- |
| 冻结配置回执 | `.local/swt-bench/agentscope-freeze.json` |
| dev20 轮次 | `repro-results/sdk-dev20-001/`（`round.json`、`summary.json`、`predictions.jsonl`） |
| 保留集轮次 | `repro-results/sdk-holdout-001/` |
| 迁移前基线 | `repro-results/swt-bench/dev20-repaired-001/`、`repro-results/swt-bench/holdout-repaired-001/` |
| 定点任务 | `repro-results/sdk-fixedpoint-8801-002/` |
| 官方判分 | `repro-results/sdk-holdout-001/official-execution.receipt.json`、harness 的 `evaluation_results/` |
| 轮次驱动脚本 | `.local/swt-bench/run-sdk-round.ps1`、`.local/swt-bench/official-grade-sdk.sh` |
