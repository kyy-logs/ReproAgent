# 契约范围、重复验收与观测修复

任务开始于 2026-10-16，继续于 2026-10-17。代码在 `codex/holdout-execution-reliability` 隔离分支，基线 `f659551`。没有改写既有评测轮次或 verdict，没有调用真实模型。

## 修改范围与验收条件

1. **原始 Issue 独立保留。** `build_review_context` 从 task/input/issue.md 按 contract.description_hash 校验并加载原始报告；不能仅由模型派生契约替代报告目标。若完整原文已经在 expectation_sources 中精确呈现，original_issue 使用明确的索引复用，避免重复正文；不拿局部摘录充当完整 Issue。有绑定哈希但原文缺失或被改动时，验收返回 EVIDENCE_UNAVAILABLE。空 description_hash 的旧记录兼容旧投影。
2. **契约范围与正常对照。** 分析、探索和审查提示均要求：不同对象/触发分支不能因共用消息模板而自动等价，不能把该差异降为无关备注，也不能将 revision.reason 当成正确行为来源。正常对照可以通过，不必复现缺陷，但仍须有原版语义依据。这部分是模型行为约束，不能声称从程序上证明了语义等价。
3. **重复验收原因拆分。** 复用确定性绑定、完整执行、测试清单、预置条件和失败签名比较，分别报告 execution_consistent 与 semantic_confirmed。模型判定不一致时反馈 semantic_disagreement，保留两次运行和两份分类，不投票、不缓存首次肯定结果、不自动变成 DONE。`replay_observation` 使用受控结构校验，进入下一轮 SDK 请求、事件及 Trace。
4. **正常 SDK 阶段结束。** phase 已有结果时，在 on_reasoning 中提前结束，避免进入 SDK _call_model 后被当作模型重试失败；agent_reply 的 PhaseEnded 显示为正常结束，原生 OTel 状态仍保留。真正的协议、取消、预算和工具错误继续走原有通道。
5. **评测汇总。** EvalResult 追加 accepted_candidate_id / export_state，从 TaskResult 映射。字段可表示单次认可的候选和已导出的诊断包，不等于成功；prediction 仍只对 DONE 输出正式 patch。
6. **启动来源预检。** 新增受版本管理的 scripts/run-sdk-round.ps1，在读取凭据前校验 Python 实际导入的 src 与 evals 目录，记录提交、源码指纹和未提交数量；支持 ExpectedCommit 与 CheckOnly。旧 E 盘 .local 入口已备份并改成要求显式 ProjectRoot 的兼容包装，避免静默运行旧主检出。CheckOnly 不读取密钥，也不调用模型。

没有删除来源保护、候选哈希、完整执行、断言/语义审查、独立重复、修复版验证或 DONE 门禁；没有增加任务步数、命令期限、模型调用次数或验收容量。

## 定向回归

- 新增缺口先红：原始 Issue 丢失、被篡改/缺失未阻止审查、重复执行与审查分歧不区分、正常 PhaseEnded 误报、评测字段缺失。
- 37 项 SDK/评测相关测试通过：targeted-expanded.xml。
- 73 项核心验收、反馈、原始 Issue、启动器相关测试通过：related.xml。
- 2 项 PowerShell 来源预检通过：launcher-final.xml；正确工作树可通过，错误提交或导入另一 checkout 的解释器在读取凭据前失败。
- Trace 回归先确认白名单遗漏（trace-red.xml），补充受控字段后，29 项补充回归通过：final-related.xml。
- 定向测试存在交集，不能把这些数量相加当作唯一测试总数。
- 标准串行全量运行已结束：1,495 项全部执行，1,484 通过、10 跳过、1 失败，exit 1，耗时 2,065.563 秒，product source_unchanged=true。原始回执保留在 serial-full-suite/summary.json 与 all.xml，不改成全绿。
- 唯一失败为 test_an_unconfirmed_replay_returns_to_the_phase_without_a_package 的旧英文反馈句断言；该测试的状态、两次执行、无导出包及动作结束断言均已通过。其 confirm=False 模拟与实际两份肯定证据不一致，现改为注入明确的 failure_signature_matches 比较失败，检查结构化 execution_unconfirmed 反馈。
- 只修改该测试，产品代码保持不变。随后该测试、新增原始 Issue/重复验收测试、真实 SDK/Trace 反馈链路及 PowerShell 启动预检合计 11 项通过（40.60 秒，exit 0）：final-after-full.xml。没有再次启动整套全量，因此应如实同时引用全量原始结果和修正后的定向结果。
- 10 项跳过均为宿主 Windows 无符号链接权限或当前解释器不经符号链接启动，不是为本次修复新增的跳过条件。

### 首轮并行测试未完成

首轮三个互斥测试组出现失败标记且明显变慢，已保留日志并停止本次拥有的测试进程，不能算通过。full-suite/summary.json 的 complete=false，XML 未写完，不能将其中 tests=0 理解为通过。

已确认至少一例 root_null-invalid_structure 在模型调用前的环境探针失败：cleanup_ok=false，任务 BLOCKED/ENVIRONMENT_BLOCKED；原始 task.json 与 events.jsonl 已保留在 environment-failure/。不能据此认定所有并行失败都来自同一原因。

两例失败标记对应的测试隔离重跑均通过（failure-probe.xml，2 项）；经验生命周期文件串行重跑全部通过（diagnostic-suite/group1-first.xml，37 项）。最终仍以完整串行运行结果判断，不扩大产品超时，也不跳过失败测试。

## 8474 历史记录零调用回放

原始产物为 review-capacity-live-8474-20261016。程序读取该候选的两次独立执行及既有 verdict，得到：

```json
{
  "kind": "semantic_disagreement",
  "execution_consistent": true,
  "execution_unmet_checks": [],
  "semantic_confirmed": false,
  "verdict_classes": ["REPRODUCED", "INVALID_CANDIDATE"]
}
```

新反馈 5,024 B，验收材料 23,047 B。原始 Issue 的完整来源通过 original_issue.expectation_source_index=0 保留，绑定原始内容哈希。回放新增模型调用 0、新测试执行 0；所有读取的历史输入哈希未变化。

详见 replay.json、8474-replay-feedback.json、8474-review-payload.json。该回放只验证材料与诊断链路，不替代新的 Agent 生成运行，也不证明候选应获语义通过。既有任务仍为 NEEDS_INFORMATION，不运行官方判分，不制造成功分数。

## 独立审查与最终状态

独立审查未发现可行动的重要缺陷；审查代理独立运行 test_scope_replay_repair.py 与 test_round_launcher.py，9 项通过（34.71 秒）。主会话全量与最后定向回归记录如上。git diff --check 通过；8474 零调用回放再次通过，源码哈希 ae6ae12f5ff4da253f08321b98ed43566aad69cb675fbe213806dca4cfaa5e64。尚未提交、合并或 push。

本次已完成离线修复验证，但没有新的真实 8474 模型运行，没有固定版本验证或官方判分。提示词的范围约束仍需真实样本验证，不能把历史 NEEDS_INFORMATION 改报为 DONE。验收没有使用 Astra。
