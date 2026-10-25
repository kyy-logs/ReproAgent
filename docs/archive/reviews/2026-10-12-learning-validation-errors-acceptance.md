# 学习字段契约与诊断验收

> **历史归档（2026-10-24）**：保存当时的设计、运行结果或验收依据，不能据此判断当前默认行为、最新测试数量或总体复现率。
> 当前入口见[文档导航](../../README.md)，旧路径对照见[归档索引](../README.md)。

状态：代码修复与离线验收完成；真实 A/B 未运行，等待明确授权。

基线 `428dee846f8d69ea426034bb7cadb600db3ceda2`，隔离分支 `codex/learning-validation-errors`。完整 pytest：790 passed、5 skipped、655.41 秒、exit 0；定向回归 75 passed；Node viewer 8 passed。验收和独立审查未使用 Astra。

## 原始证据

`E:/ReproAgent/repro-results/ab-learn-5227/observability/trace.json` 中，学习 HTTP 200/completed，prompt 3070、completion 253、content_bytes 1041；回复的非空 experience 有 category/tags/summary/detail，但没有 evidence_ids。因此拒绝写库是正确的，错误在于将校验原因统一折叠成 invalid。

正式用户材料里的 response_schema 已将 evidence_ids 列为 required，旧提示词也提到引用要求；不能说正式 schema 与校验器不一致。此次让五字段要求和结构示例更醒目，并消除末尾“return null”与顶层对象要求的歧义。提示词改善不保证每次模型都遵守契约，程序仍严格校验，不从正文猜引用或自动补字段。

旧 on-5227 虽然 DONE，但 experience.matched=[]，不能证明卡片生效。还有一个控制变量差异：旧开启臂实际工具表为七个，含 read_experience；关闭臂为六个。因此不能说两臂所有模型输入完全相同，也不能把结果差异只断言为随机波动。匹配是标签/摘要关键词打分，不是按用例 ID 隔离；同用例生成的卡也可能零分。

## 实现范围

- `prompts/extract_experience.md` 明确五个必填字段、显式 evidence_ids 数组、结构示例与空经验对象。标签取自实际框架/module/API，不为强行命中而编造。
- `experience.py` 使用受控 LearningValidationError 分类，CLI/Event/Trace 汇总带 code 和 missing_fields；只列固定 schema 字段，不回显任意模型字段或异常文本。
- 区分 JSON、结构、缺字段、额外字段、字段值、无效引用、证据变更、卡片大小、模型协议、provider、内部与存储错误。learn span 记录最终结果码。
- `core/serialization.py` 的 ObjectRootError 仍继承 ValueError，既有调用兼容；学习将合法 JSON 的非对象根归为结构错误。
- 单请求、HTTP 重试上限、独立学习预算、冻结引用、旧库与封存主结果保持原规则。不增加自动纠错、人工核对或强制注入。

## 验证证据

| 检查 | 结果 |
| --- | --- |
| 缺 evidence_ids | 真实旧回复离线重放为 invalid_missing_fields，missing_fields=[evidence_ids]，不改旧任务 |
| 缺其他字段/额外字段/错误结构 | 受控错误码；额外私有字段不进入错误诊断 |
| 无效/重复 EID、冻结证据变化 | 拒绝写卡，分别标识引用与证据问题 |
| 非法分类/标签/Unicode、超限卡 | 字段或大小错误，不误报 provider/内部问题 |
| 截断模型回复 | invalid_model_output，仍只有一次逻辑学习请求 |
| 非预期 KeyError / OSError | 分别 internal_error / store_error |
| 生命周期 | 校验失败保留主结果、交付包、旧库和客户端清理；诊断进入事件和 learn span |
| 初始 RED | 15 failed、1 passed，证明旧实现缺少具体诊断 |
| 审查边界 RED | 7 failed、3 passed，重现根结构/Unicode/内部分类问题 |
| 最终定向 GREEN | 75 passed，108.13 秒，exit 0 |
| 完整 GREEN | 790 passed、5 skipped，exit 0 |
| Viewer | Node 8 passed，exit 0 |

必要日志保存在 `E:/ReproAgent/.local/learning-validation-errors/`；旧回复重放在 `.local/learning-fields-ab-v2/old-response-replay.json`，真实 provider 调用为 0。

## 独立审查

GPT-6.1-sol 新上下文只读审查发现三项 P2，均先复现再修复：合法 JSON 数组/null 的结构分类、未知内部异常误报存储、不可编码 Unicode 字段逃出验证分类。跟进审查独立验证修复，并确认无新阻塞问题；完整回归随后通过。

未判断项：真实 provider 的提示词遵循率、因果 A/B 收益、跨 Issue 泛化。它们不能从离线验收推断。

## 后续真实实验已准备

计划与配置：`E:/ReproAgent/.local/learning-fields-ab-v2/plan.json`。DeepSeek Flash、thinking disabled、temperature 0；一次新 CLI 学习 5227，写入成功并自然命中后冻结库。开启臂用冻结卡库，关闭臂用合法空库，均只读且工具表一致；两臂各两次，交替次序 on/off/off/on，单任务最多 20 动作、主预算 900 秒。

门禁：学习写入、自然命中、开启臂实际 wire request 注入非空摘要、控制臂没有经验内容/成功详情读取、实际工具集一致、模型/Issue/代码输入及两个库哈希不变。失败提前停止，不强行补卡或改标签。每臂两次只作为同一 Issue 重复任务的小样本试验，不能证明稳定提升或跨 Issue 泛化。

本次真实调用被自动审批拒绝：会解密本地密钥，向 `https://api.deepseek.com` 发送公开 Issue、原版代码、工具/候选结果并产生 token 费用，而当前可信消息未被认定为明确授权。没有解密、没有真实调用、没有产生新实验结果；不得通过换入口绕过。已向用户列明目的地、数据范围、预算和具体计划请求授权。
