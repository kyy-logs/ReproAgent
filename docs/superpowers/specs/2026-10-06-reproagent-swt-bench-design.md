# ReproAgent：SWT-Bench Lite 评测接入设计

日期：2026-10-06。状态：用户确认后已实现数据、绑定、预检、批量运行、预测导出和外部报告链路；实际官方 Docker 判分待可用环境。开发诊断与限制见 [使用说明](../../swt-bench.md)。

## 1. 用户目标与成功标准

用户同意采用 SWT-Bench Lite 评测 ReproAgent：先固定 20 个开发样本打通流程，再评测未参与调试的样本。评测对象是从原始 Issue 生成有效复现测试的能力，包括运行效率、稳定性和误报。原生与 AgentScope 可在相同模型、输入和预算下对照。

本设计的假设：第一版继续使用现有 Python/pytest + local 执行接口，以提前准备的项目环境生成测试；输出标准预测文件，由固定版本官方 SWT-Bench harness 独立判分。开发子集、官方判分、人工正确性审查分别记录。

交付标准：固定来源的数据导入、可重现的开发样本清单、环境绑定与预检记录、串行批量运行、冻结候选的标准测试补丁、官方报告导入、汇总与逐例失败记录。完整 Lite 的环境覆盖和成绩不属于这 20 例开发接入的完成标准；20 例也不当作正式保留集。

## 2. 已核对的现状

### 仓库

- `evals/schema.py` 已有 EvalCase/EvalResult；`evals/run.py` 已有 generation_input、run_case 和 summarize。
- generation_input 不暴露修复版；run_case 目前使用默认预算与默认后端，缺少 candidate_parent 等项目绑定参数。
- 现有统计把 DONE、独立重跑和人工判断分开，但没有外部 SWT 判分状态或数据集版本。
- 当前环境状态通过 environments 文件是否存在推断，不能直接作为新评测的准备成功证明。
- 产品仍是 Python/pytest + local，AgentScope 后端已可独立选择。本次不重写 Controller、Runner 或 Verifier。
- 历史 20 例已经用于调试，继续作为回归资料，不加入新的保留测试集。

### 官方来源

- 数据：`princeton-nlp/SWE-bench_Lite`，test split，调研时 revision 为 `6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2`。
- 官方 harness：`logic-star-ai/swt-bench`，调研时 master commit 为 `330a649a764fab2fadaea632776eeae87272f74b`。
- 原始 test split 为 300 条；官方 `dataset/filter_cases_lite.txt` 是 24 条排除项，排除后为 276 条。
- 排除文件调研时 SHA-256：`d88e9ecfc5ab4f68cb961cea90bf54ddcd7d8af58b77c15e7def8a5b2fd00f2b`。实际导入须从固定 harness commit 读取并校验，而不是继续使用浮动 master。
- 原始行含 problem_statement、base_commit、patch、test_patch、环境提交和测试清单。正式生成输入只取原始问题和问题版项目；不使用包含修复提示的 hints_text 或预拼接提示。
- 调研计数通过公开 dataset rows API 得到；正式导入必须读取固定 revision 的快照，再核对 ID、字段与数量。

参考：[官方说明](https://github.com/logic-star-ai/swt-bench)、[过滤逻辑](https://github.com/logic-star-ai/swt-bench/blob/330a649a764fab2fadaea632776eeae87272f74b/src/dataset.py)、[判分逻辑](https://github.com/logic-star-ai/swt-bench/blob/330a649a764fab2fadaea632776eeae87272f74b/src/grading.py)、[数据入口](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite)。

### 本机运行条件

只读检查未找到 PATH 中的 docker，也未找到 Docker Desktop 常见安装位置；WSL 查询退出失败并提示安装，尚未证明有可用 Linux 环境。E 盘空闲约 90.6 GiB。官方快速并行评测建议约 120 GB 空间；子集串行评测的实际需求需另行确认，不能据此断言一定无法运行。

本次不会自动安装 Docker Desktop、启用 Windows 系统功能或重启机器。数据导入、补丁导出和汇总可在当前 Windows 环境实施；官方 Docker 判分需要已有可用 Linux/Docker 环境。没有官方结果时字段为 not_run，不把本地 DONE 写成官方 resolved。

## 3. 方案比较

| 方案 | 收益 | 成本与限制 |
| --- | --- | --- |
| A：扩展现有 evals，生成标准预测，由官方 harness 判分 | 复用当前 Agent；输入与判分隔离；可先交付离线数据链路；推荐 | 需要单独准备项目环境；正式外部判分依赖 Linux/Docker |
| B：把整个 ReproAgent 运行循环搬进官方容器 | 生成环境接近判分环境 | 官方旧 Python 与工具 Python >=3.11 的兼容、双解释器和容器清理需要新的运行时设计 |
| C：只抽样并用现有 Verifier 自行判分 | 最快获得本地诊断 | 没有独立 SWT 判分依据，无法充分建立可比成绩 |

选择 A。官方环境支持通过评测侧环境准备解决；本版不增加生产 Docker runtime，不替换 pytest runner，不实现全部项目的测试运行器。

## 4. 数据和组件边界

```text
固定数据 revision + 官方排除规则
                 ↓
Dataset importer → catalog + dev20 manifest
                 ↓
Prepared bindings → 版本/环境/目录预检 → 每例状态记录
                 ↓
原始 Issue + 问题版项目 → 现有 ReproAgent
                 ↓
冻结候选与导出包 → Prediction exporter → predictions.jsonl
                 ↓
固定官方 harness，在独立环境运行
                 ↓
Official report importer → 逐例结果 + summary.json + report.md
```

### Dataset importer：`evals/datasets/swt_bench.py`

接收显式 JSON/JSONL 快照和固定来源记录；网络获取作为单独命令，默认离线导入不联网。不执行数据集附带的任意脚本，不把数据集依赖加入产品主安装。

校验必需字段、唯一 ID、repo 与 ID 的对应关系、base_commit 格式、原始问题非空，以及官方排除规则。拒绝重复 JSON 字段、无效行和数量不符的固定快照。按原始 UTF-8 问题文本计算哈希，不重写为研究者摘要，不自动补充行为预期。

catalog 保留评测元数据；修复补丁和官方测试存在评测侧独立缓存，不能进入问题版仓库、问题文件、模型请求或业务工具返回。只允许显式白名单字段进入 generation_input。数据缓存、固定版、官方测试控制副本和输出目录不放在 Agent 的项目视图内。

### Prepared bindings：`evals/swt_bench/schema.py` 与 `prepare.py`

数据记录没有可直接运行的本机路径，需要绑定 buggy_repo、fixed_repo、两个目标解释器、target_modules、source_roots、candidate_parent、允许的 pytest 参数及基线测试。

问题版严格对应 base_commit；修复版从同一提交应用数据集 patch，并记录补丁哈希和最终源码清单哈希，不假称必有 fixed_commit。test_patch 只在独立预检控制副本使用，不加入生成或固定验证项目。实际补丁应用与克隆只操作本次评测明确拥有的目录，不重置或修改已有用户项目。

目标模块与生成测试目录由问题版源码、原始问题和已有测试结构确定，不能从隐藏补丁或官方新增测试反推。准备工具不把 dataset.version 当作安装命令，不执行未经审核的数据字符串。
依赖和解释器按核对的版本说明在本次评测拥有的可丢弃目录中准备，不修改已有用户项目环境。记录与官方环境的差异；旧 Python、pytest 插件或项目测试运行器不兼容时为 unsupported/blocked，不静默换成另一套环境来伪称官方复现。

预检状态使用 pending / ready / blocked / unsupported / preparation_error，附解释器和依赖版本、项目哈希、日志与原因。环境准备和模型执行分别计时。先完成所有开发样本准备记录，再冻结本轮清单；失败样本不替换成别的 ID。

机器预检、技术资料审查和最终人工测试正确性审查是不同字段。数据导入默认 pending；通过技术核查后记录 review_status=approved 与审查来源，符合现有 run_case 条件。不能伪造最终 human_judgement=True。

### Batch runner：扩展 `evals/run.py`，新增 `evals/swt_bench/run.py`

run_case 增加可选 keyword 参数用于预算和两个后端选择，默认保持旧调用有效。项目绑定传入现有 PythonPytestConfig；既有历史记录不改写。

批量默认串行，使用冻结的样本/配置哈希，每例新 output_dir。首轮采用 native/native；策略对照采用同一模型后端的 native/agentscope；model 后端兼容性另列，默认不自动跑全部四组合。

每例默认最多 20 动作、单命令 60 秒、任务 900 秒，沿用现有清理/收尾限制；模型输出上限取当前配置。执行前冻结轮次配置。模型协议内部纠正沿用现有有限规则；一次任务失败后不增加预算或重新运行来覆盖结果。后续诊断重跑必须写新轮次，原结果保留。

准备失败、不支持、Agent 异常、取消、缺输出都写入总清单，不能只保存 DONE。模型请求使用现有密钥环境变量/启动器，不保存密钥。请求次数、usage、已知费用小计和未知费用分别统计，从 model.attempt 计数，避免重试用量遗漏或与 model.completed 双算。

### Prediction exporter：`evals/swt_bench/predictions.py`

读取已冻结候选及其 manifest，验证绑定 ID、原版版本、候选路径和哈希。仅导出候选文件构成的标准 unified diff，保留项目内原安装路径和末尾换行信息。拒绝业务源码改动、路径穿越、符号链接、覆盖已有文件或数据/运行 ID 混配；若候选不能转换，记录 export_error。

每个已选 ID 都有一条预测行：instance_id、model_name_or_path、model_patch。失败或无有效候选写空 model_patch，并在旁侧完整 ledger 保存原因。full_output 默认不输出，避免传播提示和凭据。支持文件和 fixture 只按受保护候选清单导出，不复制运行环境或修改安装文件。

预测文件与 ledger 原子写入，并绑定 dataset revision、harness commit、样本清单、轮次配置和内容哈希。补丁应用成功不等于复现成功。

### Official reports / aggregation：`evals/swt_bench/results.py`

先输出可审查的官方调用命令和预测文件。命令在具备环境时由外部 harness 执行；若未执行则保留 not_run。生成测试必须经过官方 unit_test 模式；reproduction_script 模式若后续使用应另标，不混合成绩。

导入官方逐例 report.json 时核对样本 ID、run_id、model_name_or_path、运行来源回执、预测哈希和 harness commit。未知或重复 ID、字段类型不符、不同轮次结果都拒绝。保留原始报告和哈希。由用户导入而无完整运行来源的文件标 imported_unverified，不宣称已独立执行。

official_resolved 来自官方 resolved 字段；本地 differential_validated、export_replayed 和 human_judgement 另存。官方规则还考虑既有测试行为，不能用本地 F/F/P 或 pytest 退出码代替。尚无官方报告为 not_run/missing；构建或判分运行失败单列 infra_error。

## 5. 开发样本选取

第一批为五个偏向 Python 测试工作流的仓库中的 20 个开发样本，目的在于接入调试。此选择是项目偏置的子集，不代表 Lite 全集，也不保证其旧环境可以在当前 Python 运行。

选择规则：从官方 276 条中取 repo 属于下表的记录；各 repo 内按 SHA-256(`reproagent-swt-dev-v1:` + instance_id) 排序，repo 按字典序轮询取样，到 20 条停止。选择只使用 ID/repo，不看 Agent 成功与否，不按模型结果筛选。数据固定 revision 导入后验证下列结果：

| 仓库 | 开发样本 ID |
| --- | --- |
| pallets/flask | pallets__flask-4992、pallets__flask-5063 |
| psf/requests | psf__requests-3362 |
| pytest-dev/pytest | pytest-dev__pytest-7373、pytest-dev__pytest-5413、pytest-dev__pytest-5692、pytest-dev__pytest-7168、pytest-dev__pytest-5495、pytest-dev__pytest-7432 |
| sphinx-doc/sphinx | sphinx-doc__sphinx-8801、sphinx-doc__sphinx-8721、sphinx-doc__sphinx-8435、sphinx-doc__sphinx-10325、sphinx-doc__sphinx-8506、sphinx-doc__sphinx-8595 |
| sympy/sympy | sympy__sympy-20154、sympy__sympy-15308、sympy__sympy-19254、sympy__sympy-21055、sympy__sympy-18698 |

manifest 保存顺序、ID、repo、base_commit、原始问题哈希、数据 revision、过滤文件哈希、选择算法和 seed。20 个 ID 在第一次模型运行前锁定；之后准备失败也不替换。

正式保留集在开发接入完成后另行固定，排除本次 dev20 和之前调试过的等价案例。不能将剩余 256 条自动称为全部未见过；还需检查本项目历史重叠与人为查看/调试记录。

## 6. 指标与分母

| 指标 | 定义 |
| --- | --- |
| 环境准备率 | ready / 全部冻结的选定样本 |
| 本地重复确认率 | 本地达到 REPEATED_OBSERVATION 或更高 / 全部选定样本 |
| 本地差分确认率 | DIFFERENTIAL_VALIDATED / 全部选定样本 |
| 官方判分完成率 | 有有效外部判分的样本 / 全部选定样本 |
| SWT 子集成功率 | 官方 resolved=true / 全部选定样本；必须同时展示未判分数量，存在 pending 时标为暂定而非最终成绩 |
| 可运行环境成功率 | 官方成功且 ready / ready；辅助指标，不替代总体分母 |
| 导出独立重跑率 | 生成成功包在新原版/修复版符合预期 / 已发布成功包 |
| 人工确认率、误报率 | 明确展示已审查样本数与待审查数；未审查不当作人工通过或人工否定 |
| 效率 | 准备/Agent/外部判分耗时分别统计，HTTP 尝试、token、已知费用与未知费用 |

P50/P90 只对有实际完成耗时的样本计算，超时样本和时间上限另列，不把中断时间当完整任务耗时。稳定性需要额外轮次，不把一次开发执行包装成多次重复基准。

覆盖率变化采用官方报告已有字段并标注版本；未运行覆盖率为 null。仅靠覆盖率不能证明测试与 Issue 对应。人类有效复现要求预期正确、真实调用目标、失败对应问题、固定测试修复后通过，并且独立重跑成功。
人工确认率的分母是已审查样本；误报率的分母是已审查的本地声称成功样本，分子是其中被明确否定者。任一分母为零时为 null，并同时公布待审查数量。

四种后端分别汇总，不合并各组最好结果。公开数据可能被模型训练见过，保留集只是未用于本项目调试，不声称模型未见。

## 7. 第一版范围与执行阶段

1. 数据导入、完整 Lite catalog、dev20 manifest、生成输入白名单与离线样本验证。
2. 环境绑定/预检回执与串行 runner；在可准备的本地项目上先跑一个真实样本，再继续同一冻结清单的其他样本，其余无法运行的样本记录明确原因。
3. 预测补丁导出、独立新副本重跑、官方命令与报告导入、Markdown/JSON 汇总。
4. 已有 Linux/Docker 环境可用时运行官方判分并保存来源回执；环境不可用时保留这一阶段未执行，明确列出实际交付的链路与剩余工作。

真实首例属于同一轮次，失败结果保留。第一版不安装系统级容器运行时，不修复 Agent 目标业务代码，不添加 Django 专用 runner，不自动全量跑 276 条，也不提交排行榜或向他人发送结果。

## 8. 验证与交付

离线测试覆盖：排除集合方向、固定 revision/哈希、重复 ID/字段、问题原文保持、禁止材料隔离、确定性采样、绑定目录和版本校验、blocked 分母、未知费用、后端/预算传入、失败预测占位、补丁应用与换行、官方报告来源绑定和 missing/pending 语义。

真实集成测试使用小型 Git fixture：问题版与修复版分离，生成候选成为标准 patch，应用到两个新副本实际 pytest，原版失败、修复版通过；无模型服务即可验证数据链路。外部报告结构以固定 harness 的真实样例核对，离线合成报告只能证明解析逻辑，不能声称完成官方判分。

产品已有完整回归继续运行，保持默认 CLI 与旧 run_case 调用有效。独立 wheel 安装仍不附带 datasets/Docker。数据获取/官方 harness 依赖仅在独立评测环境中安装。

交付目录：评测源码在 evals/datasets 与 evals/swt_bench；说明在 evals/README.md 和 docs/swt-bench.md；原始数据、环境、预测、逐例回执及报告在忽略的 .local/swt-bench 和 repro-results/swt-bench 下。历史案例与输出包不改写。

第一版完成后的表述必须区分：代码链路通过、实际执行样本数、本地证据、官方判分数与人工审查数；不能只用一个“成功率”遮蔽这些边界。
