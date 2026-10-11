# 学习请求失败修复验收

状态：离线验收完成。完整 pytest：765 passed、5 skipped、exit 0；HTML viewer：8 passed、exit 0。没有真实模型调用，没有使用 Astra。

基线 `a401e227f6eaf2485ea07646ddec634f307392d8`，修复分支 `codex/learning-provider-errors`。本记录生成时尚未合并或推送。

## 证据与定位

真实记录：`E:/ReproAgent/repro-results/cli-8365-1/observability/trace.json`。学习材料 ready，8153/8192 字节、10 条证据；一次学习 HTTP 尝试为 http_error，随后 experience.learning 被错误归为 invalid。接口没有返回已采集的 usage；不能把汇总中的 0 解读成没有费用。

记录中的学习 wire request（content ID `8be1e515e46642e7`）完整保留，启用 `response_format={"type":"json_object"}`，但 system/user 中均无 JSON 指令。学习提示词与其他结构化阶段不同，没有明确要求 JSON。这违反了 [DeepSeek JSON 模式说明](https://api-docs.deepseek.com/guides/json_mode/) 中的提示词要求。

历史运行没有保留 HTTP 状态码或错误正文，不能恢复具体拒绝原因，也不能断言那次就是 HTTP 400。此次修复纠正已确认的请求契约缺陷，并补齐未来定位所需的诊断信息。

## 修改范围

- `prompts/extract_experience.md`：明确要求单个 JSON 对象及空经验对象，不输出 Markdown。
- `core/protocol.py`、AgentScope `model_factory.py`：安全的 ProviderRequestError；每次 HTTP 尝试保存实际状态码；HTTP/网络重试边界不变。
- `observability.py`、`otel_projection.py`、`trace_rendering.py`：状态码白名单与错误分类；关联 HTTP span 的有限脱敏错误摘要，只选择 code/type/message，不复制 headers/debug。每字段最多 512 字符，先脱敏再限长；正文省略明确标记。
- `experience.py`：学习请求失败返回 provider_error；经验结构不合法仍返回 invalid；unknown_usage_attempts 区分已报告 token 小计与缺失 usage。
- `docs/experience.md`、三组 SDK/生命周期回归测试：说明和保护新行为。

未改探索策略、权限集、Verifier、经验材料的 8192 字节上限、库追加规则、主任务退出码、thinking 配置或预算。一次逻辑学习请求仍最多三次 HTTP 尝试，主任务先封存，学习独立执行。trace 中的错误摘要不作为学习材料，不增加模型调用。

## 验证

| 检查 | 结果 |
| --- | --- |
| JSON 模式接口拒绝无指令的学习请求 | 修复前真实 SDK + MockTransport 回归失败；修复后通过并能写经验 |
| HTTP/网络失败区别于无效经验 | 修复前 invalid；修复后 provider_error，库和封存结果不变 |
| 400/401 与 429/503 重试 | 前者一次，后者最多三次；每次状态码与未知费用分别记账 |
| 503 后 200 | 不继承前次状态，结果为 completed |
| trace 错误正文 | 已知密钥脱敏、限长、排除 headers/debug，HTML 可安全展示 |
| usage 缺失 | unknown_usage_attempts 可见，未知费用不伪装成免费 |
| 定向回归 | 37 passed，exit 0 |
| 完整回归 | 765 passed、5 skipped，653.54 秒，exit 0 |
| HTML viewer | Node 8 passed，exit 0 |
| 补丁格式 | git diff --check 通过 |

完整日志保存在 `E:/ReproAgent/.local/learning-provider-errors/`。原始请求的离线重放记录在 `E:/ReproAgent/.local/learning-provider-fix-replay/result.json`：用户材料逐字相同，均为 8153 字节；旧提示词在模拟接口下 400，修复后的实际 extract_experience 路径为 200/empty。thinking 关闭，每路一次尝试，真实 provider 调用为 0。empty 是模拟接口返回空经验，不代表真实模型总结质量。

## 独立审查与限制

GPT-6.1-sol 新上下文只读审查：Critical/Important/Minor 均无问题；在完整回归通过后可集成。

审查未判断项及处理：历史实际状态/正文不存在，因此不作断言；真实 provider 行为未调用，因此不宣称线上验证；审查时完整回归尚在运行，现已由执行方确认通过；既有压缩/超限响应作为协议失败的策略保持不变，不在此次重新设计。

此次不修改原始任务记录，不重新执行 pytest-8365，也不证明该任务的 EXHAUSTED 已解决。需要后续真实运行才能验证线上学习调用和经验质量。
