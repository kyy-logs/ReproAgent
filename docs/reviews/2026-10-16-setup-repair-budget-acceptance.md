# Setup 反馈与首次试跑预算修复验收

## 范围

在隔离分支 `codex/holdout-execution-reliability` 实现，基线 `a5186a5`。主工作区其他会话改动不参与本次修改，不合并、不推送。

- `core/setup_diagnostics.py` 与 Verifier：通过原有门禁后，对未进入 test call 的 setup 失败返回 `TEST_SETUP_FAILED`，包含最多四条 `nodeid / exception_type / message`、省略计数和受核验引用；先脱敏、规范化路径再节选。未推断候选/平台责任。
- Controller 累计真实 `original_execution_count`，跨契约修订保留；prepare/fixed 不计。运行消息和逐调用 progress 使用此事实，不从历史文本猜测。
- 首次执行前 reader reserve 为 `min(6, ceil(total*0.3))`，执行后为 `min(2, ceil(total*0.1))`。20 步不变。提示表达“下一次调用计步后能否读”，权限在实际计步后使用同一策略。初始剩八步开始提醒。
- 完整性、源码保护、来源引用、独立语义审阅、取消/费用/时限、SDK deny 优先级保持原门禁。

## 已验证

1. setup RED：4 failed / 2 passed；实现后 6 passed。相关 Verifier/反馈回归 **42 passed，exit 0**。
2. 预算策略/实际 SDK 新增 RED：16 failed；实现后 **16 passed**。Controller/进度/SDK 整组 **103 passed，exit 0，112.28 秒**。
3. `test_setup_repair_loop.py`：**1 passed，exit 0，10.51 秒**。真实 AgentScope SDK + Controller + Runner + 本地 pytest，仅 HTTP 模型回答脚本化。第 13 步首次发布 → setup 缺资源 → 下一次实际 HTTP 收到具体错误 → 第 14–17 步必要 Read 被允许 → 第 18 步补 data 文件发布 → 两次原版独立执行确认；共三次 original run，原始源码 hash 不变。无 setup 语义请求。

上述脚本化测试证明执行链可达，不证明真实模型一定会采纳反馈。该集成测试在前两项实现后首次运行即通过，没有另称一个独立 RED。

4. 独立审查发现异常 repr/JSON 转义值脱敏缺口，新增测试 RED 为 11 failed / 1 passed；补齐脱敏形式后，setup/经验触发/完整修正链 **26 passed，exit 0，14.80 秒**。审查者独立纯检查 37 passed，真实执行部分因其临时目录权限受限未验证；本文本地执行结果单独列出。
5. 首次全量尝试在约15%主动中止：发现旧权限回归写死 reserve=3，与新的4步任务 reserve=2 不符。已让该用例明确进入实际 reserve 后验证拒绝；不放宽权限断言。下方最终套件重新从头运行。

6. 同一 P2 的引号上下文反例：真实 FileNotFoundError 的整条路径可能改变 repr 引号选择；RED 2 failed / 1 passed。覆盖两种 Python 引号后，setup + SDK 纠错链 **22 passed，exit 0，11.67 秒**；864 个合成实际异常路径探测通过。原审查者限定复查 **20 passed**，确认原 P2 解决，未发现新绕过。
7. 第一次三组全量执行的外部验证脚本未创建公共 basetemp 父目录，产生 fixture setup 的 WinError 3；这是本次验收驱动的问题，不是产品失败。已补建父目录，保存旧日志并重新运行，不计为通过。

## 真实 8474 单轮结果

- 在 `9c990e6` 冻结源码上实跑，**EXHAUSTED / NONE**，207.016 秒，24 HTTP，763,459 tokens；未配价格，货币成本未知。仍是原20步与关闭thinking。
- 发布步骤 **17 / 18 / 20**；3 候选 / 3 执行 / 3 verdict。首次发布确实比上一轮20步提前，但本例没有成功，不能说通过率提高。
- 首候选审阅材料 **67,552 B > 32,768 B**；第二候选在call调用 `SphinxTestApp` 时传入不支持的 `builddir`；最后候选的否定警告断言未被独立模型认可（`CANDIDATE_ASSERTIONS_UNCONFIRMED`）。程序记录的异常与模型审阅意见分开保存。
- 前两次完整 SDK 输入分别含原版执行计数1/2及反馈，各自 SDK reply 下有一个实际HTTP；第三次反馈虽进入SDK输入，该reply下没有HTTP，未声称送达。20份wire正文被head/tail摘录，无法完整复核前两条反馈正文；离线完整wire测试独立证明发送链路。
- 本轮全部进入call，不再是上次缺资料目录的setup路径，因此没有在真实8474中触发新的 `TEST_SETUP_FAILED`；这一分支由真实本地pytest + SDK集成覆盖。
- 运行源码hash `058f0ab8ccc6feaa467f189e77c82b8580f3595a4681865c2c6d10824c18b3e2`，轮次seal和当时源码已核验并保存 `source-revalidated.json`。之后最终 `17c9c5b` 仅补引号脱敏与空行，未再付费重跑，不能声称本轮使用最终提交。
- 没有可接受候选，未提交空补丁到官方判分。没有更改冻结issue/参考修复/金标测试。
- 实跑分析：`E:/ReproAgent/repro-results/setup-repair-8474-20261016/run-analysis.md`；机器记录同目录 `run-analysis.json`；原始链路在案例 `task/observability/trace.html`。

## 最终全量验收（通过）

从最终源码收集 **1425** 个用例，按文件分为3个互斥组，独立仓库外basetemp。脚本断言文件并集无遗漏/重复，最终还用XML测试数与收集总数核对。命令、分组、日志和XML位于 `E:/ReproAgent/.local/holdout-five-20261015/setup-final-validation-2/`。最终结果 **1415 passed / 10 skipped / 0 failures / 0 errors，exit 0**，总1425与收集数一致；三组分别484/1、441/1、490/8（passed/skipped），各exit0。并行墙钟574.657秒。

## 限制

六步/两步是初始预留参数，本次真实模型仍在第17步才首次发布，未保证16步前发布。真实setup执行取代对动态fixture的不可靠静态猜测。没有新模型预检，没有放宽验收标准，也没有手写holdout候选。本次修好的是具体反馈和纠错预算机制，8474剩余的候选语义、API用法和证据体积问题尚未解决。

最终代码来源 hash `7caaee4b7a121cae94e2856742fb3d3f02b15817995d82ac3da1ec7e408487f0`，已验证生产目录无未提交改动。完整来源、命令及XML核对结果存于 `E:/ReproAgent/repro-results/setup-repair-validation-20261016/verification.json`。代码验收提交 `17c9c5bb1d6d39c8e57a09311c2ce0f914571222`；后续仅补文档。
