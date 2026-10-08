# 实现与验证记录

执行范围：先完成实现和离线测试；用户随后提供 DeepSeek 凭据，追加真实模型冒烟及累计 20 个历史案例的扩展验证。不自动部署或发布。

## 已实现

- Python/pytest CLI 与只读 inspect。
- 冻结源码、原子记录、不可变候选与完整哈希绑定。
- Windows Job Object 进程树清理、时间/动作/日志预算。
- 独立 pytest Probe、源码来源与文件保护检查。
- 结构化模型动作、契约来源、硬检查优先的语义核对。
- 新副本重复复现、独立修复版本验证、可移植重跑包及诊断包。
- 成功包和诊断包按固定模板自动生成中文 report.md，链接测试与证据，保留原始问题、脱敏和文件哈希；生成过程无模型调用。
- HTTP 模型接口、离线传输测试、评估统计工具、wheel 与 CI 配置。
- Claude Code 项目级 /reproagent Skill 与 Windows 启动器，真实调用和状态读取已验证，详见 [接入说明](claude-code.md)。
- AgentScope 2.0.9 成为唯一基础设施：模型调用、受限 Glob/Grep/Read 文件工具、阶段内 ReAct 执行与消息
  历史都由 SDK 提供；复现策略、业务编排、证据核验与独立交付仍属于 ReproAgent。旧的两个后端参数保留为
  弃用别名，见 [基础设施说明](agentscope.md)。
- SWT-Bench Lite 固定版本导入、开发样本选择、独立预检、串行运行、标准测试补丁和官方结果来源校验，见 [评测说明](swt-bench.md)。
- 正式评测清单的冻结选择器 `select_holdout`：排除 dev20 与已调试案例，按仓库轮询与固定 seed 从生成侧公开元数据选样本；汇总区分来源状态、实际执行、重复、差分、独立交付与官方判分，官方报告缺失时为待判分。

## 当前证据

最新完整离线测试：**440 passed、4 skipped**（443.33 秒，2026-10-07，AgentScope 成为唯一基础设施后的整链路 + 最终评审修复波新增 3 项；默认 basetemp，`REPROAGENT_RG_PATH` 指向本机 ripgrep）；依赖检查无冲突，4 个 skip 均为本机符号链接权限与 venv 链接不可达。上一轮（2026-10-06，含 SWT 接入及当时的 AgentScope 2.0.9 可选后端）为 244 passed、1 skipped（164.37 秒）；SWT 该轮新增46项测试，包含真实 Git apply、pytest、补丁字节保留、绑定隔离、源码冻结、费用、取消、Unicode 路径及外部回执/缺报告状态。
安装 SDK 前，完整原生环境为 176 passed，4 skipped（111.11 秒）：除符号链接测试外，另跳过三个 SDK 集成模块。新 wheel 独立安装、原生 CLI 和缺依赖提示均通过。
跳过项为 Windows 账户缺少符号链接权限；普通路径越界测试已运行。
测试覆盖真实进程 PID 清理、pytest 阶段和导入异常、src/conftest、候选保护、
独立重放、预算耗尽后的证据、诊断包、同一候选修复验证和只读 CLI。
安装测试构建 wheel 并安装到独立工具环境；另一目标环境只安装 pytest，
实际运行 Probe 时没有 ReproAgent 或 HTTPX。

实际平台：Windows、CPython 3.12.14、pytest 9.1.1。

2026-10-06 报告输出优化：成功和诊断导出均从 resources/report.md.template 填充中文报告。
原始问题独立于契约引用保留；测试与支持数据不新增副本；阅读报告纳入包哈希。
23 项针对性测试覆盖模板、中文链接、原始输入、特殊字符密钥、安装路径、总量限制和收尾期限。
独立 wheel 安装后实际渲染报告，证明模板随发行包提供。
独立审查发现的问题逐项复现并修复，最终复查无遗留发现。
没有重新运行历史模型评估；旧案例与原始包保持原样，模板示例来自历史证据的独立副本。
Windows/Linux × 目标 Python 3.10/3.11/3.12 × pytest 7.4/8/9 已首次执行 CI，18 个矩阵任务全部失败于环境耦合，见下文 CI 记录。
工具自身需要 Python >=3.11。

2026-10-06 AgentScope 接入：模型与 Agent 后端独立可选，四种组合均经过真实 pytest 原版重复失败、隐藏修复版通过及导出包独立重跑。受控策略每次决策仅一次 SDK 模型桥接；无 SDK 业务工具或自主探索循环，继续共享 Controller、BudgetedGateway、Verifier 与导出规则。
22 项真实 SDK 集成测试覆盖原始结束原因、usage、限流尝试、取消和期限、动作/路径约束、状态隔离、错误字段脱敏、流读取上限、压缩响应拒绝与四种组合。独立审查提出的三项问题经 RED 回归修复，最终复查无遗留实际问题。
主工具 .venv 已安装可选依赖，独立 SDK 环境与主工具 pip check 均通过。原生后端仍默认；目标环境仅装 pytest，没有安装 AgentScope。

真实 DeepSeek + AgentScope 双后端合成冒烟仅运行一次：DONE / DIFFERENTIAL_VALIDATED，耗时 22.34 秒，7 次 HTTP 尝试、21844 token，费用 unknown。
生成测试调用真实 parse([])，断言返回 list 与 []，保留非空输入断言；原版两次退出 1、修复版退出 0，导出包在新原版/新修复副本分别退出 1/0。Codex 已检查测试，独立人类审查仍待进行。模型在 observable_checks 增加了已有测试检查，生成候选保留对应非空断言；不能据此声称完全消除语义偏移。
本次没有重测 20 个历史案例，也没有 SDK 策略提升复现率的比较证据。
本地回执为 `.local/agentscope-integration-verification.json`；真实输出报告为 `repro-results/agentscope-smoke-001/artifacts/reproduction/report.md`。

2026-10-06 SWT 接入：固定 SWE-bench Lite revision 和官方 harness commit，实际快照300条、官方排除24条后276条、固定开发子集20条。转换、命令行导入、独立预检和批量汇总已执行。
真实 native/native 开发首轮为2例在当时可运行、1例 REPEATED_OBSERVATION（无差分确认）、1例 EXHAUSTED、18例 NOT_PREPARED。44次 HTTP 尝试、322531 token，费用 unknown。
已接受候选独立重跑原版/修复版均退出1，差分复现未成立；human_judgement保持null。原始问题没有改写为研究者提示，固定补丁与官方新增测试不进入生成输入。
接入中发现 Sphinx Unicode Git 文件名误判，回归修复后独立预检为8 ready、12 blocked；未改写首轮模型结果。接入阶段未重跑修复后的8个环境；后续已完成新轮次，见下述评测记录。不能把该诊断轮当成正式基准分数或一般能力结论。
独立审查发现的材料隔离、官方源码与预测来源、历史报告混入、执行失败及部分导入状态问题已修复，最终复查关闭发现。本机缺少可用 Linux/Docker，官方 harness实际执行和容器兼容性仍未验证；合成报告测试不等于官方成绩。（该状态已于 2026-10-07 改变：官方 harness 已在本机 WSL2 + Docker 实际执行，见 2026-10-07 官方 SWT 判分记录。）
数据解码器在独立工具环境安装，产品 wheel 和目标环境未增加 pyarrow/datasets/Docker SDK。历史20例及旧包未改写。回执为 `.local/swt-integration-verification.json`，首轮报告为 `repro-results/swt-bench/dev20-native-001/report.md`。

2026-10-06 后续真实评测：第二轮 2 例调用模型，6 例 Sphinx 在 Windows 长路径快照阶段阻塞；有效交付 0。第三轮沿用固定20例、模型和预算，以长路径前缀及独立补齐依赖的 Sphinx 环境执行，8 例调用模型，1 DONE/REPEATED_OBSERVATION、7 EXHAUSTED、12 NOT_PREPARED，有效差分交付 0。165 次 HTTP 尝试、1475156 token，费用 unknown，官方判分与人工审查待完成。
参考测试在独立副本核验，其中5例呈现原版失败/修复版通过；这5例 Agent 有效交付0/5。Sphinx-8801 的两个未接受候选独立执行均为1/0，但语义核验证据超32KB导致任务未交付，只用于诊断，不计成功。详见 [真实评测报告](evaluations/2026-10-06-swt-development.md)。本轮评测新增文档和本地环境，未改写旧任务包；运行期间另有 CI/venv 路径处理的并行变更，评测未覆盖这些变更，正式对比前需冻结实现版本。

2026-10-07 评测清单与汇总口径（仅代码，未跑新轮次）：新增 `select_holdout`，从生成侧 catalog 按排除项与 `repos` 顺序做仓库轮询、仓库内按 `sha256(seed + ':' + instance_id)` 排序选出 sealed manifest，只读公开元数据，数据集补丁、参考测试与对照结果不参与，仓库池不足时报错而不静默减量；该 manifest 由现有的 `run`、`preflight`、`control` 命令消费，未新增运行路径或评分算法。
`summarize_round` 增加实际执行、独立交付（确认/重跑未通过/待重跑）与来源状态展示，并引入 `official_grade_status`/`official_rate`：只有全部案例都有带回执的 verified 官方报告才给最终比例，否则记为待判分且比例为 null，不写成 0；官方成绩仍只来自独立 SWT harness，本地 DONE 或原版重复都不能代替。旧轮次无新字段时按 not_recorded / not_provided 读取，仍然可汇总。三个新回归场景先失败后通过。

2026-10-07 冻结版本后的修复轮执行（仅评测运行与文档，未改代码）：按 `.local/swt-bench/task8-freeze.json` 冻结实现提交 `372a6b7`、模型配置 sha256 `325491f4…`（deepseek-flash，`max_output_tokens` 4096，`thinking_mode: disabled`）、native/native 后端与 BudgetLimits 默认预算，费率未配置故费用 unknown。原 dev20 复跑 20 例，8 例准备可用并执行，本地重复确认 4、本地差分确认 0，修复版对照 0 通过 / 4 未通过 / 0 受阻 / 16 未提供，121 次 HTTP 尝试、953215 token，来源状态 verified；计划命名的已知定点 Sphinx-8801 未达到（本轮 BLOCKED，尽管产生 4 候选、6 运行、3 个 REPRODUCED 判决），定位到失败签名稳定器未覆盖 run 根之外的逐次状态与候选级环境不兼容两个原因，均记录未修复。随后用 `select_holdout` 在同一 catalog 冻结 10 例未调试新样本（seed `reproagent-swt-holdout-v1`，排除 dev20；本项目历史 20 例 id 与 catalog 无交集），3 例执行，`sphinx-doc__sphinx-11445` 达到 DIFFERENTIAL_VALIDATED，导出包在全新原版/修复版副本独立重跑为退出码 1 / 0，47 次 HTTP 尝试、385455 token；为 Sphinx 7.1 新建固定环境 `.local/swt-bench/envs-pinned/sphinx-doc__sphinx-7.1`。两轮合计 30 例、11 例实际调用模型、168 次 HTTP 尝试、1338670 token。官方 Docker 判分见下一条；小样本不代表一般复现率。详见 [2026-10-07 修复后评测报告](evaluations/2026-10-07-swt-repaired.md)。

2026-10-07 官方 SWT 判分（仅文档与一处工具修复）：在本机 WSL2 Ubuntu + Docker 上运行固定 harness（commit `330a649a…`）的 unit_test 模式，同一冻结 snapshot 与预测、`--max_workers 1`，对新样本轮 10 例判分。官方结论：RESOLVED 1（`sphinx-doc__sphinx-11445`，即本地判为 DIFFERENTIAL_VALIDATED 的同一例）、未解决 6、无官方报告 3（sympy 三例均为 `Command '/bin/bash /eval.sh' timed out after 1800 seconds`，harness 自身默认上限）；接受的运行构建失败 0，早期尝试的若干镜像构建失败（容器内 `/root/setup_repo.sh` 返回 128，仓库拉取失败）经让容器构建走代理后消失。本工具回执为 `exit_code 0`、无 stop_reason、7 份 instance 报告哈希，报告核验后导入 `repro-results/swt-bench/holdout-repaired-001/official/`；聚合 `official_graded 7`、`official_successes 1`、`official_not_verified 3`、`official_grade_status pending`、`official_rate` null，1/10 不写作比率。dev20 修复轮未官方判分：20 例中只有 4 例带非空模型补丁，且这 4 例本地修复版验证已全部未通过。
该工作暴露并修复了一个 harness 集成缺陷（commit `fd872ff`）：`check_harness` 只排除了 harness 自身的三个日志/报告目录，而 harness 运行期间会创建 `locks/`（其 Locker 文件），运行后的复检因此把一次已完成的运行判为 `infra_error` / `HARNESS_SOURCE_CHANGED` 并覆盖其判决；现把 `locks` 与 harness 自己的输出目录一并排除，并有单元测试同时断言“运行自身的输出目录不算源码变化”和“其他位置的新文件仍算变化”。

2026-10-06 CI 首次执行（Windows/Linux × 目标 Python 3.10/3.11/3.12 × pytest 7.4/8/9，共 18 个矩阵任务）全部失败，合并后为三类环境耦合问题，均已定位到根因。
其一，dev 依赖未声明 setuptools，而打包测试用 --no-build-isolation 构建 wheel；Python 3.12 的 venv 不再自带 setuptools，本地 .venv 恰有而未暴露。
其二，目标解释器路径被 Path.resolve 解析；Linux 上 venv 与工具链的 bin/python 是指向基础解释器的符号链接，解析后字符串与 sys.executable 不一致，且 venv 的链接间接层被替换为链接目标。共四处同样写法：语言适配器、导出包内的 replay.py、评测绑定层 `evals/swt_bench/prepare.py` 与绑定装载 `evals/swt_bench/__main__.py`。
其三，CI 装在 runner 全局解释器上，未创建启动器与 Skill 依赖的 <repo>/.venv，5 个启动器测试报缺工具解释器。
修复为：dev 增加 setuptools>=68；四处解释器路径改为绝对但不解析链接；CI 按 README 创建 .venv 并在其中安装与运行。
CI 同时改为安装 .[dev,agentscope]：此前不装该可选依赖时，3 个 SDK 集成模块的 module 级 importorskip 使约 22 个用例塌缩为 3 条 skip，CI 全绿也不覆盖 SDK 路径。
验证：三类失败在本地逐一复现（含用目录 junction 复现链接解析差异；文件符号链接在 Windows 需提权，venv 后果由下述 CI 直接暴露）；克隆出无 .venv 的仓库并按 workflow 步骤安装后，全量离线测试 244 passed、1 skipped，pip check 无冲突。
首次执行回执：https://github.com/kyy-logs/ReproAgent/actions/runs/37455797100
第二次执行（runs/37463036547）：Windows 9/9 通过，Ubuntu 9/9 失败，失败集中在 SWT 评测链路 7 项，根因为同一 resolve 缺陷的评测绑定层实例。该失败直接证实此前只能推理的后果：解析后的解释器不在原 venv 内，`import pytest` 失败，预检报 blocked，下游用例不启动。补齐评测层修复后本地全量 244 passed、1 skipped。第三次执行（runs/37464232536）18/18 通过：Windows 245 passed，Ubuntu 240 passed、5 skipped（仅 Windows 启动器用例），两侧 pip check 均无冲突；SDK 集成模块与 SWT 链路在两种平台均实际执行，不再塌缩为模块级 skip。该组合矩阵至此为已执行并通过；三种目标 Python 与 pytest 版本的实际运行记录以各任务日志为准。

独立整体验证审查已完成，提出的 11 项重要问题已修复。
针对这些问题新增 17 个回归场景，先全部失败后全部通过；另补充触发条件修订的来源约束测试。
修复包括完整测试节点与修复版一致性、辅助文件和目标来源保护、收尾期限、Probe 大小限制、
统一模型消息脱敏、逐次 HTTP 尝试计费、健康任务 inspect、有限文件索引、独立导出证据链、
依赖阻塞与可持久化的契约修订。最后完整回归包括修复与 wheel 安装验证。

仍有三个次要优化记录：HTTP 响应大小检查发生在缓冲之后；包内任务状态保留导出时状态；
本地兼容性复跑的目标环境缓存尚未按版本区分。CI 新任务使用新环境，尚未实际执行。

## 后续验证

首次 DeepSeek 接入已验证，合成案例三轮运行均为 EXHAUSTED / NONE；已定位契约分析将非必需信息列为缺失项的问题。
共 28 次模型请求、38844 token，未执行候选测试，不能视为复现成功。细节见 `deepseek-smoke.md`。

已追加 3 个公开历史 Bug 的真实模型冒烟：环境均可运行，人工 pytest 对照在原版失败、修复版通过，
Agent 自动成功 0/3（2 个 EXHAUSTED，1 个 NEEDS_INFORMATION，均为 NONE）。
共 27 次模型请求、77545 token。新增定位：search_code 枚举未说明、证据/仓库路径混淆、契约字段类型不受明确约束。
该原始基线保留于 `historical-case-validation.md`。随后已修复工具参数说明、缺失信息范围、
契约/验收结构与有限纠正、来源修订保护、异常响应用量记录，新增 25 个回归场景。
中间失败轮也保留；最终使用相同模型、输入、提交与预算重跑，三个案例全部 DONE / DIFFERENTIAL_VALIDATED，
并在新原版、新修复版副本独立重跑导出包成功。最终轮 28 次模型请求、109962 token。
Codex 已检查生成测试正确性，独立人类审查仍待进行；详见 `historical-case-fixes.md`。

已追加 17 个历史 Bug、5 个新仓库，累计 20 个不同 Bug、7 个仓库。
新增轮 8/17 为 DONE / DIFFERENTIAL_VALIDATED，8 个导出包均在新原版失败、新修复版通过；
9 个 EXHAUSTED，其中 6 个未生成候选、1 个未执行、1 个只有一次运行，另 1 个因对象 repr 内存地址变化被重复失败签名比较拒绝。
新增轮 216 次 HTTP 尝试、1093322 token；费用未知。所有新增例的研究者对照均原版失败、修复版通过，未向 Agent 提供这些测试。
源码、提示及预算在本轮冻结；没有用修改预算或重试替换失败。此前离线测试未在这次纯评估扩展中重跑。
旧三例修复后的最终轮加本轮首次尝试累计 11/20 完成自动流程，不能视作 20 个首次尝试的基准成绩。
Codex 已检查新增 8 个成功测试，独立人类正确性审查与通用 Agent 对照实验仍待进行。没有一般复现率或费用基准。
详见 `expanded-case-validation.md`，原始及中间失败回执仍保留。

## 后续产品优化

扩展验证中优先定位到重复失败签名对对象 repr 易变地址过于敏感、模型读源码耗尽动作预算、
动作 JSON 格式及测试目录错误。先修签名稳定性，再做预留执行步骤和协议可靠性的对照实验。
原始扩展轮的 9 个失败仍保留。随后已修复上述流程问题及目标 SyntaxError 归因、原始事实保留和返回/抛出语义。第一修复整轮为 18/20，最终整轮为 20/20 完成重复复现、修复版对照及独立导出重跑；详细变更、用量及限制见 [修复复测报告](expanded-case-repairs.md)。这些结果不能作为一般复现率，独立人类评审仍待进行。

依照原设计，环境跑不起来时先报告阻塞。后续可增加依赖/服务诊断、
在用户授权下修复环境、缩小复现范围、外部资源重置和容器隔离；
这些能力没有混入当前 MVP 成功判定。

## 已知限制（2026-10-07 记录）

以下为迁移当日的历史记录；原始 Issue 输入与 replay 目录长度窗口已在 2026-10-08 修复，见文末修复记录。

- **探索阶段的输入没有原始 Issue 正文。** 阶段收到的是契约、阶段历史与反馈；契约携带
  trigger/expected/reported_actual/observable_checks。两个提示词都固定了"返回 X"与"抛出 X"的区别，
  但阶段输入里没有任何能反驳这份契约的材料：如果契约把"返回一个 ValidationError"改写成"抛出一个
  ValidationError"，阶段只能照它执行。这条限制由 Task 6 披露并保留，不是新发现。
- **导出包内的 `replay.py` 不为目录长度预留余量。** 它直接镜像 `workspace_path`，普通路径在 248–259
  字符区间时可能触发 `WinError 206`；它的子进程工作目录也会碰到工具自身已经在别处拒绝的 259 字符
  上限。同时长路径前缀可以避免该区间，但这条余量本身没有实现。
- 以上两条都在现有测试之外，没有对应的回归测试；它们作为已知限制公布，不隐藏在成功描述里。

## 2026-10-07 基础设施迁移（离线）

AgentScope 成为唯一基础设施：`create_controller` 只装配 SDK gateway 与 SDK explorer 工厂；旧 native
执行路径、动作循环策略引擎及其测试已删除；`agentscope==2.0.9` 改为 `pyproject.toml` 的主依赖，
`[agentscope]` extra 保留为空以兼容旧安装命令。

离线验收：`tests/unit tests/integration` 全量 **437 passed、4 skipped**（408.46 秒，默认 basetemp，
`REPROAGENT_RG_PATH` 指向本机 ripgrep）；`pip check` 无冲突。4 个 skip 都是本机符号链接权限与 venv
链接不可达，没有一条来自 `importorskip`。新增整链路测试用 MockTransport 走完
Glob → Grep → Read → `write_candidate`，再由 Controller 自动执行原版、结构化核验、独立重复、固定版
执行与导出，导出包在新原版/新修复副本分别退出 1/0。该测试断言探索 wire 携带 tools、领域请求是无 tools
的结构化请求、没有额外的 run/submit 决策、隐藏修复版不进入模型输入、以及纯文字的"已复现"结尾不会
成为成功。

必需 SDK 测试不再使用 `module` 级 `pytest.importorskip`：SDK 是主依赖，缺失时必须像产品一样报安装
错误，而不是让这些模块静默跳过（`test_windows_long_paths.py` 内在 helper 级的那个保留，因为该模块
同时承载不依赖 SDK 的路径与 venv 回归）。

**真实模型步骤未执行。** 本环境没有 `REPROAGENT_API_KEY`、`DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY`，
也没有可用的 Linux/Docker，因此 Sphinx-8801 定点任务、dev20 重跑、冻结保留集执行和官方 harness 判分
都没有运行，没有产生任何真实模型结果，也没有可用于对比的 HTTP/token 计数。保留集 manifest 已按固定
策略离线冻结（见 [评测说明](swt-bench.md)），但**没有执行**。结论：**迁移代码验证通过，能力验收未通过。**
详见 [基础设施迁移评测](evaluations/2026-10-07-agentscope-infrastructure.md)。
