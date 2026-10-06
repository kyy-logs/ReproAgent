# SWT-Bench Lite Implementation Plan

> **For agentic workers:** Use executing-plans inline, task-by-task with RED/GREEN tests and one fresh whole-change review.

**Goal:** 接通固定 SWT-Bench Lite 开发子集的数据、环境、运行、预测和独立判分记录。
**Architecture:** 扩展现有 evals；生成端只见原始问题与问题版，评测侧保存修复和官方材料。冻结清单贯穿运行与预测；本地和外部结果分别汇总。
**Tech Stack:** Python >=3.11、stdlib、现有 pytest/httpx；仅数据获取环境使用 pyarrow。
**Spec:** ../specs/2026-10-06-reproagent-swt-bench-design.md

## Global Constraints

- 固定 dataset revision 6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2、harness commit 330a649a764fab2fadaea632776eeae87272f74b。
- 300 条 test 行减去官方 24 条排除项为 276；seed reproagent-swt-dev-v1，五仓库轮询 20 个开发样本。
- 原始 Issue 不改写；patch/test_patch/hints_text 不进入生成输入。
- 默认串行、native/native、20 动作/60 秒单命令/900 秒任务；失败不替换，不隐式增加预算重跑。
- 官方结果未执行为 not_run，人工判断未完成为 null；失败保持分母，未知费用不是零。
- 目标仍 Python/pytest/local；不安装系统 Docker/WSL，不改旧案例，不发布。

## Review Focus

1. 过滤文件方向和浮动来源：必须核对排除集合及 revision/hash。
2. 路径和隐藏资料混入：绑定隔离目录，候选补丁只增加受限文件。
3. 部分失败或取消：全部 ID 有 ledger/预测，取消后的未启动任务不可执行。
4. 模型重试用量/费用：只从 attempt 计账，未知保持 null。
5. 外部报告混配与伪成功：核对 run/model/hash，缺来源标 imported_unverified。

## Task 1：数据导入与固定选择

Files: evals/datasets/swt_bench.py、evals/datasets/fetch_swt.py、evals/swt_bench/io.py；tests/unit/test_swt_dataset.py。
Interfaces: load_snapshot(path, filter_path, source) -> catalog dict；select_dev(catalog, count=20) -> manifest dict；generation_fields(row) -> white-list dict；fetch_snapshot(output) -> source dict。

- [x] RED：排除方向、重复字段/ID、必需字段、原文和隐藏材料、seed 选择、来源哈希与固定快照数量。
- [x] 实现纯离线转换、固定来源和显式联网获取；获取不执行仓库代码。
- [x] GREEN：pytest tests/unit/test_swt_dataset.py；实际下载固定 revision 并验证 300/276/dev20 与设计一致。

## Task 2：环境绑定与原有 runner 参数

Files: evals/swt_bench/prepare.py、schema.py；evals/schema.py、run.py；tests/unit/test_swt_prepare.py、test_eval_runner.py。
Interfaces: validate_binding(row, binding, protected_roots) -> EvalCase；preflight(case) -> receipt；run_case(case, model, output_dir, *, limits=None, model_backend='native', agent_backend='native') -> EvalResult。

- [x] RED：绑定越界/目录重叠、错误原版 Git HEAD、变更源码、隐藏资料在原版内、解释器不可用、预检失败、候选目录/预算/后端传递。
- [x] 实现只读绑定校验、目标版本/pytest 探测和可持久化 ready/blocked/unsupported/preparation_error；不靠环境文件存在判定 runnable。
- [x] GREEN：pytest tests/unit/test_swt_prepare.py tests/unit/test_eval_runner.py。

## Task 3：标准预测与独立重跑

Files: evals/swt_bench/predictions.py；tests/integration/test_swt_predictions.py。
Interfaces: candidate_patch(task_dir, case, binding) -> str；write_predictions(manifest, outcomes, model_name, output) -> receipt。

- [x] RED：冻结候选和清单校验、原版覆盖拒绝、源码修改/路径越界、末尾无换行、失败空预测、哈希变化。
- [x] 实现统一 diff、每 ID 预测和 ledger、来源哈希；只导出候选文件。
- [x] GREEN：真实 Git apply 新原版和新修复副本，pytest 原版失败/修复通过；pytest tests/integration/test_swt_predictions.py。

## Task 4：串行评测与外部结果汇总

Files: evals/swt_bench/run.py、results.py、__main__.py；tests/unit/test_swt_results.py、tests/integration/test_swt_batch.py。
Interfaces: run_batch(catalog, manifest, bindings, model, output, *, limits, model_backend, agent_backend) -> round dict；import_reports(round_dir, reports_dir, receipt=None) -> results；summarize_round(round_data) -> dict。

- [x] RED：失败保持20分母、未知费用、取消不续跑、数据/绑定冻结、防覆盖、官方类型/身份/来源混配、缺结果和人工待审查。
- [x] 实现所有选定ID的增量ledger、完整预测、JSON/Markdown汇总、可审查官方命令；report导入不声称已执行无来源输入。
- [x] GREEN：pytest tests/unit/test_swt_results.py tests/integration/test_swt_batch.py；离线完整 CLI 链路及 help。

## Task 5：开发样本实跑、文档与独立审查

Files: docs/swt-bench.md、evals/README.md、README.md、docs/implementation-status.md；忽略目录中的来源/绑定/实跑记录。

- [x] 所有20例先记录准备状态；在可运行项目上运行首例并继续同一清单，失败不替换。实际缺环境记录阻塞。
- [x] 导出成功包在新副本独立重跑。保留本地结果与尚未执行的外部判分。
- [x] 运行完整 pytest 与 pip check；一次独立全改动审查并修复实际问题。
- [x] 更新设计状态、使用命令、实施记录和回执；不声称未完成的官方判分或人工成绩。

## 执行记录 / Ledger

- 用户“实现”确认已展示的书面设计，并明确要求执行。沿用本会话原先选择的逐项 inline 方法；用户执行指令优先，不再次要求确认同一接入。
- 当前无 Git 仓库，直接在 E:\ReproAgent 修改，不初始化仓库或创建工作树；技能 Git 专用脚本不适用，进度记录在本文件。
- Pre-flight: Task1 catalog/manifest → Task2 bindings → Task3 predictions → Task4 round/importer，所有接口使用 dataset identity/hash；旧 EvalCase/EvalResult 仅追加默认字段以保留调用。
- Ruling: 本机无 Docker，交付数据/本地评测/外部报告链路，官方执行明确未运行；不以本地判定冒充外部成绩。
- Task 1: complete — 数据/Unicode 针对性9项通过；固定官方快照已实际导入300/276/20，原始GBK失败保留并修复为UTF-8。
- Task 2: complete — 绑定和旧runner14项通过；追加默认配置字段并保持旧入口有效。
- Task 3: complete — 5项真实导出/Git apply/pytest测试通过；既有支持资料角色为data而非support，按真实接口修正并新增RED/GREEN。
- Task 4: 核心批量/结果/CLI和取消链路已接入，针对性21项通过；外部Docker执行未运行，剩余真实CLI和正向receipt测试。
- Ruling: 无Git仓库不执行Git专用技能脚本；评测fixture和公开数据checkout可以在明确拥有的新目录创建独立Git仓库，产品根不初始化。
- Ruling: Git补丁文件名先只支持常规ASCII路径，拒绝无法正确编码的名字；测试内容支持UTF-8、无末尾换行与空文件，保留字节而非替换路径。
- Ruling: 已审查源码变化在Controller准备阶段被拒绝，原流程将准备异常归为BLOCKED；测试从错误的FAILED预期改为BLOCKED并保留“零模型调用”断言，未修改产品状态规则。
- Task 4: complete — 命令行实际导入300/276/20；独立预检无需模型凭据；来源完整与部分手动导入的状态分别处理。
- Task 5: complete — 最终完整244 passed、1 skipped，164.37秒，pip check无冲突；独立全改动审查与最终窄修复复查均关闭发现。官方Docker执行明确未进行。
- 审查修复经RED/GREEN：忽略文件进入快照、执行harness只校验HEAD、预测与manifest未绑定、旧轮次日志混入、官方失败未记录infra_error；补充预检后源码变化和输出目录保护。手动部分导入保留旧已验证记录，成功回执缺报告标missing。
- 官方调用改为固定本地snapshot JSON，按manifest来源哈希验证，避免浮动HF数据重取。只有实际执行的来源回执才能使导入记录为verified；合成回执测试只证明接口。
- 真正准备20例及一次native/native首轮：2 ready、1 REPEATED（0 DIFF）、1 EXHAUSTED、18 NOT_PREPARED；44 HTTP尝试/322531 token/费用unknown。成功包在新原版/修复版均退出1，差分重跑false。没有把本地DONE当正式成功。
- 真实数据暴露Git Unicode文件名误判：采用UTF-8与ls-files -z，经RED/GREEN修复。独立预检后为8 ready、12 blocked，原轮保留且模型未重跑，不宣称修复后性能。
- Ruling: 真实模型诊断轮已按冻结清单完成；新增6个可预检环境的模型重评留作独立下一轮，不追加隐式重试、不将已调试样本当保留成绩。代价是当前没有修复后8例的能力结果，文档与回执明确该限制。
- 文档：docs/swt-bench.md；回执：.local/swt-integration-verification.json；原始首轮：repro-results/swt-bench/dev20-native-001。
