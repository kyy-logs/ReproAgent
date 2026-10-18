# Task State / Material Offload 验收记录

日期：2026-10-18。基线 origin/main `0c17d5387faefee3e5e0bfd0b0a7fa8fc263c352`；分支 `codex/task-state-context`。远端默认分支是 main，没有 master；实现位于隔离 worktree，未覆盖 E:/ReproAgent 中其他会话的改动。

## 交付范围

- TaskStore 的现有任务、契约、候选、original 执行和独立判定记录驱动 `STATE.json`，模型不能写入状态或宣告验收通过。
- 每次探索模型调用前组装至多 4096 UTF-8 字节的 user 数据消息，刷新预算和当前状态；只加入该次请求，不积累在 SDK 历史中。
- Read/Grep/Glob 的大 SDK 文本先脱敏、登记，再返回受原有 tool_response_bytes 限制的原文摘录、引用 sidecar 和材料入口。未调用摘要模型。
- `read_artifact` 按 ID 分页读取已登记材料；长单行可按 column 续读。`artifact_id="catalog"` 分页读取完整索引，STATE 仅保留最近三项短信息。
- 程序摘要保留 stop_reason、exit_code、collected/executed/probe_complete，区分执行不完整与已复现。材料存储完整性与上游完整性分开表示。

## 维持的边界

Controller / Verifier 的成功标准、权限状态、源码保护和引用资格不变；fixed 执行及其判定不登记为探索材料。read_artifact 读回的诊断正文不产生新的源码引用权，且受步骤、时间和发布预留限制。已有 OTel 和 HTTP 观测继续工作，context.prepared 新增 revision/bytes/hash 供核对。

没有增加 provider 或摘要模型调用，未开启 thinking，未调用真实付费 provider；新增状态及摘录进入请求会消耗输入 token。测试和审查未使用 Astra。

## 测试与审查

- 原始新增功能测试：25 passed；SDK 文件工具、领域工具与引用边界：46 passed。
- 独立审查使用 gpt-6.1-sol，只读全分支。审查者独立复现：缺失 hash/kind 的材料元数据能被错误注册，随后导致 KeyError，中断下一轮模型请求。
- 修复前新增 8 项边界用例失败；修复后材料/状态单测合计 26 passed。
- 修复后 SDK 真实序列化请求链及已有阈值/工具集回归：6 passed。覆盖预算更新、拒绝进入下一轮、临时状态不积累、大输出读回、契约变化与发布预留拒读。
- 原压缩阈值测试的首轮估算未包括新增 task_state，已更新测试校准以测实际输入；保留候选优先及跨阈值的业务断言。原工具集断言明确加入 read_artifact。
- 最终全量覆盖 1182 条：**1176 passed / 6 skipped / 0 failed**。四个独立进程均 exit 0；所有集成测试文件按排序序号对 3 取余分组，另一个进程跑 tests/unit。清单核对为 18/18/17 个集成测试文件，互不重复且无遗漏；没有其他测试目录。

| 分组 | 结果 | 耗时 | 退出码 |
|---|---|---|---|
| integration 0 | 141 passed | 562.03 s | 0 |
| integration 1 | 185 passed, 2 skipped | 439.38 s | 0 |
| integration 2 | 119 passed | 488.90 s | 0 |
| unit | 731 passed, 4 skipped | 640.28 s | 0 |

各组命令均为 `.venv/Scripts/python.exe -u -m pytest -v <分组文件或 tests/unit> -p no:cacheprovider --basetemp <仓库外独立临时目录>`。最终产品代码在各组启动前已固定。分组运行不验证跨组的进程级测试顺序依赖；之前单进程回归未完成，不能算作绿色全套结果。完整日志归档于 `.local/task-state-context-acceptance/`。

回归中另有两处旧测试假设需更新：trace 开关请求对比固定预算时钟后仍比较全部消息和工具字段（单项复测 1 passed）；损坏经验库测试将“6 个工具”改为逐项核对实际 7 个基础工具名，任务结果及经验库错误隔离断言保留。上述用例均在最终分组回归中通过。

## 修复的边界

| 问题 | 结果 |
|---|---|
| 总存储额度耗尽后重复登记错误地把完整材料标为不完整 | 复用已有元数据，不重新按剩余额度推算 |
| 元数据写入失败留下不计预算的正文 | 回滚正文；回滚失败时停止继续写入；重开后孤立正文计入额度 |
| 缺失或类型错误的元数据使读者崩溃 | 注册前校验所有消费字段；损坏条目不进入目录 |
| STATE 只显示最近三项，较早材料没有可达入口 | 固定 catalog ID 提供受限分页目录 |
| 工具协议错误事件名不一致，无法更新当前阻塞 | 使用真实 exploration.protocol_error 事件 |
| 材料目录不可用、目录页过小 | 受控错误，不承诺虚假的可读 ID 或无进展分页 |

## 适用限制

STATE 是当前进度投影，不是跨进程恢复执行的 checkpoint；重开后只从受控 task 记录恢复任务元信息，再由当前 phase 提供契约。hypotheses 初版不从聊天推断。每项材料 256 KiB、每任务正文总量 4 MiB/128 项，上限之外的内容明确标记未完整保存。SDK 上游可能已经截断，upstream_complete=null 不能解释为全文。

本次验证功能与边界，不声称提高真实 Bug 复现率或评测分数。未 push、未合并。
