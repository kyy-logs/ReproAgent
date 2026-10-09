# AgentScope 单运行时架构真实验收：2026-10-08

**结论：验收已真实执行（上一轮"一项都没跑"的状态已解除），并且验收过程中发现的问题已经被修掉、再验过。
迁移后的架构在官方判分上与迁移前持平；但"已交付候选几乎不发生"才是更要紧的一条。** 具体地：

- **官方判分**：迁移前 1 例 resolved、迁移后（修复前）0 例、修复后**又是 1 例，而且是同一例**
  （`sphinx-doc__sphinx-11445`）。三轮里带空补丁的预测一律 unresolved，所以这个差别只来自各自有补丁的
  那一两个实例；10 例里真正"既交了补丁、容器又能判"的只有 11445 一个（第五节）。
- **两例判不了**（`8627`、`8474`）：harness 的容器里 setuptools 82 不再提供 `pkg_resources`，而 2022 年的
  Sphinx 在导入阶段就要用它——**官方 gold 补丁同样跑不过**。决定记录并排除，不改评判环境（第五节）。
- 补齐 4 个 pytest 用例的环境后（第八节），它们第一次真正跑起来，**7 例执行、交付候选 0 例**：模型确实
  转向了 `write_candidate`，但都在**剩余 0～1 步**时才调。这一条已经被修（`e228f10`）并再验：修复后最后
  三步不再出现读取，7 例全部产生阶段结果，交付候选与差分确认**都从 0 变成 2**。
- 模型配置没有设温度，同配置三轮对同样 3 个用例给出三组不同结果。因此又补了温度设置（`88eb3b4`）；
  **本文件里所有"各中 1 例"量级的对比都落在噪声里**，只能作为个案。
- 代价是可测的：单次模型调用 token 约翻倍，每例几乎烧完 20 步预算。

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
| 6 | 补齐 7 个未跑用例的环境并重跑 | **EXECUTED**（4 例就绪并执行，3 例 sympy 结构性排除），见第八节 |

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

**冻结保留集**：`sdk-holdout-001`（修复前）与 `sdk-holdout-005`（修复后）各 10 例，都已判分。

判分回执（`sdk-holdout-005`）：`exit_code 0`、耗时 6397 秒、`stop_reason` 空、7 份 instance 报告哈希；
harness commit 与 snapshot 哈希与迁移前那一轮完全相同（`330a649a…`／`d891180a…`）。

| 判定 | 迁移前 `holdout-repaired-001` | 迁移后·修复前 `sdk-holdout-001` | 迁移后·修复后 `sdk-holdout-005` |
| --- | --- | --- | --- |
| resolved | **1**（`sphinx-doc__sphinx-11445`） | **0** | **1**（同一个 `sphinx-11445`） |
| unresolved | 6 | 7 | 6 |
| error | 3（三个 sympy） | 3（同样三个） | 3（同样三个） |

逐例回执：7 例 `verified`（4 个 pytest 全部 unresolved，3 个 sphinx 里只有 11445 resolved），3 个
sympy 是 `missing`——harness 没有产出报告。按工具的口径，**只要还有用例没有 verified 报告，整轮就保持
"待判分"、不给 `official_rate`**，所以这里报的是原始计数（10 例中 1 resolved / 6 unresolved / 3 error），
不是比率。

### 这组官方数字能支持什么

- **能支持**：修复后的迁移架构在官方判分上**与迁移前持平**——同样 1 例 resolved，而且是同一例
  （`sphinx-doc__sphinx-11445`）。修复之前那一轮是 0。
- **不能支持**：任何"能力优劣"的结论。这三轮里**带空补丁的预测一律 unresolved**，所以差别只可能来自
  各自有补丁的那一两个实例；而 10 例里真正"既交了补丁、容器又能判"的只有 11445 一个。
- 三个 sympy 的 error 三轮完全相同，都是
  `Command '/bin/bash /eval.sh' timed out after 1800 seconds`（harness 自身默认上限），与本轮改动无关。

### 两个实例判不了：harness 环境的一个已知缺陷（记录并排除，不修）

`sphinx-8627` 与 `sphinx-8474` 在这个 harness 里**用官方 gold 补丁也跑不过**：`gold_post` 与
`base_post` 都失败于 `ModuleNotFoundError: No module named 'pkg_resources'`，抛自
`sphinx/testing/fixtures.py` → `sphinx/application.py` → `from pkg_resources import iter_entry_points`。

**根因已查明，而且不是镜像建坏了。** 在 8627 的镜像里新建一个干净虚拟环境就能复现：

```text
pip list            →  pip==26.0.1  setuptools==82.0.1
import pkg_resources →  ModuleNotFoundError
```

**setuptools 82 已经不再提供 `pkg_resources`**（先弃用、后移除，改用 `importlib.metadata`），而这套
2022 年的 Sphinx 在导入阶段就要用它，于是容器连 conftest 都加载不了。这是数据集条目本身在新工具链下
失效，**对任何补丁一视同仁**——它既是"不是我们补丁的问题"的证据，也是"这一例判不出来"的原因。

**决定：记录并排除，不改评判环境。** 另一条路（把评判环境的 setuptools 钉在 <81）能让实例重新可判，
而且可以用"金标是否 resolve"客观验证；但它会改变分母，**使此前所有轮次的官方数字失去可比性**，要用
就得连旧轮次一起重跑。这个取舍不属于本轮验收，所以不动。

后果很小，且要说清：`8474` 本来就没有补丁；`8627` 丢的是一个**官方裁决**——它的**本地**差分是成立的
（修复版对照通过）。官方裁决与本地差分在本文里始终分开写。



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

## 七、本轮发现并修掉的基础设施缺陷

两者都是在真实验收里暴露出来的，都属于"迁移删掉了旧运行时里某个必要的约束"这一类。

1. **发布预留被迁移删掉了**（修复提交 `8c17d68`）。第一次定点尝试以 EXHAUSTED 结束：探索阶段把整个
   20 步预算花在逐个读文件上，从未发布候选。现在的中间件在剩余步数 ≤ 3、且契约还有期望、也不缺信息时，
   拒绝 `Read`/`Grep`/`Glob`，把最后三步留给发布。集成测试
   `test_agentscope_runtime.py::test_the_last_steps_are_reserved_for_publishing` 固定这个行为。
2. **本机缺少 ripgrep**。SDK 的 `Grep`/`Glob` 在没有 ripgrep 时按设计阻塞，于是探索退化成逐个
   `Read`——这正是第 1 条里"预算被读文件吃光"的直接原因。装上 ripgrep 并只给该进程加上 PATH 之后，
   两个轮次才跑出上面的结果。冻结回执里记了这一点。

3. **判分工具在失败后无法重跑**（修复提交 `a6b3d46`）。官方 runner 用自己的 `'xb'`（独占创建）打开
   `official.stdout.log` / `official.stderr.log`，而失败的那次会留下它们；重跑于是撞上一句既没说文件
   也没说原因的 `FileExistsError`，只能手动删文件。而一次基础设施失败（全是 `infra_error`、没有报告哈希）
   **不是裁决**，不该作废整个轮次。现在 `prepare_retry` 会清掉失败那次自己的遗留并记下它的停止原因，
   新回执用 `retried_after` 保留"这是一次重跑"。**已经出结论的轮次仍然拒绝重跑**，判过的成绩不能被重掷。
4. **CI 上一条会互斥失败的测试**（修复提交 `ab15ad8`）。`test_learning_timeout_preserves_sealed_result`
   在 CI 上以 `assert 0 == 1` 失败：学习预算**在 `build_learning_input` 之前**开始计时，而抛异常的那个
   检查在发请求之前，所以预算比准备工作还短时，会得到**同一个 `timeout` 代码、但一个请求都没发出**。
   把准备时间人为加 0.2 秒即可逐字复现（同一测试、同一行、同一断言）。预算从 0.15 秒提到 5 秒——仍然远
   小于那个挂死的处理器，掐断的依旧是"已发出的请求"这一支。

5. **探针的帧追踪在 Python 3.9 上终结收集**（修复提交 `dc47acb`）。这是本轮最有价值的一条，因为它推翻了
   一个我先前给出的判断。`pytest-dev__pytest-8365` 在验证轮里连发 3 次 `write_candidate`，3 次都是
   `INVALID_CANDIDATE`，我一度记成"候选质量不够"。逐份读裁决才发现，三次的原因都是
   **`no collected tests`**，而 run 的 stdout 显示崩溃在**产品自己的探针插件**里：

   ```text
   src\_pytest\fixtures.py:1335: in fixture
   <attrs generated init _pytest.fixtures.FixtureFunctionMarker>:1: in __init__
   probe/reproagent_pytest_probe.py:36: in profile
       _origins[name] = str(Path(frame.f_code.co_filename).resolve())
   E   OSError: [WinError 123] …: '<attrs generated init _pytest.fixtures.FixtureFunctionMarker>'
   ```

   追踪器把每一帧的 `co_filename` 当路径去 `resolve()`。**attrs 生成的函数没有文件**，编译器给那一帧的
   名字是 `'<attrs generated init …>'`，而 **Python 3.9——数据集给每个实例钉的版本——在 Windows 上对含
   尖括号的名字直接抛 OSError**。异常终结了收集 → `no collected tests` → 候选被判无效，**在测试跑起来
   之前**。任何定义 fixture 的用例都会中招；其他 pytest 用例从没发布过候选，所以它一直没露面。

   修法是"没有文件就没有出处可记"：出处不再记录，调用照常计数。用那一轮**原样保存的 argv** 在 Python 3.9
   下重放验证：修之前一个节点都收集不到，修之后 **3 个节点全部收集并运行**，另有 60 个 target origin 被
   正常记录。注意它不是"让候选变好"——那三个测试现在会在 setup 阶段以真实原因报错——而是**让反馈回到模型
   手里**，此前模型收到的是一个它无能为力的基础设施崩溃。

   校验：全量离线门槛 **564 passed、0 failed**。

另有一处**未定性**的问题必须披露：CI 运行 `37759702346`（提交 `a2aae15`）的
`offline (windows-latest, 3.11, pytest>=8,<9)` 这一个 job 失败，用例
`test_the_fixed_version_never_reaches_the_exploration` 报 `TaskState.FAILED` 而不是 `DONE`，同时 SDK
打出 `Model offline exhausted all 1 attempt(s)`。同一个测试在前后两个提交、以及本地连跑 20 次（又加
6 个 CPU 负载进程连跑 8 次）都是通过的，所以它是间歇性的、只在这个 CI 组合上出现过一次。已记录，尚未定位。

另需说明：CI 的 `test_learning_timeout_preserves_sealed_result` 失败跑在提交 `348796b6` 上，**是本文件
所记改动的父提交**，与第 3、4 条都无关。

## 八、补齐没跑起来的用例，以及由此得到的架构结论（同日补充）

第三节里 7 例 "NOT_PREPARED" 是**没有环境**，不是没来得及。把环境补上之后，这四个 pytest 用例第一次真正
跑了起来，结果反而更清楚地暴露了架构问题。

### 原来卡在哪（两个都可复现）

1. **目标仓库必须以源码形式被 `import pytest` 找到。** 产品开跑前有一次环境探针：
   `python -c "import pytest; print(pytest.__version__)"`，它**不带 `PYTHONPATH`**。而 pytest / sympy
   这些仓库自己就是被测对象，代码在 `src/` 下、并未安装，探针于是 `ModuleNotFoundError`，任务在第 3 个
   事件、**一次模型调用都没发生**时就判 `BLOCKED`。这正是官方镜像里 `pip install -e .` 存在的原因。
2. **构建期生成的版本模块。** `pytest/__init__.py` 要 `from ._version import version`，那个文件由构建期
   写入。写进 checkout 会留下未跟踪文件、破坏冻结源码校验，所以改由环境提供：一个**只在 `PYTHONPATH`
   上确实有 checkout 时才安装**的 finder —— 普通 `import pytest` 仍报告安装版（4.4.2 / 6.2.5 / 7.0.1），
   checkout 在场时报告数据集标注的版本（4.4 / 6.3 / 7.0）。

第一次重跑（`sdk-holdout-002`）把第一条原样撞了出来：7 例全部在第 3 个事件 `environment.probed`
（exit 1）就判 `BLOCKED`。那一轮**保留为证据，没有被覆盖**。

### 补上了什么

按官方 `exec_spec.json` 的规格建环境——**7 例全部指定 Python 3.9**，并采用官方锁定版本：

| 用例 | 环境 | 数据集自己的判定节点 | 对照 |
| --- | --- | --- | --- |
| pytest-dev__pytest-5221 | py3.9 / pluggy 0.13.1 / attrs 23.1.0 | `TestShowFixtures::test_show_fixtures` 等 2 个 | **discriminating** |
| pytest-dev__pytest-5227 | 同上 | `test_log_cli_default_level` 等 3 个 | **discriminating** |
| pytest-dev__pytest-8365 | py3.9 / iniconfig 2.0 / toml 0.10.2 | `test_tmp_path_factory_handles_invalid_dir_characters` | **discriminating** |
| pytest-dev__pytest-8906 | py3.9 / pluggy 0.13.1 | `test_module_level_skip_error` | **discriminating** |
| sympy ×3 | —— | —— | **结构性排除**，见下 |

"discriminating" 是用**数据集自己的测试**跑出来的：原版失败、修复版通过。回执在
`.local/swt-bench/reference-controls-py39-002/verification.json`。

**3 个 sympy 是结构性排除，不是没做。** 官方对 sympy 用的是 **`bin/test`——sympy 自己的测试运行器**，
不是 pytest；`FAIL_TO_PASS` 记的是 `test_idiff` 这种裸名字，本地这套 pytest harness 无法选择。而且这
三例在官方判分里**两轮都是 1800 秒超时 error**，永远出不了官方分。跑它们只会得到一份既无法验证、也
无法判分的本地数据。

### 跑出来的结果（`sdk-holdout-004`，7 例执行，未被中断）

2,780,155 token、140 次 HTTP 尝试、单例耗时 p50 63.6 秒。**交付候选 0 例，差分 0 例。**

| 用例 | 实际动作 | 最后一个发布动作时的剩余步数 | 结束于 |
| --- | --- | --- | --- |
| pytest-5221 | Grep 5、Read 14 | 从未发布 | EXHAUSTED |
| pytest-5227 | Grep 7、Read 11 | 从未发布 | EXHAUSTED |
| pytest-8365 | Read 11、Grep 2、Glob 1、write_candidate 1、revise_contract 5 | revise_contract，**0** | EXHAUSTED |
| pytest-8906 | Read 6、Grep 2、write_candidate 3、revise_contract 7 | request_information，2 | NEEDS_INFORMATION |
| sphinx-11445 | Grep 3、Read 5 | 从未发布 | FAILED / MODEL_PROTOCOL_ERROR |
| sphinx-8474 | Grep 7、Read 11、write_candidate 1 | write_candidate，**0** | EXHAUSTED |
| sphinx-8627 | Glob 1、Grep 9、Read 6、write_candidate 2 | write_candidate，**1** | BLOCKED / ENVIRONMENT_BLOCKED |

**这是本轮最具体的一条架构结论：发布预留解决了"从不尝试发布"，没有解决"来得及发布"。** 第 7 节记录
的那个修复让中间件在剩余 ≤ 3 步时拒绝 `Read`/`Grep`/`Glob`，模型因此确实转向了 `write_candidate`——
4 例都调了——但它是在**剩余 0～1 步**时才调的，候选还没落地预算就没了。预留只拒绝了读取工具，没有
拒绝 `revise_contract` 和 `request_information`，模型可以把那 3 步继续花在别处。

### 同配置三次运行给出三组结果

`sdk-holdout-001`（3 例）、`sdk-holdout-003`（被中断，不可用）、`sdk-holdout-004`（7 例）**用的是同一
批绑定、同一份冻结配置**，而同样 3 个 sphinx 用例的结果完全不同：

| 用例 | 001 | 004 |
| --- | --- | --- |
| sphinx-11445 | BLOCKED / ENVIRONMENT_BLOCKED | FAILED / MODEL_PROTOCOL_ERROR |
| sphinx-8474 | NEEDS_INFORMATION | EXHAUSTED |
| sphinx-8627 | DONE / **DIFFERENTIAL_VALIDATED** | BLOCKED / ENVIRONMENT_BLOCKED |

原因是模型配置 `examples/model.deepseek.non-thinking.json` **没有设置温度**，用的是服务端默认值。
**所以本文件此前那些"各中 1 例"的对比都落在噪声里**：单轮、3～7 例，说明不了两套架构的差别。要拿到
可比较的数字，至少需要固定温度、并在同一配置下重复多轮。这一点同样适用于第五节的官方 1 → 0。

### 这条结论已经变成了一次修复（提交 `e228f10`）

上面那句"发布预留解决了从不尝试发布、没有解决来得及发布"的**根因**是：预留的开关不只看步数，还要看
契约是否有据（`bool(contract.expected) and not contract.missing_information`）。设计意图写在
`test_the_last_steps_are_reserved_for_publishing` 的 docstring 里——契约缺事实时**故意不拦读**，因为读
正是把事实找出来的手段。但那条路径**随后没有任何东西让它结束**：预留是 ReAct 循环里唯一的约束，它一关，
阶段就一路读到预算耗尽，连阶段结果都不产生。

判定已经改成"只剩预留步数就拒绝读取，不论契约状态"，并且拒绝消息按契约状态给出**那个阶段真正有的出口**：
有据的契约 → 发布候选；缺事实的契约 → 用 `revise_contract` 引用已读到的证据，或 `request_information`
报告缺什么。两个出口都是终态，所以阶段现在必然以某个结果收尾，而不是以预算收尾。

本节上面所有数字都出自改动之前的实现。验证需要重新冻结、重跑，而按上面那条噪声结论，重跑还必须先固定
温度，否则测不出差别——两件事都做了，结果见下一条。另外，产品此前**没有温度这个设置**，所以为它加了一个
可选字段（提交 `88eb3b4`），并把"关闭思维链 + 温度 0"固化成 `examples/model.deepseek.deterministic.json`。

### 修复后的验证轮（`sdk-holdout-005`，提交 `88eb3b4`，温度 0）

同一批 7 例、同一份绑定、同一套预算。冻结回执 `.local/swt-bench/freeze-reserve-fix.json`。

**机制层面——这一步可以直接归因于修复。** 每一例的最后三步：

| 用例 | 004 轮（修复前）最后三步 | 005 轮（修复后）最后三步 |
| --- | --- | --- |
| pytest-5221 | Read(3) Read(2) Read(1) | write_candidate(2) revise_contract(1) revise_contract(0) |
| pytest-5227 | Read(3) Read(2) Grep(1) | revise_contract(2) revise_contract(1) revise_contract(0) |
| pytest-8365 | Read(3) revise_contract(2) Read(1) | write_candidate(2) write_candidate(1) write_candidate(0) |
| pytest-8906 | revise_contract(6) write_candidate(5) … request_information(2) | revise_contract(2) revise_contract(1) write_candidate(0) |
| sphinx-8474 | Read(3) Read(2) Grep(1) | revise_contract(2) revise_contract(1) write_candidate(0) |
| sphinx-11445 | （协议错误，11 步中止） | Read(5) write_candidate(3) write_candidate(2) |
| sphinx-8627 | write_candidate(2) write_candidate(1) | Read(4) Grep(3) write_candidate(2) |

004 轮 7 例里有 5 例的最后三步是纯读取、**一个阶段结果都没有产生**；005 轮**每一例的最后三步都是
`write_candidate` 或 `revise_contract`**，7 例全部产生了阶段结果。

**结果层面：交付候选 0 → 2，差分确认 0 → 2。**

- `sphinx-doc__sphinx-11445`：DONE / DIFFERENTIAL_VALIDATED，修复版对照通过。
- `sphinx-doc__sphinx-8627`：DONE / DIFFERENTIAL_VALIDATED，修复版对照通过。
- 其余 5 例仍是 EXHAUSTED，但现在各自以 `REVISION_REQUESTED` 或 `INVALID_CANDIDATE` 收尾，而不是空手耗尽。

**这一轮同时改了两件事，必须说清楚。** 除了预留修复，温度也第一次固定在 0。所以：**"最后三步不再是读取、
阶段结果从 0/7 变成 7/7"可以直接归因于修复**（那是代码层面的确定事实）；**"差分从 0 变成 2"无法在两个
变量之间拆开**——要拆开需要一轮"温度 0 + 旧预留"的对照，而旧预留已经不存在于代码里了。

**顺带暴露出的下一条缺陷**：`pytest-dev__pytest-8365` 在预留窗口里连发了 **3 次 `write_candidate`，三次
都是 `INVALID_CANDIDATE`**。我在这里先写成"候选质量不够"——**那个判断是错的**，查下去是产品自己的探针在
收集阶段崩了（第 7 节第 5 条）。修改它的是提交 `dc47acb`，不是模型。

**官方判分**：这一轮的 10 例同样送进了独立 harness（第五节的第三列）。结果是 1 例 resolved——正是
`sphinx-doc__sphinx-11445`，也就是本地判为 DIFFERENTIAL_VALIDATED 的同一例；`8627` 的补丁因为容器缺陷
判不了（第五节），其余 8 例是空补丁。

## 九、与历史记录的关系

- 本文件不改写任何旧轮次的结果。
- 迁移前的基线取自 `repro-results/swt-bench/dev20-repaired-001`、`repro-results/swt-bench/holdout-repaired-001`
  与 [2026-10-07 的评测](2026-10-07-swt-repaired.md)。
- 迁移前的保留集官方结论：10 例中 **1 例 resolved**（`sphinx-doc__sphinx-11445`）、6 例 unresolved、
  3 例 error（三个 sympy 实例在 harness 侧报错，没有产出判定）。
- 本轮没有产生任何可用于版本比较的"源改变"轮次：两个轮次的 `source_comparison_status` 都是 verified。

## 十、证据在哪

| 内容 | 位置 |
| --- | --- |
| 冻结配置回执 | `.local/swt-bench/agentscope-freeze.json` |
| dev20 轮次 | `repro-results/sdk-dev20-001/`（`round.json`、`summary.json`、`predictions.jsonl`） |
| 保留集轮次 | `repro-results/sdk-holdout-001/` |
| 迁移前基线 | `repro-results/swt-bench/dev20-repaired-001/`、`repro-results/swt-bench/holdout-repaired-001/` |
| 定点任务 | `repro-results/sdk-fixedpoint-8801-002/` |
| 官方判分 | `repro-results/sdk-holdout-001/`、`repro-results/sdk-holdout-005/` 的 `official-execution.receipt.json`；harness 的 `evaluation_results/`（含 005 的轮级报告与 005-subset 的两例子集报告） |
| 轮次驱动脚本 | `.local/swt-bench/run-sdk-round.ps1`、`.local/swt-bench/official-grade-sdk.sh` |
| 补充轮次（7 例） | `repro-results/sdk-holdout-004/`；环境探针未过的那一轮保留在 `repro-results/sdk-holdout-002/` |
| 修复后的验证轮 | `repro-results/sdk-holdout-005/`，冻结回执 `.local/swt-bench/freeze-reserve-fix.json` |
| 补齐的环境与绑定 | `.local/swt-bench/envs/py39-*`、`.local/swt-bench/cases/<新增 7 例>`、`.local/swt-bench/holdout-task8/bindings-7.json` |
| 数据集对照回执 | `.local/swt-bench/reference-controls-py39-002/verification.json` |
| 版本模块 finder | `.local/swt-bench/version-shim.template.py`（各 py39 环境内 `zz_generated_version.py`） |
