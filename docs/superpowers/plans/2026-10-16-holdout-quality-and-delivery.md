# 五条历史失败样本的质量与交付修复（第二阶段）

目标：修复可证伪的产品缺陷，随后在相同模型、20 步预算下用新轮次检验。第一阶段见 2026-10-15-holdout-execution-reliability.md；历史 003 不改写，不将跨版本成功拼成同轮官方成功率。

## 改动与边界

1. 在 TaskRequest / AgentContext / SWT run 配置增加带来源的 delivery。SWT 由固定 harness 指定 Linux，探测并披露实际执行平台。原版模型请求必须收到两者；普通 CLI 不新增必填参数。
2. 静态检查候选 skip/skipif 只在独立确认的标准收集上下文中支持负面证明。默认原仓库插件、conftest 和导入副作用未知，Controller 只记 target_unverified。不得把 Windows 本地通过冒充 Linux 验证，亦不得笼统拒绝所有平台条件。
3. 提取候选中的匹配片段、组合、顺序、helper 选择条件与候选自带文字重合，绑定断言、路径和行号。相同语义审查调用要求每项返回支持性、角色、依据和原文引用；程序校验覆盖、来源与实际发送的原文，拒绝负面或伪造引用。合法执行控制可由原版既有语义支持。该清单是可解释静态风险审查，不是语义真值证明；复杂动态行为标记 unknown。
4. 原版候选通过时返回 NOT_REPRODUCED，并带有限风险说明；不为这条路径增加模型请求。确保嵌套 pytester 的耗时抖动不再破坏重跑签名，而断言/预期模式内容不被归一化。
5. 最后一步发布被 grounded contract 拒绝，且有显式 missing_information 时，以既有 NEEDS_INFORMATION 收口；取消和硬时限优先。不得清空真实阻塞换取成功。
6. 8627 保持冻结官方结果；显式修复环境作为独立诊断。验证日志、脚本、补丁/预测、镜像、依赖档案和原轮次绑定后才挂接。包装命令先做整数 timeout 校验，调用既有 rejudge，再验证记录；不修改冻结 rejudge 本体以避免破坏旧 hash。

不改：金标测试/补丁、旧轮次、官方计分规则、预算上限、thinking 设置、探索强制分阶段机制。主工作区有其他代理改动，全部代码在 codex/holdout-execution-reliability 工作树完成。复用主工作区已有 attach/results 及两份测试，然后强化，原字节 hash 保存在 .local/holdout-five-20261015/reused-local-environment-work.json。

## 验收顺序

- 已观察 RED→GREEN：SDK Read/引用、重跑签名、原子 oracle 与协议、最后一步缺信息、receipt 伪造拒绝、CLI timeout 类型；默认未知收集和任意导入不得产生确定 skip 结论。
- 相关回归与全量离线回归，独立非 Astra 审查；审查不替代真实运行。
- 新五例轮次：8365 / 8627 / 8906 / 8474 / 5221。复用 003 冻结绑定，DeepSeek flash、thinking disabled、20 步 / 900 秒 / 命令 60 秒 / 32 KiB，不注入金标或手写候选。运行源文件 hash 保持冻结。
- 新 Linux/Docker 官方验收，固定 harness 330a649a；不删除共享 locks、不杀其他会话、不套用同步主分支的旧脚本。
- 8627 有候选且官方环境失败时，另行执行有来源的环境修复诊断；原官方 unresolved 仍保留。
- 输出逐例结果、成本、来源版本及剩余原因。无候选/信息不足/环境阻塞也照实记录，不预先承诺五例都能复现。

## 决策记录

- 静态 all-skip 的原提案被真实 pytest 收集钩子反例推翻；Controller 改为保守 advisory。代价是 8365 的跨平台能力仍必须靠真实 Linux 执行证明，不能靠静态审查宣布修好。
- 不给 8474 编造文档/config/预期；缺信息闭环属于诚实失败，不计成功。
- 旧 8627 诊断与当前旧轮次 hash 不符，拒绝挂接。新证据应绑定新轮次，不放宽校验兼容它。
