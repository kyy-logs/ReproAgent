# 探索步骤预算软收敛：验收记录

日期：2026-10-14。工作分支：`codex/exploration-convergence`，起点 `766f7f8`。真实效果实验未运行。

## 实现与边界

- 默认 20 步时，第 16 个逻辑请求开始收敛，提示中 used=15、remaining=5；第 20 轮仍可发布，第 21 轮没有 HTTP 请求。
- 每次请求临时生成 budget/facts/guidance JSON，最多 1024 UTF-8 B，不写入持久历史；契约/候选仅从程序阶段视图取得，用户自由文本不提升到系统指令。
- 最终输入计数包含新提示，达到原容量阈值时 NEEDS_INFORMATION；阶段已完成、取消、任务时间或步骤到限仍优先。
- 跨阶段/契约不重置步骤；网络重试共享一份快照，只消耗一个逻辑步骤；协议纠错消耗下一步骤。
- 新增 `budget.progress` trace 点及任务级首次 `budget.convergence`，事件故障与 trace 故障分别隔离，不改变候选发布和权限结果。
- 保留最后 3 步读工具拒绝、源码/候选保护、Verifier 和独立确认；不改 thinking，不新增模型请求、依赖或 CLI 参数。

## 本次验证

命令在工作区 `C:/Users/Dell/.codex/worktrees/exploration-convergence/ReproAgent` 执行，`.venv` 只复用现有依赖，源代码优先指向该工作区。basetemp 均为仓库外的 `C:/Users/Dell/AppData/Local/Temp/` 目录。

| 验证 | 结果 |
| --- | --- |
| 既有 budget/runtime/first_browse 基线 | 28 passed，15.11s |
| Task 1 RED | 新模块不存在，预期的 collection failure |
| Task 1 GREEN | 19 passed，0.21s |
| Task 2 RED | 9 failed / 4 passed；缺提示、最终输入未检查，预期暴露未实现功能 |
| Task 2 GREEN（含停止优先级） | 67 passed，17.09s |
| Task 3 RED | 4 failed / 2 passed；缺少事件及事件写入异常导致任务失败 |
| 集中回归（含现有经验门禁与 AgentScope 观测） | **175 passed，29.12s，exit 0** |

集中回归命令：

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit/test_exploration_progress.py tests/unit/test_budget.py tests/integration/test_agentscope_runtime.py tests/integration/test_agentscope_gateway.py tests/integration/test_exploration_progress_observability.py tests/unit/test_trace_view_model.py tests/unit/test_trace_rendering.py tests/integration/test_agentscope_first_browse.py tests/unit/test_experience_first_browse.py tests/integration/test_agentscope_observability.py --basetemp C:/Users/Dell/AppData/Local/Temp/reproagent-convergence-acceptance
```

离线请求验证使用真实 AgentScope/formatter/guard、MockTransport；没有真实 provider 或模型费用。没有执行无关全量 suite，集中通过不能写成全仓测试全绿。

## 离线 trace 样例

路径：`E:/ReproAgent/repro-results/budget-convergence-offline-20261014/task/observability/trace.html`，旁边保留 `trace.json`。

脚本产生 15 次 Read 和第 16 次 write_candidate：16 个实际序列化请求、16 步、一个发布候选。trace 主状态写为 `OFFLINE_SCRIPTED`，这不是完整 Controller/Runner 的真实复现验收。

自动验证 trace 因果关系、budget 字段与 wire_request 一致，使用产品 write_trace/write_trace_html（含路径保护）生成文件。重试样例的三个 HTTP span 引用一份去重正文，不能按正文副本数统计请求次数。

**浏览器点击验收未完成。** 内置 Browser Use 明确禁止 file: URL，并禁止通过另一浏览器或 localhost 绕过该限制；因此停止这一 UI 操作，未声称浏览器实际展示已经验收。未改 viewer 代码，已有 viewer view-model/rendering 回归通过；页面交互和视觉表现仍需用户本地打开检查。

## 实现中作出的调整

1. 按用户批准的定向验收范围运行；发现新耦合后增加既有 SDK 观测测试，不无理由反复全仓测试。代价：无关交互不在本次覆盖内。
2. 初始受限临时目录导致 23 个 setup errors；改用本机外部 basetemp。工作区复用依赖需 site.addsitedir 加载 pywin32 路径，未修改产品或主环境。
3. 极端配置下若必须字段本身也装不进 1024 B，拒绝发送并返回 NEEDS_INFORMATION，不能截断 JSON/计数。
4. 契约修订夹具从空 source_refs 改为真实 Read 后引用冻结范围；原夹具不满足既有契约，不能用于验证跨阶段进度。
5. 压缩阈值夹具计入新的临时提示，保持“第一轮可发布、发布后容量超限也保留已发布结果”的原验收目标。
6. 扣步边界比较的是 remaining_before - remaining_after = 1，修正计划里 used 与 remaining 的表述笔误。
7. 重试正文按内容去重验证引用关系；UI 安全限制保留为未验证边界，不绕过。

## 独立审查

使用 gpt-6.1-sol 独立只读审查 `766f7f8..39683c3`，未使用 Astra，未调用真实 provider。
结论：Critical / Important / Minor 均为 0，无待修复项；限本次步骤软收敛范围可合并。
审查方另执行 42 passed / 14 deselected / exit 0 的定向验证，工作区保持干净。

审查保留两项不作效果判断的范围：真实模型是否更早发布/减少读取/提高成功率，以及 trace 页面的视觉与点击表现。前者须另行授权实验，后者被浏览器安全策略阻止；均不能由离线通过代替。

独立审查完成时保留功能分支和工作区，未合并、未 push。执行账本与日志另存 `E:/ReproAgent/.local/convergence-audit-20261014/`。

## 本地 main 合并验证

用户随后授权合并。2026-10-14 将 main 从 `766f7f8` 快进到 `a5bf419`，未执行 push。
对 main 使用上面的同一组十个测试文件、独立 basetemp `C:/Users/Dell/AppData/Local/Temp/reproagent-convergence-main-merge-20261014` 再验收：**175 passed，40.34s，exit 0**。

main 中四份同步文档先与分支内容核对一致，再备份到 `.local/merge-backups/convergence-20261014-123706/` 后合并。
用户原有评测 HTML 保留，SHA256 为 `aaa7f31a825f9c89e80bb8249f364d2c2b2be59aceb972d29c76d0b67551c0e2`。
合并测试日志保存在 `.local/convergence-audit-20261014/main-merge-tests.log`。

## 尚未证明的事项

提示进入实际请求体，不等于模型一定聚焦或更快完成。未跑真实 A/B，未比较有效候选率或 DONE 提升；时间、费用、风险软收敛尚未实现。
