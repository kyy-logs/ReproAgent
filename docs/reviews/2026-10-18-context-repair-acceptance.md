# Context / Grep 验证后修复验收

日期：2026-10-18。基线：3a5848691158d736a82924812c9c43574a5a544f。分支 codex/task-state-context。

## 修复

1. Grep 原有非贪婪路径正则会误分割 verification-2026-10-18、part-2026-10.py，并把正文中的数字冒号片段误当路径。现按已登记文件的规范路径别名与长度预筛选，读取独立行号/正文；绝对、相对、Windows 路径形式均受原注册校验，歧义拒发引用。完整正文、500/2000 字节宽度与最后的新鲜哈希复核不变。
2. 成功保存的大 Read/Grep/Glob 返回至多 min(tool_response_bytes,4096) UTF-8 字节，包含前置且独立成行的入口、原文摘录、sidecar 和截断标记。保存失败沿用旧截断上限并显示原因；材料额度不变。不调用摘要模型，不强制读材料。
3. 在实际 HTTP hook 中独立捕获唯一的 user task_state 原始消息文本；source=wire_task_state，绑定对应 HTTP span，先于整包 wire_request 捕获。整包正文单条 32 KiB 和全任务容量不变；新记录也遵守脱敏和总量限制，不能保证耗尽后完整保留。状态过大/重复标 unsupported，异常标 capture_error（记录器本身不可用时尽力记录），不向 HTTP 传播异常、不重发请求。
4. 扩展 SDK + MockTransport + 实际 pytest/Controller 链路：原版通过、未复现后，下一请求 STATE 含 run_id、执行事实、候选判定及剩余预算；另覆盖 Runner 保存记录前受控注入 COMMAND_TIMEOUT 后的下一请求。没有修改 Controller 或 TaskProgress 产品逻辑。

## 调试与审查

- 初始新测试：7 failed / 6 passed，包含真实 rg 日期路径丢引用、约 32 KiB 返回未缩短、STATE 无独立正文；先确认 RED 再修实现。
- 相关回归曾发现入口前置缺少尾部换行，源码第一行紧贴入口；已修并保持字节额度。
- 独立审查使用 gpt-6.1-sol，未使用 Astra。审查确认引用/状态采集边界，指出新解析器曾对正文中的每个数字分隔符复制并缓存无效前缀，形成平方级内存放大。
- 该审查问题已有压力 RED：12 KiB 量级正文造成 4001 次路径探测。修复为仅检查登记路径长度和别名，约束扫描窗口、行号位数，并且只缓存有效登记路径。压力测试要求探测<=2、缓存<=1、tracemalloc峰值<2 MiB。
- 拒绝将原有 1 MiB HTTP 解析保护及全任务采集容量耗尽后的省略视为本次缺陷；这些边界维持并明确记录，不承诺无限采集。

## 验证

相关回归（包含新增压力用例和三项 wire 边界）：81 passed。

最终四组全量覆盖 **1196 项：1190 passed / 6 skipped**，每组 exit 0。54 个集成测试文件分组为 18/18/18，清单互不重复且无遗漏；另跑完整 tests/unit。结果：

| 分组 | 结果 | 耗时 | 日志 |
|---|---|---|---|
| integration 0 | 154 passed | 542.00 s | full-0.log |
| integration 1 | 158 passed | 346.56 s | full-1.log |
| integration 2 完整重跑 | 147 passed, 2 skipped | 212.90 s | full-2-retry.log |
| unit | 731 passed, 4 skipped | 533.47 s | full-unit.log |

所有命令使用 `.venv/Scripts/python.exe -u -m pytest -v <分组文件或 tests/unit> -p no:cacheprovider --basetemp <仓库外独立目录>`。这是分进程全量覆盖，未宣称单进程全套一次通过；不覆盖跨组的进程级测试顺序依赖。

integration 2 首次为 1 failed / 146 passed / 2 skipped：SWT 控制测试在未改动的 store.atomic_write/os.replace 处两次遇到 WinError 32 文件占用，未到测试判分断言。无法确认占用者，不归因于具体外部进程。独立新目录复测为 1 passed，随后同一组全部文件新目录重跑 exit 0。保留 full-2.log 与 filelock-rerun.log，不把首次失败隐去；本次未修改底层原子写入。

原始日志在 .local/context-repair/。测试显式设置 REPROAGENT_RG_PATH=E:/ReproAgent/.local/bin/rg.exe；外部 basetemp 故意含 -2026-10-18，不回避日期路径。

本次没有付费模型调用、真实案例重跑或官方 SWT 判分。不能用离线请求送达测试推断模型一定会选读材料或复现率提升。材料完整性仅代表当前收到的文本保存范围，上游完整性仍可能为 unknown。

## 独立验证报告的统计更正

外部 VERIFICATION.md 的四组合计原始日志为 1172 passed / 10 skipped，原表合计误写 6 skipped。补跑四项 rg 用例后才是 1176 passed / 6 skipped。这里仅记录核对结果，不改其他代理的验证文档。

交付：修复和验收记录保留在 codex/task-state-context；不修改其他代理的验证材料，不 push 或合并。
