# 经验写入与情境检索验收

状态：实现、独立审查修复和最终完整回归完成。没有真实 provider 请求、没有解密密钥、没有 Astra 验收。

基线 da7ac47，隔离分支 codex/experience-triggers。设计和计划分别为 docs/superpowers/specs/2026-10-12-experience-trigger-retrieval-design.md 与 docs/superpowers/plans/2026-10-12-experience-trigger-retrieval.md。

## 已实现

- 学习模型保持五字段契约；提示词写明条件、事实、建议和不确定性。程序验证 EID/哈希后，从所引冻结受控事实提取 kind/tool/code，生成规范 tool:/error: 标签，保存可选 triggers。没有全程工具列表的泛匹配，没有从错误文本猜条件。
- 同一冻结库支持首轮问题召回和探索中实际事件召回。过程卡不能因 Issue 词碰巧重合而提前注入。原位库变更不影响当前视图。
- AgentScope 公共 on_acting/on_check_permission 及原有协议纠错路径通知真实事件。下一次已有模型请求之前补充有界 advisory UserMsg；不重跑工具、不增加模型请求或步数。
- 累计最多 3 条摘要，共享 2048 B（含唯一一次成功详情）；跨阶段不重置。容量不足卡、上下文未加入的卡不能读取；取消/结束阶段不注入。
- 新增 experience.triggered/retrieved/injected/omitted 事件及两项受控 trace 属性。学习同时保留小型受控错误事实，完整参数超限时仍可引用这种事实，但不假装完整经过已保留。

## 证据

| 验收 | 结果 |
| --- | --- |
| 基线 | 19 passed |
| 写入 RED | 5 failed / 1 passed，缺 triggers 和同情境去重错误已复现 |
| 写入 GREEN | 81 passed / 1 skipped，53.33s |
| 视图 RED / GREEN | 7 failed -> 13 passed |
| SDK 动态注入 RED | 4 failed / 2 passed，真实 SDK/mock HTTP 确认缺失路径 |
| SDK/工具/权限回归 | 66 passed，20.86s |
| 额外事实/trace/材料/采集检查 | 48 passed，33.15s |
| Viewer | 8 passed |
| 审查修复/旧库/SDK/launcher | 74 passed / 1 skipped，12.11s |
| wheel 安装与资源预检 | 3 passed，33.64s |
| 仓库外 SWT 对照/来源复核 | 17 passed，30.23s |
| 最终完整 pytest（f828a79，仓库外 basetemp） | 827 passed / 5 skipped，690.76s，exit 0 |

两项边界先 RED 再 GREEN：空白标签不能冒充保留检索命名空间；上下文 append 失败不能提前开放详情。trace 白名单缺失及小型受控事实保留也分别复现后修复。

## 独立审查与修复

GPT-6.1-sol 新上下文只读审查 da7ac47..9b4216d，发现三项 Important，均独立复现 RED 后修复：

1. 旧卡恰好 2048B 加空 triggers 后误关整库：空条件持久化不写可选字段，按 _card_record 的实际字节计量；原始 2048B 记录可读且追加后保持同形内容。
2. exploration.tool_error 的诊断 I/O 失败会丢 SDK 终态：隔离新增可选事件写入，原始工具响应保持交付，不重跑工具。
3. 大消息上限下摘要可绕过 SDK 压缩阈值：异步注入前用 SDK 本地 count_tokens（包含工具 schema）测量候选完整输入，超阈值省略历史建议；无额外 provider 调用。

审查还复现独立 ExperienceView API 的 pending-summary -> detail-read -> admission 超额。正常 SDK 原路径没有这种交错；本轮把它作为类的预算不变量修复，详情计入待注入摘要，admission 再核对总量。相关测试分别先 RED 后 GREEN。

审查未判断真实复现收益（留待授权 A/B）、旧 reader 接收新卡（不承诺反向兼容）；两项边界明确保留。没有再次派发审查，按要求用覆盖测试和最终完整回归确认修复。

初次完整验收在源修复前停止；其中六项 launcher 失败是新工作区缺 .venv，已建立私有 Python 并复用既有本地依赖，无下载。后续 launcher 和 wheel 预检通过，不将环境缺失计为产品修复。

修复后首次完整运行因把 basetemp 放在 E:/ReproAgent 内，得到 8 failed / 819 passed / 5 skipped（767.82s）。七项 SWT 对照临时项目继承外层 pytest 配置，节点 ID 多出仓库路径前缀；一项来源测试继承外层 Git。实际 buggy/fixed 调用结果正确，不修改产品来适配测试目录。改用 C:/Users/Dell/AppData/Local/Temp/reproagent-experience-triggers-verification/swt 后，相关 17 项全部通过。完整回归使用该仓库外目录下独立 full 子目录；原失败记录保留，不当作通过证据。

真实 SDK 离线闭环：实际 revise_contract 失败 -> ToolResponse metadata -> capture_exploration -> 冻结证据 EID -> 五字段提案程序校验 -> 追加经验库 -> 新任务加载同卡 -> 对应真实拒绝 -> 下一 wire 请求含摘要。前一请求没有过程摘要，原始 tool result 完整保留；库字节不变。

## 限制和后续评测

旧卡按原关键词方式可读，未凭空补造 triggers；新版 reader 能读旧卡，旧程序不保证能读新增元数据。历史 ab2 那张卡没有结构化错误 metadata，不能声称它已经因本改动自动变成可触发卡。真实新学习要使用本分支代码生成新库。

程序验证了触发条件的事实来源，没有验证自然语言建议的全部语义或因果效果。支持的受控错误是有限集合；未覆盖的错误继续使用既有路径。零命中和容量不足在观测中有原因。

A/B 必须保持相同工具表、模型/环境/预算及冻结库。过程卡允许首轮零匹配，在对应事件后核对实际 wire 注入；整个任务没有注入时不宣称卡片效果。不要继续使用只在首轮 zero_natural_matches 就停止的旧运行器验证过程卡。每臂两次仅小样本，不能证明稳定收益或泛化。

完整验证日志位于 E:/ReproAgent/.local/experience-triggers/；最终日志 full-outside.log，退出码 full-outside.exit=0。计划执行账本和审查包备份在该目录的 execution-ledger.md、review-da7ac47..9b4216d.diff。当前分支 codex/experience-triggers 保留，未 push 或合并到 main。

收尾决策：用户已要求直接执行，本计划明确保留分支且不 push/合并，因此按该已定范围完成，没有再次要求选择集成方式。测试代码及运行源码在最终完整回归期间保持 f828a79；后续仅更新计划和验收文档。没有遗留 Minor 修复项；真实模型收益和旧 reader 读取新卡仍属于上述边界。
