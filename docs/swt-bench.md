# 用 SWT-Bench Lite 评测 ReproAgent

评测工具位于 `evals/datasets` 和 `evals/swt_bench`，在仓库根目录运行，不随产品 wheel 安装。工具解释器仍用 `.venv`；目标项目使用各自准备的 Python/pytest。数据获取器的 Parquet 依赖单独安装，产品和目标环境不需要 datasets、pyarrow 或 Docker SDK。

## 本机已准备的资料

- 最新真实评测见 [2026-10-06 评测报告](evaluations/2026-10-06-swt-development.md)：第三轮 20 例中 8 例调用模型、12 例环境阻塞，有效差分交付 0；参考测试可区分版本的 5 例中为 0/5。官方判分未运行。
- 固定快照与来源：`.local/swt-bench/data/snapshot.json`、`source.json`、`excluded.txt`。
- 原始 test split 300 条；按官方 24 条排除项得到 276 条。过滤文件是排除项，不是白名单。
- 生成资料 catalog：`.local/swt-bench/data/catalog.json`，不含 patch、test_patch、hints_text 内容。
- 固定开发清单：`.local/swt-bench/data/dev20.json`。五仓库、20 例，按固定 seed 和仓库轮询选取。
- 原版/修复副本和独立解释器：`.local/swt-bench/cases`、`envs`；绑定为 `.local/swt-bench/bindings.json`。
- 按原版 version 固定的对照绑定与依赖清单：`.local/swt-bench/reference-controls-006/bindings.json`、`dependencies.json`；本轮对照回执在 `reference-controls-006/controls`。
- 修复后的独立预检：`.local/swt-bench/preflight-after-repairs/preparation.json`，20 例中 8 例 ready、12 例 blocked。
- 真实模型首轮：`repro-results/swt-bench/dev20-native-001`，其报告、模型请求和失败记录都保留。

来源固定为 [SWE-bench Lite revision](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite/tree/6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2) 与 [SWT harness commit](https://github.com/logic-star-ai/swt-bench/tree/330a649a764fab2fadaea632776eeae87272f74b)。完整快照、排除项和 manifest 都有哈希绑定。此开发子集偏向五个项目，不代表全量 Lite 或独立保留集。

## 获取与导入

当前数据已下载；新机器可在独立工具环境安装 Parquet 解码器：

```powershell
cd E:\ReproAgent
.\.venv\Scripts\python.exe -m venv .local/swt-data-env
.\.local\swt-data-env\Scripts\python.exe -m pip install pyarrow
.\.venv\Scripts\python.exe -m evals.swt_bench fetch `
  --output .local/swt-bench/download-new `
  --decoder-python E:/ReproAgent/.local/swt-data-env/Scripts/python.exe
```

只有 fetch 联网。它下载固定版本，不执行数据集脚本。导入、预检、汇总默认不调用模型：

```powershell
.\.venv\Scripts\python.exe -m evals.swt_bench import `
  --snapshot .local/swt-bench/download-new/snapshot.json `
  --filter .local/swt-bench/download-new/excluded.txt `
  --source .local/swt-bench/download-new/source.json `
  --output .local/swt-bench/import-new
```

输出 catalog.json 和 dev20.json。网络获取、导入与运行均使用新目录，拒绝覆盖。原始问题按字节哈希绑定，Unicode/Emoji 不经 Windows GBK 改写。

### 冻结未参与调试的新清单

固定开发清单是调试用的，不能当作正式评测。正式清单用 `evals.datasets.swt_bench.select_holdout` 从同一 catalog 选出：传入 catalog、排除项集合（dev20 的身份加已调试历史案例）、`repos=` 已支持 Python/pytest 的仓库顺序，以及固定 `seed`（默认 `reproagent-swt-holdout-v1`）。选择只读生成侧公开元数据，按仓库轮询取每个仓库内 `sha256(seed + ':' + instance_id)` 最小的样本；数据集修复补丁、参考测试和参考对照结果都不是输入，生成结果也不参与，因此同一 catalog、排除项、仓库顺序与 seed 必然得到同一份 sealed manifest。仓库池不足时直接报错，不静默减少样本。缺绑定的案例仍留在分母里：选择在任何案例运行前冻结，失败不替换。

清单以 sealed manifest 落盘后，用现有的 `run`、`preflight`、`control` 命令消费，不新增运行路径：

```python
# freeze-holdout.py：用本仓库 .venv 解释器运行，写出冻结清单
import json

from evals.datasets.swt_bench import DEV_REPOS, select_holdout

catalog = json.load(open('.local/swt-bench/data/catalog.json', encoding='utf-8'))
development = json.load(open('.local/swt-bench/data/dev20.json', encoding='utf-8'))
debugged_ids = set()  # 已调试历史案例的身份，按实际清单补齐
excluded = {case['instance_id'] for case in development['cases']} | debugged_ids
manifest = select_holdout(catalog, excluded, repos=DEV_REPOS, count=10)
open('.local/swt-bench/data/holdout10.json', 'w', encoding='utf-8').write(json.dumps(manifest, ensure_ascii=False))
```

该清单只说明“本项目未调试过”，不保证模型训练未见过这些公开数据。同一轮内若来源指纹变化，该轮不可用于对比。

## 项目环境与绑定

每例绑定指定 instance_id、buggy_repo、fixed_repo、buggy_python、fixed_python、target_modules、source_roots、candidate_parent、pytest_args、可选 baseline_tests、fix_patch_hash、fixed_source_hash、review_status 与 review_actor。

路径相对于 bindings JSON 所在目录；解释器可用绝对路径。现有 bindings.json 提供完整实用示例。原版须为 base_commit 的干净 checkout；忽略但未跟踪的普通文件也会拒绝，防止隐藏资料被复制进生成快照。修复版为同一原版应用数据集 patch 后的独立副本，绑定补丁哈希和源码哈希。官方 test_patch 只用于独立控制实验，不放进生成或修复验证仓库。

target_modules 与测试目录根据原版项目确定。sympy 的测试可位于包内；不能统一假定项目都有顶层 tests。源目录、缓存、固定版本和输出目录要隔离。只将已技术核查的绑定标为 approved，并记录 review_actor；该状态不等于最终人工正确性审查通过。

先运行独立预检，无需密钥：

```powershell
.\.venv\Scripts\python.exe -m evals.swt_bench preflight `
  --catalog .local/swt-bench/data/catalog.json `
  --manifest .local/swt-bench/data/dev20.json `
  --bindings .local/swt-bench/bindings.json `
  --output .local/swt-bench/preflight-new
```

ready 表示原版/修复版的 Python、pytest 和指定目标模块来源探测通过，不保证所有 fixture、构建器或测试都能运行。缺依赖、旧 Python/pytest 不兼容等记录为 blocked。控制台输出编码和 Git 路径通过 UTF-8 与 NUL 分隔处理，不把合法 Unicode 文件名误当隐藏资料。

本机准备使用 Python 3.12，明确不同于官方项目旧环境；没有偷偷改业务源码来兼容。这些本地环境问题不能当成 Agent 的 Bug 复现能力结论。

## 运行与后端对照

在启动终端配置 `examples/model.deepseek.json` 所需的 `DEEPSEEK_API_KEY` 环境变量，按已授权模型配置运行。不要把密钥放入数据或配置。run 会调用模型服务；默认串行，20 动作、单命令 60 秒、每任务 900 秒。

```powershell
.\.venv\Scripts\python.exe -m evals.swt_bench run `
  --catalog .local/swt-bench/data/catalog.json `
  --manifest .local/swt-bench/data/dev20.json `
  --bindings .local/swt-bench/bindings.json `
  --model-config examples/model.deepseek.json `
  --model-backend native --agent-backend native `
  --output repro-results/swt-bench/dev20-native-next
```

`--limits <JSON>` 可显式配置统一预算；`--name <简单标签>` 指定预测 model_name_or_path。两个后端都支持 native/agentscope。策略比较时固定模型后端，只替换 Agent 策略；每组合使用新轮次，不挑各组最佳结果合并。

批量入口退出 0 表示记录流程完成，不代表所有 Bug 复现成功。NOT_PREPARED、EXHAUSTED、FAILED、CANCELLED 保留在分母。没有准备的绑定不会调用模型；取消后不再启动后续案例。原任务协议内纠正仍有限，任务失败后不会自动加预算重跑。

运行输出：round.json、summary.json、report.md、manifest.json、bindings.json、cases 下的任务记录，以及每个选定 ID 都有一行的 predictions.jsonl。候选冻结时再次检查已审查源码哈希，避免预检之后的变化进入模型。

预测使用已接受候选的原始字节和原安装路径。只增加受限测试目录中的 test/data 文件，拒绝覆盖源码、越界、链接或篡改。空文件、UTF-8 内容及无末尾换行得到保留；当前补丁文件名支持常规 ASCII 路径，其他文件名报 export_error。失败样本写空 model_patch，完整原因仍在 ledger。不要仅导出成功样本。

## 环境参考对照

参考对照证明目标测试确实执行过，而不是只看退出码：在独立副本里套用数据集修复补丁与参考测试，原版至少一个目标 call 失败、修复版全部目标 call 通过，且没有 collection、setup 或 teardown 错误、没有 skip，才算能区分版本。collection 报错、fixture setup 抛错和 skip 都会给出与“原版非零、修复版零”相同的形状，却没有真正执行目标测试。

```powershell
.\.venv\Scripts\python.exe -m evals.swt_bench control `
  --snapshot .local/swt-bench/data/snapshot.json `
  --manifest .local/swt-bench/data/dev20.json `
  --bindings .local/swt-bench/reference-controls-006/bindings.json `
  --output .local/swt-bench/reference-controls-006/controls
```

row 中的 patch、test_patch 与 FAIL_TO_PASS 只在独立评测端读取，不进入 run_case 的生成输入。每个副本是新目录加自己的 Git 仓库，参考补丁套用后按内容校验：返回 0 却没有改动任何目标文件记为 patch_error，外层 Git 忽略规则无法造成假对照；套用修复补丁后的源码哈希还必须等于已审查绑定的哈希。目标解释器子进程的环境按白名单重建，操作者导出的密钥不会传进去。逐例留下 control.json、buggy/fixed 的 stdout/stderr 日志与套用过的补丁；verification.json 保留固定清单的全部案例，缺绑定的记为 not_run。

Sphinx 按原版 version 分别固定扩展依赖：sphinxcontrib-applehelp 2.x 要求 Sphinx >= 5，1.0.x 接受 3.x，所以 3.4/3.5 与 5.0 使用不同解释器与依赖集合。解释器、Python/pytest 版本、依赖集合哈希、源码哈希与补丁都进回执，见 `reference-controls-006/dependencies.json`。解释器缺失或无法启动、构建产物不全的案例仍为环境阻塞，继续留在分母中，不改业务代码、不放宽文件隔离。

## 官方独立判分

本机未安装可用 Docker/WSL，本次没有实际运行官方 harness，因此没有官方分数。

在准备好的 Linux/Docker 环境中，checkout 上述固定 harness commit，按官方说明安装依赖。复制本轮预测/来源记录和固定 snapshot.json；通过仓库评测工具执行：

```bash
python -m evals.swt_bench official-run \
  --round /path/to/copied-round \
  --harness /path/to/pinned-swt-bench \
  --python /path/to/harness-venv/bin/python \
  --snapshot /path/to/pinned-snapshot.json
```

调用使用本地固定快照，不重新从浮动 Hugging Face 数据名称获取 patch。执行前后验证 harness HEAD、实际源码、非跟踪输入与源码哈希，并核对预测文件。生成 provenance receipt，再导入当前 run/model 的逐例官方报告。旧轮次日志不会混入。

也可在 harness 内参考 official-command.sh 手动执行（设置 REPROAGENT_ROUND 和 REPROAGENT_SWT_SNAPSHOT）。首轮旧模板另保留，使用其修正后的 official-command-pinned.sh。手动执行的报告如无本工具可核验的执行回执，导入后标 imported_unverified：

```powershell
.\.venv\Scripts\python.exe -m evals.swt_bench import-reports `
  --round <round-directory> --reports <run_instance_swt_logs>
```

若有本工具生成的来源回执，追加 `--receipt <official-execution.receipt.json>`。来源包含 run/model、manifest、预测字节、dataset snapshot、执行源码与逐例报告哈希。无报告为 not_run/missing；执行超时、启动失败、非零退出或源码变化会记录 infra_error 与失败回执。超时/失败不证明容器或所有外部资源已经清理；在可丢弃评测环境中检查官方 harness 的资源状态。

官方判分来自 unit_test 模式，不由本地 DONE 或 pytest 退出码替代。解析器的合成测试和命令验证不能证明实际 Docker 兼容性。官方报告未齐全时保留 pending，整体比例仅是暂定下界，不作为正式成绩。

## 已执行的开发诊断

首轮 native/native 保留在 dev20-native-001：2 例在当时通过准备检查；Flask-4992 达到 REPEATED_OBSERVATION，但未通过修复版；Flask-5063 为 EXHAUSTED，其余 18 例 NOT_PREPARED。

共 44 次记录的 HTTP 尝试、322531 token，费率未配置，费用 unknown。Flask-4992 的导出在新原版和新修复版都退出 1，故差分独立重跑为 false。测试采用 Issue 提案中的 mode="b"；提供的修复版不接受该候选，不能算有效差分复现。Codex 已技术检查，human_judgement 仍为 null。

首轮有 6 个 Sphinx 绑定因 Git 的 Unicode 文件名输出被误判；该实现问题已通过回归修复。重新预检为 8 ready、12 blocked，保存于独立目录；没有更改首轮模型结果，也没有把旧结果转换成新代码的能力分数。接入阶段未重跑这 8 例；后续已完成第二、第三轮真实模型评测及独立重跑，见上述最新报告。仍不宣称一般复现率或后端提升。

旧项目 20 个历史案例仍用于开发回归。正式评测需另冻结未参与本项目调试的样本，并检查历史重叠；公开数据也可能被模型训练见过。

重新生成汇总不调用模型：

```powershell
.\.venv\Scripts\python.exe -m evals.swt_bench summarize --round repro-results/swt-bench/dev20-native-001
```

汇总把来源状态与各类计数分开列出，互不代偿：选定样本、准备可用、实际执行（运行器真正启动的案例）、本地重复确认、本地差分确认、独立交付确认/重跑未通过/待重跑、修复版对照（通过/未通过/受阻/未提供），以及官方判分。官方判分只认有来源回执的 verified 报告：全部案例齐了才记为已判分（最终）并给出 `official_rate`；只要还有一例未核验就记为 `待判分`，`official_rate` 为 null，不写成 0，也不把本地比例当作官方成绩。`official_total_rate` 仍保留“暂定下界”的旧口径，供需要分母的读者对照。没有这些字段的旧轮次照旧读取：来源读作 not_recorded，修复版读作 not_provided。

实施记录和最新完整测试见 [实现记录](implementation-status.md)，具体接口见 [设计](superpowers/specs/2026-10-06-reproagent-swt-bench-design.md)。
