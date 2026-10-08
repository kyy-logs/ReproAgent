# ReproAgent

把 Bug 描述转成 Python/pytest 回归测试，实际运行、核验并独立重放，最后导出测试与证据。
当前安装位置为 `E:\ReproAgent`，迁移说明见 [迁移记录](docs/migration-to-e.md)。

当前为本地 CLI MVP，只有一套基础设施：AgentScope。最新完整离线测试为 554 passed、5 skipped，包含真实 AgentScope SDK 整链路和 SWT 数据/评测链路；原始 Issue 与 Windows 导出/replay 边界的独立审查修复见 [修复记录](docs/reviews/2026-10-08-reviewed-boundary-repairs.md)。SDK 接入阶段曾验证无 SDK 环境为 176 passed、4 skipped。扩展问题修复后，同一批 7 个仓库、20 个历史 Bug 的最终整轮有 20/20 完成重复复现、修复版验证及导出包独立重跑，见 [修复复测报告](docs/expanded-case-repairs.md)。原始累计 11/20 和第一修复轮 18/20 均保留。这些提前准备环境的诊断样本不能推算一般复现率，独立人类评审待进行；本次基础设施迁移没有重跑该批历史案例，也没有真实模型轮次，见 [迁移评测](docs/evaluations/2026-10-07-agentscope-infrastructure.md)。

## 安装与输入

工具解释器需要 Python 3.11+：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

目标项目使用用户提供的解释器，提前准备依赖和 pytest。ReproAgent 不会给目标环境安装包、修业务代码或启动缺失服务。
复制 `examples/task.json` 和 `examples/model.json` 后编辑：

- `repo`、`issue_file`、`output_dir` 相对于 task 配置文件所在目录解析。每次运行使用新的输出目录。
- `language.python` 填目标环境解释器的绝对路径；`target_modules` 指明实际待观察模块，例如 `package.parser`。
- `source_roots` 是项目中的源码根，相对项目目录，例如 `["src", "."]`；省略时根据 src 目录推断。
- `candidate_parent` 选能继承相关 conftest 的测试目录，例如 `tests/unit`。
- `baseline_tests` 可选，先观察已有测试；断言失败会被记录，不会直接判定环境不可用。
- `pytest_args` 支持 `-q/-v/-vv/-s/--disable-warnings/--tb=short/--tb=long/--tb=no`。不支持 Agent 覆盖执行参数。
- 模型接口采用兼容 Chat Completions 的 JSON 文本接口。配置 base_url、模型 ID、API key 的环境变量名称。
不在配置文件写密钥。供应商若不支持 `max_completion_tokens`，将 `output_limit_field` 改为 `max_tokens`。
  DeepSeek 示例为 `examples/model.deepseek.json`，显式关闭思维链且输出上限 4096 的版本为
  `examples/model.deepseek.non-thinking.json`；凭据环境变量为 `DEEPSEEK_API_KEY`；模型可用性以账户实际接口为准。

## 运行与查看

ReproAgent 只有一套基础设施：AgentScope。模型调用、受限文件工具、阶段内执行和消息历史都由 SDK 提供，
复现策略、业务编排、证据核验与独立交付仍属于 ReproAgent，没有第二个运行时可以切换。`--model-backend`
/ `--agent-backend`（启动器的 `-ModelBackend` / `-AgentBackend`）是迁移前的旧参数，现已弃用且不再选择
运行时。安装和结构见 [AgentScope 基础设施说明](docs/agentscope.md)。

Claude Code 接入提供项目级 `/reproagent` Skill，本机也已安装当前用户全局入口，可在任意目标项目使用。在本项目目录启动 `claude`，输入 `/reproagent check` 检查，输入 `/reproagent <Bug 描述或 task.json 路径>` 开始复现，或 `/reproagent inspect <结果目录>` 查看证据。详见 [Claude Code 使用说明](docs/claude-code.md)。

```powershell
$env:REPROAGENT_API_KEY = "your-key"
.\.venv\Scripts\reproagent.exe run --config examples/task.json --model-config examples/model.json
.\.venv\Scripts\reproagent.exe inspect repro-results/task-001
```

run 会调用付费模型；当前离线测试不会调用模型服务。inspect 只读取记录，不继续执行任务。
可选 `--fixed-repo <修复版目录> --fixed-python <解释器>`：同一冻结测试在独立修复副本执行；修复材料不进入 Agent 上下文。

成功顺序为生成候选 → 原始版本失败 → 证据核对 → 新副本重放同一失败 → 导出 → DONE。
一次观察为 SINGLE_OBSERVATION，两次确认升级 REPEATED_OBSERVATION，修复版通过才升级 DIFFERENTIAL_VALIDATED。
修复版不兼容会保留重复观察并记录限制；已申请验证但预算耗尽会返回 EXHAUSTED。

退出码：DONE=0；BLOCKED、NEEDS_INFORMATION、EXHAUSTED=1；配置错误=2；FAILED=3；CANCELLED=130。
确认目标依赖缺失会产生 BLOCKED 及诊断包；候选自己的导入拼写错误属于无效候选。预期来源不足时 Agent 可读取项目文档或已有测试，引用工具返回的证据申请契约修订；新版本会使旧候选成功判定失效。仍需用户补充时返回 NEEDS_INFORMATION。清理失败优先标记 FAILED。
模型打印“reproduced”、pytest 退出码 1、捕获预期的错误或 xfail 都不能单独建立成功。
契约、动作与语义验收结构错误最多纠正两次；动作纠正仍计步；契约仍无效返回 FAILED / MODEL_PROTOCOL_ERROR，验收仍无效保留未确认结论。纠正共用原任务期限、取消和用量记录；明确否定的验收检查不会被重试提升为成功。预期修订只接受工具返回的原始项目引用。

## 导出与复跑

输出目录包含 request/task、不可变契约/候选/快照、每次执行与 Probe、事件日志，以及 `artifacts/reproduction` 或 `artifacts/diagnostic`。
成功包提供 candidate 文件、原安装路径、fixture 哈希、完整源码清单哈希、预期来源文本、语义判定、已接受运行 ID、环境、命令、证据映射、manifest 和独立 replay.py。
用户首先阅读包内自动生成的 `report.md`，再通过链接审查测试和日志。成功包与诊断包均按 [固定模板](src/reproagent/resources/report.md.template) 填入保存的证据，不增加模型调用。模板说明见 [输出说明](docs/technical-highlights-and-output.md)。测试保持原安装路径，报告不再复制测试；原始 Bug 描述独立保留。
将成功包复制到任意目录，在另一份相同 Bug 版本副本中：

```powershell
python path/to/reproduction/replay.py --repo path/to/fresh-buggy-copy --python path/to/project-python --output path/to/new-replay-output --install
```

安装器拒绝覆盖现有文件。回归测试在 Bug 版本失败，故复跑退出 1 是预期现象；核对新 Probe 的实际失败。
诊断包明确标为未验证，不含已接受复现说明。清单不递归哈希自己；脱敏日志同时记录原始和导出哈希。

## 预算

`limits` 可配置，默认最多 20 个 Agent 动作，单命令 60 秒，主任务 900 秒，清理 10 秒，本地收尾 5 秒；
单次工具响应 32 KiB，单次运行 stdout/stderr/Probe 合计 32 MiB，Probe 读取另有 32 MiB 安全上限。错误动作仍计步，分析和验证共用期限与模型用量记录；收尾分块操作检查期限，发布前再次检查。文件系统调用采用协作式期限检查。
缺 usage 或计价时费用为 unknown；配置费率可报告估算。每次 HTTP 尝试单独记账；超时重试无法确认此前计费时，总费用仍是 unknown，同时保留已知小计。此适配器没有可靠费用上界，拒绝 `model_cost_limit` 硬限制。

## Automatic experience library

Optional progressive advice and automatic post-task learning are available. See [experience setup and frozen evaluation](docs/experience.md) and [task example](examples/task.experience.json). Disabled by default; real-model A/B benefit has not been measured.

## Known limitations

- 文件副本隔离不是安全沙箱。目标代码、pytest 插件与生成的测试拥有当前用户的系统与网络权限；只在可信项目和可丢弃环境使用。
- 冻结源码及候选哈希能检测文件变化，无法证明测试没有外部副作用。进程树清理针对受控 Job Object/进程组。
- 原始日志仅保存在本地任务目录，可能含敏感值；模型片段及导出副本只脱敏已知 API key。发布前检查其他凭据。
- xdist、浏览器、自动依赖修复、数据库重置、外部服务自动启动、resume 和多候选并行搜索暂未支持。
- 带不可重置外部前置资源的候选不能升级重复复现；运行不起来时报告环境阻塞，缩小运行范围属于后续优化。
- 语义核对依赖模型，证据引用与硬性检查降低误报，不能替代人的审查。
- 原始 Issue 和契约共同计入探索输入容量；整体放不下时明确停止，不静默删掉原始事实。模型仍可能误解事实，最终依赖证据核验。
- Windows 的导出与独立 replay 支持目录保留长度窗口；子进程 cwd 超过系统可启动长度时明确拒绝。目标 pytest 或依赖自身的长路径限制仍可能阻止测试启动。
- 当前实际验证为 Windows + 工具/目标 Python 3.12.14 + pytest 9.1.1；CI 矩阵（Windows/Ubuntu × 目标 Python 3.10–3.12 × pytest 7.4–9）18/18 通过，含 AgentScope SDK 模块与 SWT 链路。其他组合见 `docs/compatibility.md`，首次执行的失败与修复记录见 `docs/implementation-status.md`。

## 开发与评估

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit tests/integration -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist
```

测试覆盖真实 pytest、进程清理、源码来源、独立导出与 wheel 安装；独立目标环境只装 pytest。
首次打包测试会创建开发测试环境并安装 pytest，不修改用户的目标环境。
`evals/README.md` 说明历史样本审查、修复信息隔离及分母统计。20 个历史案例的原始累计记录见 `evals/cases/cumulative-historical-results.json`，最终修复整轮见 `evals/cases/expanded-final-results.json` 与 `docs/expanded-case-repairs.md`；原始扩展失败保留于 `docs/expanded-case-validation.md`。原始三例 0/3 基线保留于 `docs/historical-case-validation.md`；代表性大样本、独立人类审查与能力比较尚未完成。合成案例首次冒烟结果见 `docs/deepseek-smoke.md`。
SWT-Bench Lite 的固定数据导入、20 个开发样本、独立环境预检、批量运行、标准预测和官方报告导入已接通，见 [评测使用说明](docs/swt-bench.md)。未参与本项目调试的新清单由 `select_holdout` 按固定 seed、只读生成侧公开元数据冻结，再用同一批命令运行；汇总把来源状态与实际执行、原版重复、差分、独立交付、官方判分分开计数，官方报告缺失时显示待判分而不是 0。官方 Docker 判分已在本机 WSL2 + Docker 对新样本轮执行：官方判定 RESOLVED 1/10（`sphinx-doc__sphinx-11445`），整轮因仍有 3 例无 verified 报告而保持待判分；开发首轮结果和准备阻塞均保留，不作为全量基准成绩。
最新 [真实 Bug 评测报告](docs/evaluations/2026-10-07-swt-repaired.md)：冻结实现版本后，原 dev20 复跑 8 例调用模型、本地差分确认 0，计划命名的 Sphinx-8801 定点仍未达到（原因已定位）；同一冻结配置下 10 例未调试新样本中 3 例执行，`sphinx-doc__sphinx-11445` 达到 DIFFERENTIAL_VALIDATED 且导出包在全新副本独立重跑 1/0。官方 harness 已在该轮判分，官方判定 RESOLVED 1/10（正是该例），另有 6 例未解决、3 例无报告，整轮仍记待判分。这是小样本，不代表一般复现率。上一轮见 [2026-10-06 评测报告](docs/evaluations/2026-10-06-swt-development.md)：固定20例中8例调用模型、12例环境阻塞，有效差分交付0。
最新 [基础设施迁移评测](docs/evaluations/2026-10-07-agentscope-infrastructure.md)：迁移代码通过离线整链路验证（MockTransport）。**该迁移验收轮自身**的真指定点、dev20 重跑、冻结保留集与官方 Docker 判分均**未执行**——执行它的会话没有模型凭据，也没有可用的 Linux/Docker，这与上一段已完成的 SWT 修复轮是两个不同轮次，两者的官方判分状态不互相矛盾。结论为"迁移代码验证通过，能力验收未通过"，该轮没有产生任何真实模型结果。
设计、架构与实现计划位于 `docs/superpowers`。
