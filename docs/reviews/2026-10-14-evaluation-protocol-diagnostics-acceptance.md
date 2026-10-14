# 评测 trace 与协议错误诊断：第一批修复验收

日期：2026-10-14（Asia/Shanghai）。分支 `codex/evaluation-protocol-diagnostics`，起点 `ee1b802`，实现提交 `bde83eb`。本次是用户批准的已有流程修复，未修改候选语义或评测环境。

## 解决的问题

1. `evals.run.run_case` 直接运行 Controller，没有 CLI 的 trace_session。评测失败后只有笼统事件，无法查看被拒绝的原始工具参数。
2. 工具参数的不同解析错误都被折叠为 INVALID_TOOL_INPUT，下一轮模型不知道问题类型或位置。
3. 终止文字写成“all 3 attempts”，容易把一次 MULTIPLE_TOOL_CALLS 加两次 INVALID_TOOL_INPUT 误读成三次相同错误。

## 实现

- 评测默认每例独立采集并落盘 trace.json/trace.html，沿用现有 AgentScope/OTel、本地内容限额、已知凭据脱敏与路径保护，不新增外部观测框架或命令参数。
- CLI 与评测共用 trace_output.py；结束/写入失败输出固定警告，不打印异常原文，不改变主结果。JSON 写入失败不生成孤立 HTML。
- run_case 的 Python API 提供 trace=False 关闭选项，用于对照；默认命令自动采集。
- 主错误码仍为 INVALID_TOOL_INPUT / MULTIPLE_TOOL_CALLS / UNKNOWN_TOOL，不改变权限三态、经验触发规则或候选验收。
- 工具输入细分为 JSON_SYNTAX_ERROR、JSON_ROOT_NOT_OBJECT、JSON_DUPLICATE_KEY、JSON_NON_FINITE、TOOL_INPUT_TYPE_ERROR、JSON_NESTING_LIMIT、JSON_PARSE_ERROR。语法错误带 parser 行/列/偏移，不带原始参数、字段名或解析器异常原文。
- 下一实际纠错请求带安全诊断和累计次数，原始被拒参数不回放入聊天；原始返回仍可在受限/脱敏 trace 的 provider_response 中查看。
- 每个 phase 仍最多三次累计协议拒绝，成功工具调用不重置。终止信息、事件与 trace 记录真实混合序列。

观测本身不增加模型调用。诊断文字扩充会占少量输入 token，不能将其说成模型运行完全零额外 token。thinking 配置未改。

## RED/GREEN 与回归

所有执行使用真实 AgentScope、formatter、Controller 或 runtime，外部模型接口由 httpx.MockTransport 替代；未调用真实 provider，未使用 Astra。

| 检查 | 实际结果 |
| --- | --- |
| 修改前 action correction / eval runner / CLI observation / SWT batch 基线 | 31 passed，50.53s |
| 新协议诊断 RED | 6 failed：缺类别、累计历史、诊断模块 |
| 协议诊断、既有纠错和 runtime GREEN | 51 passed，24.04s |
| 新评测采集 RED | 5 failed：默认无 trace、关闭参数不存在、共享 writer 不存在 |
| 初次组合验证 | 58 passed / 1 failed；失败为新测试使用空 record key，改为现有合法 key `task`，未放宽存储规则 |
| 集中验收 | **209 passed，205.55s，exit 0** |
| 共享解析器的模型网关、model factory、provider error 与协议回归 | **88 passed，23.25s，exit 0** |
| 独立审查者定向验证：两个新增集成文件与既有 action correction | **23 passed，15.45s，exit 0** |

集中验收命令（在隔离工作区执行，basetemp 不在任何仓库内）：

```powershell
.venv/Scripts/python.exe -m pytest -q tests/integration/test_eval_observability.py tests/integration/test_protocol_input_diagnostics.py tests/unit/test_action_correction.py tests/unit/test_eval_runner.py tests/integration/test_agentscope_runtime.py tests/integration/test_observability_lifecycle.py tests/unit/test_trace_rendering.py tests/unit/test_store.py tests/integration/test_swt_batch.py tests/integration/test_swt_predictions.py tests/unit/test_swt_results.py tests/integration/test_agentscope_observability.py tests/integration/test_agentscope_first_browse.py tests/integration/test_experience_evaluation.py tests/unit/test_controller.py --basetemp C:/Users/Dell/AppData/Local/Temp/reproagent-eval-protocol-acceptance
```

覆盖点包括：

- 具体类别/行列确实进入下一序列化 HTTP 请求，不仅是内部事件。
- 混合三次拒绝之间有一次成功 Read，仍按累计三次终止，不执行被拒绝调用，不请求第四次纠错。
- trace 开/关的结果、实际请求数、usage 计数相同；trace finish/JSON/HTML 故障不改变结果。
- 两个评测案例各自拥有 trace_id，正文不跨例混入。
- 合成密钥在 Issue 中出现时，trace 仍不暴露该密钥；拒绝参数仅在原始返回的受控采集中保留，不出现在纠错输入或安全事件中。
- 现有 CLI 默认写入/关闭/故障隔离、源码绑定、SWT 分母/预测补丁、经验行为与 Controller 回归。

测试夹具的工具响应辅助函数有差别：tool_response 会 JSON 编码参数，原始非法 JSON 用能原样保留 arguments 的 tool_reply 构造，避免误测成“根节点字符串”。

未执行无关全仓测试，不将此结果表述为全仓全绿；未重跑真实 11445 或 SWT 官方容器。

## 离线样例

`E:/ReproAgent/repro-results/eval-protocol-offline-20261014/` 保存 trace.json、trace.html、events.jsonl、task.json 和 README。
这是真实评测入口配合脚本回复的观测样例：一次分析请求加三次非法工具参数，最终 FAILED/MODEL_PROTOCOL_ERROR。不是历史11445的原始记录，也不证明真实模型已学会纠错。

## 独立审查

独立审查范围为 `ee1b802..bde83eb` 的产品代码及测试；Critical / Important / Minor 均无发现，结论为本次限定范围可合并。审查确认安全诊断进入下一实际请求、混合协议拒绝累计行为、trace 收尾与故障隔离，并独立执行上述 23 项测试及 `git diff --check`。

审查者未调用真实 provider 或 Docker。其首次沙箱内测试因长期无输出被中断，不计作通过；有结果的 23 项来自随后允许访问隔离工作区的执行。此审查不证明真实模型纠错效果，也不能恢复历史11445响应。

## 未包含的后续工作

本轮不修 8627 的 pkg_resources/tox 环境，不修改 8365/15678 的候选生成策略，不更新评测 HTML 或官方成绩。原 holdout-diag-001 的 2/10 final 结论不变。
历史11445没有保存的原始响应不能恢复；需要新的真实请求时另获授权。未合并 main，未 push。
