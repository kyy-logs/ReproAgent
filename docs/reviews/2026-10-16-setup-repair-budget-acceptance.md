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

## 待完成

- 完整离线套件最终退出结果。
- 一次全变更 fresh-context review 与发现处理。
- 新一轮同冻结输入、20步、thinking关闭的 DeepSeek 8474 实跑及来源核验；仅有可发布产物时才进入官方判分。

## 限制

六步/两步是初始预留参数，无法保证模型在第16步前发布。真实 setup 执行取代对动态 fixture 的不可靠静态猜测。没有新模型预检，没有放宽验收标准，也没有手写 holdout 候选。
