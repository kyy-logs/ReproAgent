# 探索步骤预算软收敛设计

状态：已实现并合并到本地 main，离线请求链与合并后定向回归通过，尚未 push。浏览器点击验收受策略阻止，真实模型有效性尚未评估；详见配套验收记录。

## 目标与范围

用户希望达到预算阈值后收窄探索，但仍利用剩余预算推进复现，而不是到阈值立即停止。第一版仅实现**探索步骤**软阈值，不扩展时间、费用、风险阈值。

现有硬预算、最后 3 步读工具保留机制、源码保护、候选发布、Verifier、独立重跑与结果状态均保持原语义。不新增模型调用、检索 Agent、任务状态枚举或 CLI 参数；thinking 保持现有配置。

本次采用“程序生成事实视图和收敛提示”。只在阶段起点提醒会在同一 ReAct 阶段内过时；新增工具相关性判定器则超出最小范围，均不采用。

## 当前代码与缺口

- `Budget.take_step()` 与 `ExplorationMiddleware.before_model_call()` 控制逻辑模型调用数量；HTTP 重试在 `_GuardedModel` 内，不另外消耗探索步骤。
- `AgentScopeRuntime._message()` 只在探索阶段起点提供剩余预算。一个阶段内可以连续发生多次模型调用，模型可能一直看到旧值。
- `ExplorationMiddleware._reserved_for_publishing()` 在剩余步骤不多于 3 且有契约时拒绝读工具。这是已有尾部护栏，不是提前收敛提醒。
- 当前 AgentScope 路径拒绝配置硬费用上限，也没有统一风险阈值引擎，不能将本次步骤功能描述成四类预算均已支持软收敛。

## 阈值、计数与状态

沿用任务的同一份 Budget，不新增计数器。令 `L=agent_steps`、`U=steps_used`：

```text
soft_threshold = (3 * L) // 4
remaining_steps = max(0, L - U)
U >= L                 → exhausted，禁止新探索请求
soft_threshold <= U < L → converging，下一请求携带收敛指导
U < soft_threshold     → normal，下一请求携带简短预算事实
```

快照在本次调用扣步**之前**生成，remaining_steps 包括准备发出的这一轮。默认 L=20：第 1–15 次请求 normal；第 16 次请求看到 used=15、remaining=5、converging；第 20 次看到 used=19、remaining=1。第 21 次仍由原 guard 拒绝，不能发 HTTP。

小预算采用同一公式：L=1 时第一轮就是 converging；L=2 时第二轮开始；L=3 时第三轮开始。这不会解除现有读工具权限，例如 L≤3 时原保留机制仍可能直接拒读。此版本不承诺改善这类极小预算的探索能力。

软模式是预算的派生视图，不写入 TaskState。阶段结束、换候选、修订契约、协议纠错都不重置 U；HTTP 重试重复发送同一轮输入，不重复扣步或刷新提醒。

## 每轮提醒如何送达

在 `ExplorationMiddleware.on_model_call()` 现有输入处理完成后、调用 guarded model 之前，追加且仅追加一个临时 `SystemMsg(name='exploration-progress', ...)`。它只存在于本轮 input_kwargs，不加入 SDK 持久消息历史。后续轮次重新生成；现有 experience-browse 提示继续独立工作。

运行时在每次 `explore(AgentContext)` 起点更新 middleware 的 phase_context。消息包含程序事实：mode、total_steps、used_steps、remaining_steps、soft_threshold、next_call_index、contract_version、missing_information_count、contract_grounded，以及真实发布的最新候选 ID（若有）。只在当前 history 有对应 candidate_id 时附该候选的 result_code，否则明确 unknown；不得把旧候选结果套到新候选。

不复制 Issue、源码、工具输出、feedback 或 missing_information 原文进系统指令。实际缺失项、具体拒绝和执行诊断继续从已有阶段输入与工具结果读取。候选 ID 和结果码只允许短的 ASCII 字母/数字/下划线/连字符标识（最多 128 字符），不符合则 unknown。没有事实就 unknown，不总结所谓“最佳假设”。

渲染后消息最多 1024 UTF-8 字节。超出时先去掉可选候选字段，再去掉可选契约字段，保留计数和固定指导；不能裁断 JSON 或任意文本。提示中不出现密钥或用户自由文本。

normal 只发送事实；converging 增加固定指导，要求聚焦当前路径、仅为具体阻碍做必要的定向读取、避免重开宽泛搜索或重复不变的拒绝。契约和证据充分才发布，否则基于已展示依据修订或 request_information。执行与独立确认归 Controller，验收标准不放宽。

软模式不新增权限拒绝。剩余 3 步的现有读工具保留机制仍优先，模型收到的指导必须写明“while readers are allowed”，不能承诺这时还能读取。guard、取消、时间上限、上下文容量与已完成 phase 的优先级不变。

现有压缩检查发生在 middleware 加临时提示之前，不能假定它已计入新增提示。最终消息组装后，用 SDK 本地 count_tokens 对实际 messages/tools 再检查原有 `trigger_ratio * context_size` 阈值；到达阈值仍返回 NEEDS_INFORMATION，不压缩、不截断、不发送。该检查不扣探索步骤，也不调用 provider；已完成 phase、取消、任务到限与步骤耗尽优先走原 guard，不得被容量错误覆盖。

## 观测与可证明边界

- 在既有 `exploration.step` 事件增加扣步前快照字段；原 steps_remaining 保持扣步后语义，二者明确区分。
- 第一次实际通过 guard 的 converging 逻辑调用记录一次 `budget.convergence` 转换，作用域为任务；各已准入逻辑调用记录 `budget.progress` trace 点，字段放在已有允许的 budget 属性内。
- 转换与 progress 事件表示调用已准入，不表示 HTTP 已发送、provider 已收到或模型遵从。只有下一次捕获的真实 wire_request 才能证明提醒进入了请求体。
- 日志和 trace 写入失败不改变业务结果，不新增高基数指标或 viewer 页面；现有 trace 通用详情展示 budget 属性，现有 wire_request 展示完整请求。
- 这些信号不记录隐藏思维，也不证明实际探索更高效；不开 thinking 仍能使用。

## 验收

离线使用真实 AgentScope、formatter、guard 与 httpx.MockTransport，捕获最终 JSON 请求体：验证默认第 16 轮、同阶段刷新、跨阶段不重置、HTTP 重试快照不变、不污染历史、经验门禁共存、硬上限与取消不发额外请求。补测 L=1/2/3、1024 B、未知事实、标识注入、权限结果不变和观测故障。

测试只能证明提示机制与边界正确。有效性另需用户授权的真实对照实验，比较候选首次发布时间、重复拒绝/无效读取、证据等级及未依据证据发布的候选。不能仅凭 DONE 数量或一次对照就称收敛有效。

## 后续方向

单独评估尾部必要读取的有限放行，不能直接删掉最后 3 步护栏。时间软阈值、统一费用账本与供应商硬费用边界、风险分级停止都需要另行设计，本次不实现。
