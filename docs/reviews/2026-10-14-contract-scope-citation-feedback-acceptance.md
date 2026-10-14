# 契约范围与批量引用反馈：验收记录

日期：2026-10-14。基线：`1d14e7603230c8abaf8ae0b5c5a61a509ad63ec7`。实现分支：`codex/contract-citation-feedback`。状态：产品实现、审查修复和已知回归问题验证完成；完整全量与其后仅测试断言修正的结果分别列示。

## 实际实现

- 契约分析、探索、语义审阅三个提示词统一区分有依据的复现目标、可选未采用提案与真正阻塞项。沿用原字段；程序不自动清空 missing_information，assumptions 不成为正确行为证据。
- 提案不能成为已确认接口；有来源的独立问题不必等可选新 API/整句文案定稿。不能把 8906 的错误指引缺陷替换为已有模块级 skip 功能正常。
- revise_contract 一次汇总 SOURCE_HASH_MISMATCH / SOURCE_RANGE_NOT_DISPLAYED；首错误码留在顶层和 metadata，每项详情与可用引用留在 errors。失败不结束 phase、不部分提出修订、不调用分析模型。
- 建议仅来自当前已签发且有效的引用，转换为快照相对路径；每项最多三条。精确范围、当前哈希、快照边界和截断行规则均不放宽。
- 正文按原工具 UTF-8 字节上限，先减少建议再省略错误，显式标记；正常容量内保持完整 JSON，极小容量用预算内短文。没有引入新的工具、模型请求或预算。
- 审查发现的重复校验已改为每次工具调用、每个文件复用已验证的建议集合。每条提交仍由 canonical_ref 重新读原版字节并验证；缓存不能跨调用，文件中途变化仍拒绝。
- SDK 实际下一请求能收到完整拒绝和对应 tool_call_id；既有软收敛、尾部拒读和首次经验浏览门禁保留。

不改 Controller、Verifier Python 程序验收、IssueContract schema、EvidenceLedger.contains、来源授权、经验检索、trace 模板或主工作区虚拟环境。仅额外更新两处已在基线复现的旧测试消息选择断言，见下文。

## 红绿与回归证据

| 检查 | 实际结果 |
| --- | --- |
| 改动前 main 的协议/领域工具基线 | 43 passed，54.92s，exit 0 |
| 隔离工作区两项现有基线 | 2 passed，4.10s，exit 0 |
| 契约规则 RED | 2 failed、28 passed，31.89s；实际请求缺新规则、提示词尚未统一 |
| 契约规则 GREEN：protocol/phase/domain tools | 51 passed，45.78s，exit 0 |
| 批量引用 RED | 5 failed、1 passed、16 deselected，6.81s；旧响应只有首条错误字符串 |
| 批量引用 GREEN：renderer/domain/rejection/experience triggers | 60 passed，39.54s，exit 0 |
| 请求链 RED | 1 failed、3 passed、31 deselected，8.47s；缺批量修复指引，已实现反馈送达测试通过 |
| 请求链 GREEN：runtime/progress/first browse | 50 passed，37.82s，exit 0 |
| 基线三个失败独立核对 | main：1 failed、2 passed，10.62s；外部 Issue 旧断言失败，启动器通过 |
| 修正 worktree 环境后的两项启动器回归 | 2 passed，3.10s，exit 0 |
| 审查修复 RED + 基线旧断言 | 2 failed，9.84s；20 错误/10 已签发范围实际 220 次读文件，旧断言选中系统进度消息 |
| 审查修复 GREEN：domain/renderer/backends/launcher/runtime | 85 passed，147.92s，exit 0 |
| 完整全量（审查后的最终产品代码） | 1014 passed、6 skipped、1 处基线旧断言失败，1106.11s，exit 1；后续只修改该测试的消息选择 |
| 动作纠错旧断言修正后的整份测试 | 10 passed，7.24s，exit 0 |
| 缓存复杂度和调用中源码变化 | 2 passed，4.70s，exit 0；前者界定最多 30 次读文件，后者验证下一提交重新读并拒绝变化 |

定向日志位于实施工作区 `.superpowers/sdd/2026-10-14-contract-scope-citation-feedback/`，实施结束后备份到 `E:/ReproAgent/.local/contract-citation-audit-20261014/implementation/`。每条测试命令使用工作区 `.venv/Scripts/python.exe` 和仓库外 `C:/Users/Dell/AppData/Local/Temp/reproagent-*` basetemp；准确命令与结果写在对应日志/执行账本。

首轮全量在 21% 时中止，观察到上述三个失败，未计为通过。中止原因是独立审查已确认需修复重复校验，同时已定位旧测试和隔离环境问题；没有把中止结果并入后续完整结果。

完整全量命令：

```powershell
.venv/Scripts/python.exe -u -m pytest -q --basetemp C:/Users/Dell/AppData/Local/Temp/reproagent-contract-citation-final-20261014
```

该完整运行唯一失败为 `test_a_rejected_response_is_corrected_and_each_attempt_is_counted`，在未改 main 上也失败（5.54s），原因是把最后一条收敛系统消息当作纠错用户消息。只修改测试选择：按 user 角色和拒绝前缀定位真实纠错消息，预算、事件码、完整工具集合和拒绝无副作用断言保留；该文件全部 10 项通过。其后没有产品代码变化。未重复这套 18 分钟的全量运行，不把两条命令合成一次“1015 passed”的全量结果。当前无已知未解决失败。

## 8906：真实工具离线重放

读取 `E:/ReproAgent/repro-results/batch-pre-pytest-8906` 原 trace 与冻结清单，逐个验证涉及文件的内容哈希。在全新输出中冻结这些原版文件的子集，保留完整原 Issue；通过真实 Toolkit/Read 重建原记录已经展示的范围，未直接向 ledger 填入引用。

- 11 个原版文件；29 个原记录展示范围；原记录有四次修订尝试。
- 原第一次修订提交五个引用，本次一次返回五项 SOURCE_RANGE_NOT_DISPLAYED，omitted_errors=0。
- 实际反馈 3450 UTF-8 字节，低于默认 32768 B。
- 建议首选范围依次为 python.py 590–629、outcomes.py 110–169、outcomes.py 40–89、test_skipping.py 1320–1379、skipping.rst 55–94。
- 把已展示完整范围复制回新的请求后，返回 revision_requested，phase 正常结束。程序没有自动替换原请求。

输出：`E:/ReproAgent/.local/contract-citation-audit-20261014/historical-replay-final/result.json`；审查修复后再次重放通过；脚本 `replay_8906_final.py` 和日志保留在同一 audit 区域。

这只证明引用恢复机制。没有真实模型契约分析、候选执行、语义验收、独立确认或修复版对照；不宣称 8906 DONE。静态规则和指定答案的 MockTransport 测试，也不能证明真实模型会正确选择复现范围。

## 独立审查与处理

首选 gpt-6.1-sol 审查因模型满载未执行；一次替换为 gpt-6-sol，完成只读审查。未使用 Astra，未委派实现任务，ReproAgent 未访问真实模型接口（独立审查代理自身使用模型）。

审查确认没有发现引用授权扩张或请求链送达缺陷，提出一项 Important：每条失败反复扫描所有同文件引用并读取整文件，40 错误/40 范围可产生 1600 次校验。已在一轮修复中加入单次调用的建议校验复用；实际回归先复现 220 次读取，再验证最多 30 次，补跨调用和调用中来源变化拒绝。没有以跨任务/跨调用缓存替代原版实时检查。

审查未评判真实模型是否选择正确的 8906 目标、是否生成有效候选或最终 DONE。此边界按设计保留，真实效果需要后续单独授权的模型评测。没有 Minor 延期项。

## 实施裁决与环境边界

1. Windows 没有 Git Bash，技能脚本用等效 PowerShell/Python brief、BASE、账本和日志管理替代；风险是账本与脚本语义漂移，因此保留任务身份和实际结果。
2. 隔离 `.venv` 只复用 main 已安装依赖，初始化共享 `.pth` 的 Windows 依赖后，将共享 editable 的 main src 路径替换为 worktree src，保留显式 PYTHONPATH。早期强制前置源码导致两项 fake CLI 测试失败；单纯追加曾加载 main，均已纠正，最终验证实际导入 worktree。主环境未改动。单纯追加导致一次导入检查选中 main，最终路径替换已修正后再运行回归，不能把那次导入检查视为产品验收。
3. main 既有外部 Issue 测试把最后消息当成用户数据，实际为已交付的收敛系统消息；仅改测试按 user 角色选择输入，保留原 Issue/hash/密钥脱敏断言。风险是选错用户消息；该测试核对首个探索请求，只有一个用户输入。
4. 增加私有 verified_refs 参数以复用单次调用、同文件的有效建议，不更改外部工具/schema。风险是缓存过期，因此每条提交仍重新核验原文件；专门验证调用间和调用中变化。

5. 另一处基线旧动作纠错测试也依赖最后消息位置，仅按 user 角色和拒绝前缀定位原纠错消息；全部 10 项通过。没有因此改 runtime/middleware 或协议。只改测试选择之后不重复全量，风险是未再取得当前测试文件版本的一次全量全绿证据；已保留完整实际结果和专项检查，不能隐去这个边界。

## 提交与集成状态

- 8f198d6：设计和实现计划。
- d619a81：契约范围分离规则与测试。
- fd500b4：批量引用反馈与预算/授权回归。
- df4d348：实际 wire、收敛与经验门禁兼容测试。
- 02a5e98：审查开销修复与基线旧断言对齐。

当前保留独立功能分支和 managed worktree；未合并、未 push。用户本地未跟踪的评测 HTML 未改动。
