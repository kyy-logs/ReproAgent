# Verifier 失败反馈：实施与验收记录

> **历史归档（2026-10-24）**：保存当时的设计、运行结果或验收依据，不能据此判断当前默认行为、最新测试数量或总体复现率。
> 当前入口见[文档导航](../../README.md)，旧路径对照见[归档索引](../README.md)。

开始日期：2026-10-14；整合日期：2026-10-15。初始基线 `804ac7b`，实施中合入并行 main `2e1f967`。独立分支 `codex/verifier-failure-feedback`；工作区 `C:/Users/Dell/.codex/worktrees/verifier-failure-feedback/ReproAgent`。

状态：产品实现、独立审查修复和最终完整离线回归已完成；已快进合入本地 main（09fcebb），合并后关键回归通过，未 push。以下只记录实际结果。

## 最终行为

- Verdict 增加默认空元组的 typed issues。每项包含受控 code/check/origin、说明、已验证引用、受控测量和恢复建议。旧记录缺少字段仍能读取；不承诺旧严格读者能读新字段。
- Verifier 程序拒绝、合法语义未确认、协议错误和并行新增的断言审查拒绝都有具体诊断。程序事实与 provider_claim 分开；false 不被写成已证明的否定。
- 失败引用再次核验，不可用或已变化引用不能留在 issue 或顶层失败证据里。成功门禁、候选绑定、保护、严格引用、独立重跑和可选修复版隔离保留。
- 程序校验字段和依据后形成 Verdict，Controller 保持现有继续/停止优先级。恢复建议不授予新权限、不增加预算、不自动重试。
- 候选验收反馈在 min(8192, tool_response_bytes) 的 UTF-8 预算内生成完整 JSON，保留主诊断、失败检查、身份与建议；截短或省略均带计数，完整 Verdict 不变。继续分支最小信封放不下时 NEEDS_INFORMATION；已终止分支无下一请求，记录可选反馈不可用但保留真实停止原因。
- 低容量最小信封可移除与原绑定相同的 previous_* 重复身份，并用完整受控建议替换冗长自定义措辞；不能截掉动作语义或必要量值。
- Controller 保存 Verdict 后记录 verification.feedback 的受控结果、决定、诊断码、绑定与字节/省略统计。trace 属性白名单只增加所需字段，报告现有证据段展示程序检查或模型审阅意见和恢复建议。
- 真实 SDK + MockTransport 验证完整反馈进入下一轮实际序列化请求。语义审阅仍为独立 system/user 上下文，不继承探索历史。trace.json 经产品 writer 写出后验证新字段存在，现有 viewer 原属性详情入口可展示。

## 实际验证

测试解释器为独立 worktree `.venv/Scripts/python.exe`，只复用 E 主目录已安装依赖，实际 import 已核对为 C 工作区源码；未修改主目录虚拟环境。所有 basetemp 均在仓库外。

| 检查 | 实际结果 |
| --- | --- |
| 改动前基线：Verifier / execution recovery / reporting | 42 passed，65.26s，exit 0 |
| 类型 RED | 12 failed：类型缺失 |
| 类型 GREEN + 既有 execution diagnostics | 23 passed，4.86s，exit 0 |
| Verifier 分支 RED | 22 failed、1 passed：诊断为空，成功无失败 issue 已符合 |
| Verifier 首次 GREEN | 63 passed、1 个新测试错误断言；EXITED/cleanup 成功的 probe-only 场景实际完成了保护检查 |
| Verifier 修正测试并补边界后的 GREEN | 66 passed，110.91s，exit 0 |
| 反馈投影 RED | 12 failed：renderer 缺失 |
| 反馈投影 GREEN + 类型 | 24 passed，1.42s，exit 0 |
| Controller RED | 5 failed：新反馈与决定事件未接入 |
| Controller 首次组合 | 47 passed、1 failed：重复候选反馈最小信封 1252 B 超过 1024 B |
| 低容量问题独立 RED | 1 failed，测得同样 1252 / 1024 B |
| trace/report RED | 2 failed、1 passed：白名单丢字段、报告缺展示；下一轮 wire 已收到诊断，外层容量拒绝已通过 |
| 低容量与 trace/report GREEN | 17 passed，7.98s，exit 0 |
| Controller / SDK / trace / report / browse / convergence 组合 | 116 passed，133.41s，exit 0 |
| 并行 main 新分支诊断 RED | 2 failed：断言审查、歧义 JSON 返回尚无 issue |
| 整合断言审查 / 协议 / SDK / Controller GREEN | 75 passed，89.97s，exit 0 |
| 顶层失效引用 RED | 1 failed：issue 已去除变化引用，顶层仍保留 |
| 修复顶层失效引用后组合 | 32 passed，29.81s，exit 0 |

首轮全量在 25% 时为审查修复中止；第二轮日志停在 44%，会话中断，未取得完整结果。这两轮均不计为通过。第二轮暴露原端到端测试失败：新增 verification.issue 带 check 却缺少 result_code，破坏原 Trace 消费约定；并且程序拒绝模型矛盾声明时，决定来源应为 program，底层未确认意见仍为 provider_claim。独立小测试先复现 2 failed，再补齐 FAIL 并恢复程序决定来源，未放宽验收条件。相关 Verifier / 端到端观测 / 实际 SDK 交付组合 **40 passed，105.63s，exit 0**。

最终完整全量结果：**1138 passed, 6 skipped in 967.94s (0:16:07)，exit 0**。日志 complete-full-suite.log；没有将部分进度当作通过。命令：

```powershell
.venv/Scripts/python.exe -u -m pytest -q --basetemp C:/Users/Dell/AppData/Local/Temp/reproagent-vf-complete-20261015
```

精确命令、输出及执行账本已逐文件校验并保留到主目录 `.local/verifier-failure-feedback-audit-20261015/`；随后仅清理本任务的 managed worktree，不处理其他计划或工作区。

## 并行开发与裁决

1. Windows 没有 Git Bash，使用等效 PowerShell/Python 日志、任务记录和账本；风险是记录语义漂移，以实际命令、退出码和提交核对。
2. 计划提到的独立 test_serialization.py 不存在，使用 typed issue 新旧记录往返测试与现有 execution diagnostic 旧记录测试覆盖；风险是遗漏读取边界，由完整套件补查。
3. 新 probe-only fixture 保留 EXITED 与 cleanup 成功，protection_checked 应为 True，修正新断言而不改程序事实；真实超时仍应为 False，由既有 Runner 回归覆盖。
4. 已终止状态不再发送下一模型请求；可选反馈装不下不能把真实环境阻塞改写为容量问题。继续路径仍明确停止；风险是误丢必要输入，以两个路径的容量测试区分。
5. 并行 main 添加 assertion_reviews 和终止守卫。本分支沿用最新七字段 schema、逐条断言核验、歧义 JSON 不纠错和模型返回后的预算/取消检查，给新返回补诊断。风险是冲突漏保留，已用 main 原断言审查测试和新诊断测试同时验证。

## 独立审查

按 executing-plans / requesting-code-review，使用一个新上下文的非 Astra 审查代理；只读审查范围 `2e1f967..1bd80a0`。审查完成，无 Critical 和 Minor，两项 Important：

1. Runtime 将反馈作为已编码字符串再次脱敏，注册值包含反斜杠时破坏嵌套 JSON 转义；改为解析反馈对象、按值脱敏再编码，保留纯文本路径和最终阶段容量检查。
2. 容量不足时插入未脱敏的受控备用建议，可能重新暴露注册值；备用建议先脱敏，再比较最终字节并插入。

修复前独立复现 **2 failed、1 passed**，含旧纯文本兼容对照；统一修复后 renderer/types/实际 SDK/首次浏览/预算收敛/审阅边界组合 **98 passed，50.71s，exit 0**。没有再次派审查代理，按 RED→GREEN 和最终全量完成同一修复轮。

审查代理另行只读运行 25 项 renderer/type、3 项 SDK/report 和 34 项拒绝/Controller 检查通过；未启动另一套全量。初始临时目录权限受限后使用批准的隔离临时目录重跑通过。

Declined to judge 的裁决：真实模型修复收益没有授权也没有证据，保持未验证；自动依赖安装/权限扩张/新重试策略及 viewer 布局改造明确不属于范围；全量结果只采用实施者实际最终运行。此取舍的风险是不能据本次离线机制验收断言成功率改善或浏览器视觉效果。

## 证据边界

此次没有访问真实模型接口、读取用户密钥或运行收费 A/B。MockTransport 脚本回答是协议/流程夹具，不代表真实模型语义判断正确；通过 wire 证明反馈已交付，不证明模型会采用建议或提升复现率。

没有新增依赖、工具、探索阶段限制、经验写入/检索、人工验收、trace viewer 布局、预算阈值或完成判断模型。没有重新验收 HTML 的视觉交互；只验证 trace 结构与既有属性详情的数据路径。

主目录未跟踪评测 HTML 和已修改 P12 记录保留原位。本分支在另一代理期间独立实施；合并前已再次核对 main 提交和两份本地文件的哈希，没有覆盖另一个会话的未提交文件。

最终回归补充：Trace 的 FAIL 表示该项未满足接受条件，不表示程序证明模型语义为假；check_source 仍保留 provider_claim。旧端到端消费约定通过原测试验证，而非修改消费者跳过新事件。

## 本地集成验收（2026-10-15）

main 从 2e1f967 快进到 09fcebb；src/tests 与完整回归树一致，实际 import 来自 E:/ReproAgent/src。合并后关键链路再次验证 **22 passed in 44.11s，exit 0**，覆盖 JSON 容量、SDK 反馈交付与脱敏、check 事件字段、程序拒绝矛盾成功声明。没有重复运行相同代码的全部套件。

原有评测 HTML 的 SHA-256 保持不变；原有 P12 本地修改仅补充本轮实施状态并保留为未提交。未自动提交这些原有本地文件。没有 push、真实模型运行或费用。
