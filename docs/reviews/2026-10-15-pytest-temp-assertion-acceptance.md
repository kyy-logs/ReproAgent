# Pytest 8906 / 5221：实施与验收

日期：2026-10-15。基线 main c7c6a09，隔离分支 codex/pytest-temp-and-assertions。状态：针对性实现与离线验收完成；完整套件和独立审查待完成。没有合并或 push。

## 范围与计划更正

用户要求先处理 5221 和此前计划的 8906，8474 不在本轮范围。实现前复核发现，初版 8906 计划只检查 adapter/workspace，漏掉 Runner 的 Windows 临时目录替换。eafc536、194ab3e 已实现短的独占 ra-* 临时目录及有界清理，旧 pathlib/pytest 可能移除长路径前缀。因此撤回 adapter 加前缀方案，修正计划并保留原计划审计副本；本轮没有修改 Runner、adapter、paths、临时目录策略或其测试。

5221 已有通用的宽泛关键词警示，本轮补充中性 fixture 名称、标签和 docstring 的具体生成规则，保留 Issue 必需的名称和触发输入。契约已有充分依据时，提示尽早提交最小候选以取得反馈；这只是策略指导，没有新增固定探索阶段、步骤门槛或权限限制。

原版通过时，已有 CANDIDATE_NOT_REPRODUCED 诊断不变，反馈改为提醒检查断言是否被候选名称、说明或回显输入满足。它是排查建议，不声称程序已证明具体污染原因。选中的受控建议同时用于正常输出与容量备用输出，仍在插入前脱敏。显式调用者建议具有优先级；其他错误码仍使用原建议。

没有增加模型调用、语义重试、上下文 schema、学习输入、错误码、依赖、预算或完成条件。没有读取 gold 或修复版来写候选。

## 8906 原候选重放

源任务：E:/ReproAgent/repro-results/holdout-diag-002/cases/pytest-dev__pytest-8906/task。校验原快照全部文件与原候选清单/内容哈希后，使用当前 Workspace/Runner 新建运行。候选 testing/test_repro.py 的内容哈希保持 b941426477f7fc7b5960fc17aefed39ef3616df979d4b77ac71e35a64935e4be；新的快照和候选 ID 导致新 manifest 绑定，不宣称 manifest 哈希不变。

| 项目 | 实际结果 |
| --- | --- |
| 新输出 | E:/ReproAgent/repro-results/repair-8906-current-dd9317a6/cases/pytest-dev__pytest-8906/task |
| 原版 run | run-18b0ffd9412c4575b3cb43163cdbbffe |
| 解释器 | 原有 py39-pytest-7.0/Scripts/python.exe；Python 3.9.13、pytest 7.0，未解析替换解释器 |
| 执行 | EXITED，exit 1；1 failed、1 passed |
| 原临时文件错误 | 不再出现；pytester.makepyfile 和嵌套 pytest 已实际执行 |
| 当前失败 | 错误引导消息中未匹配到 allow_module_level，进入实际目标断言；不是临时目录 FileNotFoundError |
| 清理与完整性 | probe_complete / executed / cleanup_ok / protection_ok 均为 true；独占临时目录已清理 |

机器回执位于新输出 diagnostic-replay.json，包含源文件哈希、新绑定、实际临时路径、节点结果和输出尾部。该记录只证明旧环境障碍已被现有修复解除，没有调用语义模型，没有新 Agent 生成，没有修复版/差分或官方判分，不给任务写 DONE。

## 自动验收

| 检查 | 实际结果 |
| --- | --- |
| 现有私有临时目录 / 确认一致性 / 反馈基线 | 24 passed，14.99s，exit 0 |
| 新测试首次收集 | pywintypes 缺失；隔离 venv 的外部 .pth 未执行 pywin32 初始化，属于环境问题，不计为 RED |
| 修正隔离依赖加载后的 RED | 2 failed、2 passed，8.01s；缺正常 wire 的具体建议及低容量备用建议 |
| 实现后相关组合 | 30 passed，26.49s，exit 0；包含新增请求测试、容量/脱敏/显式覆盖、Controller 和既有 SDK 交付回归 |

新请求测试通过真实 AgentScope SDK 与 MockTransport：第一个脚本候选在原版实际通过，程序拒绝为 NOT_REPRODUCED，下一轮序列化请求带有对应绑定、program_check 诊断、具体建议和生成约束。探索恰好 2 次逻辑调用，只有原有契约模型请求，没有为原版通过新增语义诊断调用。脚本下一步 request_information 结束，不能据此声称真实模型会采纳建议。

## 未验证与并行边界

没有真实模型 5221 新轮次，也没有官方重判；不能报告复现率改善。8906 原候选已执行，但本轮没有完整 Bug 语义验收。不能保证首次候选不再拖到最后一步，硬预算和既有收敛机制没有变化。

另一个代理的评测挂接、上下文清单等工作区与 main 未提交文件不属于此分支，没有复制或修改。测试依赖通过 worktree-local .pth 复用，实际产品 import 来自本工作区；main 虚拟环境不变。

精确日志、原始计划备份及账本在本工作区 .superpowers/sdd/2026-10-15-pytest-8906-windows-temp-path/。完整回归和独立审查结论以追加记录为准。
