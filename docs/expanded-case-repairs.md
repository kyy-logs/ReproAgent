# 扩展案例修复与复测（2026-10-05）

本次修复了扩展验证中发现的流程问题，并使用**同一批 20 个历史 Bug**重新进行两轮诊断验证。
最终这一整轮：**20/20 完成重复复现、修复版验证和独立导出重跑**；Controller DONE 为 20/20。
离线全套 **147 passed、1 skipped（98.19 秒）**，pip check 无依赖冲突。独立人类正确性评审仍未进行。

这是选定的、提前准备可运行环境的库级案例，输入为研究者编写的行为摘要及最小触发输入。
上述完成数不能当作普通项目或任意 Issue 的复现率；没有自动修环境、浏览器或服务 Bug 的证据。

## 修了哪些问题

1. **重复运行被对象地址误伤。** pytest 的失败消息会截断对象 repr，并含不同进程的内存地址。
   Probe 现在记录真实比较操作、完整左右值、异常类型和位置；仅对已经记录到的实际对象 id 做地址归一化。
   字符串里的数字、价格、业务值、未知地址及截断旧日志保持严格比较，不猜测或删除。
   仍要求同一冻结候选、契约、源码、完整测试节点、目标来源和两次 REPRODUCED；原始日志保留。
   packaging-1360 的新运行验证了完整流程，而原始失败回执没有改成成功。
2. **只读源码，来不及生成和运行。** 动作请求提供剩余预算；预期足够且临近动作上限时，禁止继续读/搜索，
   预留生成、运行、提交和一次纠正的空间。没有候选时不允许运行或提交。每例仍只有 12 个动作。
3. **动作协议错误和测试目录反复出错。** JSON 必须是一个符合匹配动作参数 schema 的对象。
   多个 JSON、供应商工具标记、根数组、非字符串 name、错误目录均拒绝，最多纠正两次。
   每次仍计步、计用量，共用时间和取消；不会提取歧义输出的首个动作执行。网关配置错误不伪装成模型格式错误。
   提示和动态 schema 明确当前 candidate_parent，支持配置的嵌套测试目录。
4. **目标自身生成代码的 SyntaxError 被误判。** 改查真实异常类型与最内层 traceback 来源。
   只有实际目标模块在 call 阶段抛出的生成代码语法错误，才可以进入语义核验；
   候选本身编译错误、collection/fixture 错误仍拒绝。attrs-1319 的本轮生成测试实际触发并验证了此路径。
5. **预期被模型改写，以及辅助断言写错。** 第一修复轮 validators-432/374 将返回 ValidationError 错写成抛出异常。
   分析校验成功后保存原始描述、来源引用和正文，生成动作同时获得原始事实与契约。
   提示明确区分返回与抛出，语义核验检查全部辅助断言及契约与来源的矛盾；修订仍必须有新的原始证据。
   非法修订不覆盖已验证来源。取消和预算限制继续生效，没有向 Agent 提供修复版信息。

这不是逐个仓库的特例修补，也没有修改 Agent 生成的测试或目标业务源码。
提示能降低语义漂移，但不能保证模型一定正确；引用约束、真实执行、独立重复、修复版对照与人的审查仍各有作用。

## 保留了哪些轮次

| 轮次 | 输入范围 | Controller DONE | 完整流程与独立导出重跑 |
|---|---|---:|---:|
| 扩展前记录 | 旧三例修复后最终轮 + 新十七例首次运行 | 11/20 | 11/20 |
| 第一修复轮 | 全部 20 例各一次 | 20/20 | 18/20 |
| 原始来源与语义提示修复后的最终轮 | 全部 20 例各一次 | 20/20 | 20/20 |

第一修复轮没有被覆盖：validators-432/374 虽然 DONE，但仅 REPEATED_OBSERVATION，修复版仍失败。
两者在全新副本中的导出重跑也失败，因此只统计 18 个完整结果。最终轮不是跨轮拼接的最好结果。
最早三例 0/3 与其他中间失败轮也继续保留。

## 比较条件与证据

- 原版/修复版提交、描述字节哈希、目标解释器、source_roots、candidate_parent、全部有效 limits 和模型配置保持一致。
  旧三例回执只写了三项显式预算，比较时补齐同样的 BudgetLimits 默认值；未增加预算。
- DeepSeek deepseek-flash，max_tokens=4096；每例 12 动作、任务 240 秒、单命令 30 秒，两个案例并发。
- 每轮调用模型前记录源码、三个提示文件和基线 SHA256，最终轮结束后校验冻结内容未改变。
  两轮使用不同结果目录；最终轮没有删除失败、扩大预算或挑选单例重试。
- 研究者对照测试和修复材料继续排除在生成输入之外；目标库依赖在原始评估前准备，没有在本次自动修复环境。
- 每个 DONE 的导出包在全新原版和修复版副本另跑。
  检查退出码 1/0、实际 call 失败/通过、完整 Probe、相同完整测试节点、来源绑定及目标调用。
  导出清单各文件哈希重新校验，逐例证据哈希记录在回执。
- Codex 已阅读本轮生成测试并对照输入；这不是独立人类评审。
  human_judgement 留空，effective_reproductions 为 null。

## 逐例结果

完整表示 DONE + DIFFERENTIAL_VALIDATED + 独立导出原版失败/修复版通过。

| 案例 | 原始比较记录 | 第一修复轮 | 最终整轮状态 / 证据 | 最终导出重跑 | 动作 | HTTP 尝试 |
|---|---|---|---|---|---:|---:|
| [validators-432](https://github.com/python-validators/validators/pull/432) | EXHAUSTED / SINGLE_OBSERVATION | REPEATED_OBSERVATION | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 7 | 11 |
| [validators-418](https://github.com/python-validators/validators/pull/418) | DONE / DIFFERENTIAL_VALIDATED | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 4 | 7 |
| [validators-405](https://github.com/python-validators/validators/issues/403) | DONE / DIFFERENTIAL_VALIDATED | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 5 | 8 |
| [validators-374](https://github.com/python-validators/validators/issues/373) | DONE / DIFFERENTIAL_VALIDATED | REPEATED_OBSERVATION | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 8 | 11 |
| [validators-317](https://github.com/python-validators/validators/issues/316) | DONE / DIFFERENTIAL_VALIDATED | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 3 | 6 |
| [pyjwt-608](https://github.com/jpadilla/pyjwt/issues/599) | DONE / DIFFERENTIAL_VALIDATED | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 4 | 7 |
| [pyjwt-1040](https://github.com/jpadilla/pyjwt/issues/1039) | EXHAUSTED / NONE | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 5 | 8 |
| [attrs-1428](https://github.com/python-attrs/attrs/issues/1427) | EXHAUSTED / NONE | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 5 | 8 |
| [attrs-1328](https://github.com/python-attrs/attrs/issues/1327) | EXHAUSTED / NONE | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 5 | 8 |
| [attrs-1319](https://github.com/python-attrs/attrs/issues/1284) | EXHAUSTED / NONE | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 3 | 6 |
| [click-3152](https://github.com/pallets/click/issues/3084) | DONE / DIFFERENTIAL_VALIDATED | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 3 | 6 |
| [click-3079](https://github.com/pallets/click/issues/3071) | EXHAUSTED / NONE | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 3 | 7 |
| [click-3004](https://github.com/pallets/click/pull/3004) | EXHAUSTED / NONE | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 4 | 7 |
| [click-2940](https://github.com/pallets/click/issues/2939) | EXHAUSTED / NONE | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 3 | 6 |
| [packaging-1360](https://github.com/pypa/packaging/pull/1360) | EXHAUSTED / SINGLE_OBSERVATION | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 5 | 8 |
| [packaging-1332](https://github.com/pypa/packaging/pull/1332) | DONE / DIFFERENTIAL_VALIDATED | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 4 | 7 |
| [packaging-1328](https://github.com/pypa/packaging/pull/1328) | DONE / DIFFERENTIAL_VALIDATED | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 5 | 8 |
| [more-itertools-645](https://github.com/more-itertools/more-itertools/issues/462) | DONE / DIFFERENTIAL_VALIDATED | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 5 | 8 |
| [humanize-250](https://github.com/python-humanize/humanize/pull/250) | DONE / DIFFERENTIAL_VALIDATED | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 3 | 6 |
| [humanize-254](https://github.com/python-humanize/humanize/issues/14) | DONE / DIFFERENTIAL_VALIDATED | 完整 | DONE / DIFFERENTIAL_VALIDATED | 原版失败、修复版通过 | 4 | 7 |

## 验证与用量

新增回归覆盖实际 pytest 比较、真实对象地址、字符串/业务数字不归一化、异常来源、读取预算、动作 schema、
无效修订与原始事实保留等。关键新增测试先观察失败，再通过实现修复；全套包括真实进程清理和 wheel 安装验证。
跳过项是当前 Windows 账户的符号链接权限限制，普通路径越界检查已运行。
子智能体只读审查发现的字符串误归一化、伪 repr 业务值、非字符串动作名、Probe 比较状态泄漏均已修复并加负例。
最终增量审查没有 Critical/Important。

第一修复轮 151 次 HTTP 尝试、675,026 token。
最终轮 **150 次 HTTP 尝试、710,145 token**（输入 607,341、输出 102,804），
88 个动作，JSON 格式错误返回 2 次。错误仍计入预算和回执。
这两轮额外用量合计 301 次 HTTP 尝试、1,385,171 token，未含此前轮次。
没有配置供应商费率，费用保持 unknown/null，不能由 token 数断言实际金额。

## 仍需优化

模型语义判断仍可能写错测试；严格对象身份记录对未知容器和过大 repr 采用保守回退，可能有假阴性。
最终轮 validators-432 的契约仍出现“raises”措辞，生成测试却已按原始来源正确检查返回值，修复版和独立导出验证均通过。
语义核验注意到了这个措辞冲突但仍接受了正确的测试，因此本次结果证明了生成测试改善，不能宣称契约漂移已被完全消除。
将返回值与异常机制设计为可校验的结构字段，是后续优化方向。
此次小样本无法归因每个修复各贡献多少，也没有通用编码 Agent 对照、代表性新样本或跨平台 CI 实跑结果。
下一步应先做未见过的案例与独立人类审查，再考虑自动环境诊断、依赖/服务修复、范围缩小和容器隔离。

- [最终整轮结果与哈希](../evals/cases/expanded-final-results.json)
- [保留的第一修复轮 18/20](../evals/cases/expanded-repaired-results.json)
- [本轮人工审查表](../evals/cases/expanded-final-review.csv)
- [原始扩展报告及失败记录](expanded-case-validation.md)
- [旧三例原始 0/3](historical-case-validation.md)及[后续修复](historical-case-fixes.md)
