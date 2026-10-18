# 上下文 A/B 暴露问题的产品修复

日期：2026-10-18。基线：`codex/task-state-context` / `ccd181d9476360ac378b2745f3fcb326245e7483`。
本次是既有启动、判定与状态投影的有界修复，不改变实验主指标、模型预算或完成条件。

## 问题与已实现行为

| 问题 | 改动 | 通过条件 |
|---|---|---|
| A/B 的 41 次 Grep 全因 rg 不可用失败 | 生产支持 REPROAGENT_RG_PATH；首次分析前在注册原版快照实际执行一次搜索，缓存成功 | 缺失/不可执行工具在合同模型请求前返回可定位原因；不消耗探索步骤；仍由现有 backend 控制超时、取消和清理 |
| 候选构造时的无关弃用警告被升级为任务级环境阻塞 | 在原版证据完整且绑定有效、target_calls 明确为空、全部失败是 call 阶段的 DeprecationWarning/FutureWarning、栈含候选而不含观察到的目标模块时，程序拒绝候选并允许修订 | 诊断为 CANDIDATE_PRE_TARGET_WARNING / INVALID_CANDIDATE / revise_candidate；沿已有 Controller 继续；不增加重试额度或模型修正请求 |
| STATE 重复 expected，却遗漏与之相关的非强制 caveat | 同时投影 assumptions / observable_checks，保留原有完成决定与预算字段；显式标注所有摘录 | 信封仍不超过4096 UTF-8字节；列表计入JSON转义；ASCII期望在存储阶段被截断也有来源标记 |
| 示例格式被当成固定输出协议 | 分析、探索、审阅提示词要求只把来源支持的行为义务写入 expected/checks，把非强制呈现放在 assumptions | 显式来源要求不被弱化，示例不因在状态里重复而自动成为验收标准；这部分真实模型遵从性仍待拆分实验 |

## 修改范围

- `src/reproagent/adapters/agentscope/snapshot_backend.py`：路径发现与真实预检。
- `src/reproagent/adapters/agentscope/explorer.py`：在分析和首次直接探索入口调用缓存预检。
- `src/reproagent/core/verifier.py`、`core/verification_issues.py`：窄范围、可追踪的程序性候选诊断。
- `src/reproagent/task_progress.py`：契约 caveat 与有界、显式的摘录。
- `src/reproagent/prompts/{analyze_issue,explore,review_evidence}.md`：一致的语义要求。
- 现有缺少rg测试同时清除PATH发现与REPROAGENT_RG_PATH两条来源；两个新回归文件：`tests/integration/test_exploration_startup_repairs.py`、`tests/unit/test_context_repair_boundaries.py`。
- `docs/agentscope.md`、`docs/observability.md`：使用与诊断说明。

未修改 Controller 编排、原始源码保护、修复版隔离、验收成功标准、硬预算、费用上限、经验匹配、默认 STATE/卸载启用策略。没有改变旧24次运行或其分数，没有付费运行、官方判分、push或合并。

## 必须保留的边界

1. 警告/弃用是 Issue 的目标或 observable_checks 的义务时，不走快捷候选归因，仍交由原流程审阅；中英文与 deprecation 词形有回归。
2. 只要有目标活动、观察到的目标模块出现在异常栈、混合异常、setup/teardown失败、缺失归因或不完整执行，均不使用新快捷规则。
3. 新规则是窄范围事实归因，不是通用“环境错误自动重试”。缺依赖等原有环境阻塞处理保留。
4. Python模块导入可能被探针计入 target_calls；这种归因不确定的情况保守回到旧流程，不冒充“没有目标活动”。
5. 修订仍消耗现有步骤与时间；没有关闭warnings、改原版源码或向探索模型提供fixed/gold。
6. 预检确认可执行搜索，不是测试目标语义；退出0/1分别允许匹配/无匹配。READY不会产生源码引用，也不占模型探索动作。

## 验证证据

定向回归先于产品修改运行：初始缺失能力与字段导致6失败/2通过；其中一个警告夹具把目标导入混同为目标执行，修正夹具预加载阶段后，真正预期的旧行为仍失败（旧裁判直接接受候选）。随后八项最小检查通过。

独立审查使用Sol，无Astra、无子级审查或并行pytest，发现两处P2：英文expected在存储时已被静默截断、警告义务guard遗漏observable_checks/deprecation。二者分别新增失败回归后修复；另发现JSON转义令假设字段达到889B而非512B，也有RED/GREEN。

最终定向集：**23 passed**，23.60 s，exit0，含真实SDK + MockTransport + Controller + pytest执行。证明首候选的程序性拒绝进入下一实际HTTP请求；新候选能重新提交并通过重复原版确认；总探索步数2，失败那轮完成等级NONE，未被误判成功。

对既有Flask B rep3执行记录做只读重验：新Verifier输出INVALID_CANDIDATE / CANDIDATE_PRE_TARGET_WARNING，模型调用0。此结果只证明冻结证据被重新归因，不是新Agent运行，也不意味着那个候选已经差分成功。

本地工件：`E:/ReproAgent/repro-results/context-product-repairs-2026-10-18/`：

- `focused-final.log`：最终23项定向回归。
- `unit.log`：首次完整单元737 passed /4 skipped（随后审查边界修复后另跑最终版）。
- `unit-final.log`：最终完整单元回归。
- `integration-final.log`：集成首次132通过/1失败，保留原日志。
- `integration-retry.log`：修正旧测试夹具后，完整10文件集成组重跑。
- `frozen-flask-warning-recheck.json`：冻结证据的新归因。

最终回归（均exit0）：

| 范围 | 结果 | 耗时 |
|---|---|---|
| 完整 tests/unit | **741 passed / 4 skipped** | 365.94 s |
| 受影响10个集成文件完整重跑 | **133 passed / 0 skipped** | 107.36 s |
| 定向新边界与原状态/反馈检查（包含在上述结果中，不重复累加） | **23 passed** | 23.60 s |

这不是整个仓库集成套件全量重跑；未重跑的集成文件不计入通过数。原版保护、文件工具、运行时、探索器、观测和恢复链的关联集成模块已覆盖。

集成首次为132 passed /1 failed，原因是旧 `test_without_ripgrep_the_sdk_search_blocks_instead_of_falling_back` 只将shutil.which模拟为None，未清除现已生效的REPROAGENT_RG_PATH；此时真实rg执行成功符合新配置语义。更新夹具同时清除env与PATH发现，再完整重跑10文件组为133通过。未改产品逻辑来迎合测试，也未隐去首次失败。

命令：`.venv/Scripts/python.exe -u -m pytest -q <范围> -p no:cacheprovider --basetemp <仓库外路径>`；实际设置 `REPROAGENT_RG_PATH=E:/ReproAgent/.local/bin/rg.exe`。unit与集成串行执行；basetemp包含verification-2026-10-18并位于E:/ReproAgent之外。

冻结记录复核使用 `recheck_frozen.py`，确认导入目标worktree源码，926个原A/B文件SHA-256未变化。`git diff --check`通过。未提交、push或合并。

## 限制与后续实验

- 警告归因当前只覆盖所列严格条件；未解决所有候选初始化/环境问题。
- 提示词与STATE字段一致性可以离线验收，但语义模型前后判断是否变稳定、Agent是否更会修候选，不能用脚本回答。
- 没有新真实DeepSeek案例或A/B。成功率与Token收益均未宣称提升。
- 后续正式A/C/D拆分应两组均保留这些基础修复，并在实际产品进程验证rg可用；不能删掉之前环境受限的运行来重算一个更好看的结果。
