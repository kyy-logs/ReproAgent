# Refusal experience trigger coverage

> **历史归档（2026-10-24）**：保存当时的设计、运行结果或验收依据，不能据此判断当前默认行为、最新测试数量或总体复现率。
> 当前入口见[文档导航](../../README.md)，旧路径对照见[归档索引](../README.md)。

用户已批准两类拒绝的最小修复，并要求先写计划、随后在本会话直接实现。基线 main 7948abe。

1. CandidateService 在 expected/sources/missing_information 校验实际失败处产生 GroundedContractRequired(ValueError)，code=GROUNDED_CONTRACT_REQUIRED，missing 为固定字段名元组。WriteCandidate 只捕获这一类型，返回 ERROR ToolChunk，metadata 的 reproagent_error_code 保存该码，正文给出 message/missing/next_action。其他异常不映射成该错误，拒绝不发布、不结束 phase。
2. 触发定义增加 tool_error/write_candidate/GROUNDED_CONTRACT_REQUIRED 和 controller_error/空工具/REVIEW_CONTEXT_TOO_LARGE。完整工具错误提取不再让 source_refs 成为所有工具的前提，但 revise_contract 引用错误仍要求它；其他工具及未知码保持拒绝。
3. 验收超限发生在工具外：Controller 用自身程序结果码产生 AgentContext.experience_events（默认空元组，追加字段兼容旧构造）；在下一探索阶段把本次事实交给 runtime。Runtime 在 gate 重新开启后、下一已有请求之前排队，复用现有匹配、容量、取消和注入逻辑。不解析 feedback 字符串，不将旧历史所有错误重放。
4. 学习从 action.rejected/action=run_candidate/code=REVIEW_CONTEXT_TOO_LARGE 的受控小事实提取同条件。冻结、EID 引用及哈希校验保持不变；只写所引事实。工具拒绝的小事实和完整错误都支持写入条件。
5. 维持模型五字段、学习8192B、单卡2048B、冻结库、共享3摘要/2048B/一次详情、思考关闭、现有契约/Verifier 校验。不得扩大上限或截断验收证据。旧卡不自动补造条件。不新增 provider 请求，不使用 Astra，不运行付费 A/B，不自动 push/合并。
6. 验收覆盖两条真实SDK/mock HTTP请求链，以及 Controller 程序超限到下一 phase 的传递；未知、未发生、普通异常、取消和重复事件不可误注入。改善真实成功率留待冻结库 A/B。
