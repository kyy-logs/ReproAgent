# Setup失败反馈与首次试跑预算修复

用户已确认：写计划后直接执行，当前会话逐项实现。工作树 codex/holdout-execution-reliability，基线a5186a5；不修改主目录另一个会话的文件。

## 目标与事实

真实8474在第20步才发布仅含test_repro.py的候选，引用但未提交测试资料目录；3个setup失败、0个call，原反馈只有no executed tests，随后预算耗尽。目标是把真实setup原因送回模型，并在首次试跑后保留修正空间；不以手写候选或修改金标换分。

## 设计

1. 独立core/setup_diagnostics.py从完整run.observation提取when=setup/outcome=failed的记录。只有通过现有完整性/框架检查，且未执行测试主体时，Verifier在原tests_executed_and_valid门禁返回TEST_SETUP_FAILED。分类仍INVALID_CANDIDATE，origin=program_check，recovery=inspect_evidence。不把所有FileNotFoundError认定为候选漏文件，不推断不存在的根因。
2. measurements携带setup_failures，每项恰好nodeid/exception_type/message三个字符串，最多4项、字段最多512字符，omitted_setup_failures计数。先脱敏、规范化运行路径，再节选；完整失败引用保持可查。无有效setup记录时保留旧TEST_EXECUTION_INVALID，import、来源保护、超时、cleanup门禁顺序不变。
3. Controller Lifecycle累计original_execution_count；observe_execution仅在original执行返回后递增，包括setup失败。独立重跑计入，fixed与prepare不计；契约修订不清零。AgentContext追加默认0字段，经runtime消息与每轮program progress传递。模型和history不能修改该计数。
4. execution_budget_policy(total,used,original_execution_count)是共享纯函数。首次执行前reader_reserve=min(6,ceil(total*0.3))；执行后reserve=min(2,ceil(total*0.1))。权限使用当前已消耗的steps；program progress明确下一次模型调用消耗一步后读工具是否可用。20步首次reserve=6：第14步后不再广读，为修订/发布保留空间，目标15–16步首次试跑；之后reserve=2：允许余3步时读取，并保留余2步重新发布。
5. 提前收敛提醒在首次执行前remaining<=reserve+2时出现；执行后沿用原75%软提醒，并明确正在修正已有执行。原soft_threshold、总预算、deadline/cost/cancel、工具允许集/SDK deny规则不变。真实缺信息不可强制发布。保留RESERVED_FOR_PUBLISHING兼容码，progress增加policy事实，不扩展经验索引。
6. 首次试跑复用现有Runner，不新增Sphinx专属扫描、预检模型或额外执行。进度消息保持1024 UTF8字节完整JSON；字段/指导语超限时按现有原子省略规则处理。

## 验收

- 缺资料setup→具体exception/message/受核验引用；无call时不调用语义模型；恶意文本、秘密、长路径和超限明确处理。
- 20步初始/修正边界、1/2/3/6步小预算、deny优先、阶段计数持久、取消/硬限仍优先。
- 真实AgentScope SDK+MockTransport+本地pytest：漏资料的首候选在预算内执行，下一次实际HTTP接收setup错误，允许必要Read，发布补全data文件的候选，完成原版独立重跑；原始源码hash不变、步骤<=20。脚本化模型仅验证执行链，不代表真实模型能力。
- 离线相关回归与一次完整验收；修复完成后在同冻结8474/20步/DeepSeek/thinking关闭下新轮次实跑。未通过如实记录，不循环烧token直到成功。

## 边界

这是预算预留策略的初始参数，不能保证第16步前必有可运行候选；复杂缺信息问题仍可能无法复现。通用静态扫描不能证明所有动态fixture依赖完整，因此真实setup作为最小完整性检查。程序只传执行事实，不把模型收到反馈等同采纳反馈。
