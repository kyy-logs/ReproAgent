# 历史案例扩展验证（2026-10-05）

新增 **17 个历史 Bug、5 个仓库**：8 个完成 DONE / DIFFERENTIAL_VALIDATED，并且导出测试在新的原版副本失败、新的修复版副本通过；9 个 EXHAUSTED。
加上上一轮已完成的 3 个案例，累计 **20 个不同 Bug、7 个仓库，11 个完成完整自动流程**。
这是经过人工选题和环境准备的诊断样本；11/20 是累计流程完成数，不能当作一般复现率。独立人类正确性评审尚未进行。

## 运行口径与信息隔离

- 新增案例在调用模型前选定并冻结；使用官方 GitHub PR/Issue 和精确提交归档。原版为修复 merge commit 的第一父提交，修复版为 merge commit。
- 输入是研究者编写的行为摘要及最小触发输入，**不是原始 Issue 原封不动输入**；公开描述、来源正文哈希、实际输入字节哈希、提交与归档哈希见 [案例定义](../evals/cases/expanded-historical.json)。
- 原版目录不包含研究者对照测试、修复补丁或修复版新增回归测试。Agent 仅接收描述、原版源码、目标模块、源码根与测试目录；修复版在候选冻结并重放后才交给 Controller 验证。
- 17 个研究者对照测试全部在原版失败、修复版通过。对照测试证明准备后的环境及选题可复现，**不算 Agent 成功**，也未放进 Agent 输入。
- Windows、Python 3.12.14、pytest 9.1.1；目标使用独立环境，无 ReproAgent/httpx。attrs 根 conftest 需要 hypothesis，已在评估前安装；包版本完整记录在 [运行回执](../evals/cases/expanded-historical-results.json) 的 evaluation_lock。
- 新案例统一 source_roots 为 src（PyJWT 为 .），candidate_parent 为 repro_tests，保留原项目根 conftest。验证的是已准备环境中的库级 Bug，未覆盖服务、数据库、浏览器和无法启动的项目。
- DeepSeek deepseek-flash，max_tokens=4096；每例 12 个动作、任务 240 秒、单命令 30 秒。两个案例并发，单案例保持顺序执行，时间不适合直接和旧串行运行比较。
- 产品代码及三个提示文件哈希与前一轮成功版本一致。新增 17 例均首次运行，未修改提示、增预算或重试挑选最好结果。模型输出错误照常消耗预算并保留。
- 旧 3 例采用修复后的最终轮，原始及中间失败轮仍保留；所以累计 11/20 **不是 20 个首次尝试的成绩**。同一案例多次历史运行不会重复计入独立案例数。

## 按仓库统计（仅新增批次）

| 仓库 | 案例数 | 完成完整流程 |
|---|---:|---:|
| jpadilla/pyjwt | 2 | 1 |
| pallets/click | 4 | 1 |
| pypa/packaging | 3 | 2 |
| python-attrs/attrs | 3 | 0 |
| python-validators/validators | 5 | 4 |

## 每个案例

DIFFERENTIAL 表示 DIFFERENTIAL_VALIDATED；所有 8 个 DONE 都另跑了导出包，而非仅核对 Controller 的状态。

| 案例与公开来源 | 问题 | 最终状态 / 证据 | 动作 | HTTP 尝试 | 独立导出结果或未完成原因 |
|---|---|---|---:|---:|---|
| [validators-432](https://github.com/python-validators/validators/pull/432) | 混用 MAC 分隔符仍被接受 | EXHAUSTED / SINGLE_OBSERVATION | 12 | 16 | 第 12 步才运行；一次失败已通过语义核验，未提交独立重放。 |
| [validators-418](https://github.com/python-validators/validators/pull/418) | mailto 前缀移除误删用户名 | DONE / DIFFERENTIAL | 7 | 10 | 原版失败、修复版通过 |
| [validators-405](https://github.com/python-validators/validators/issues/403) | 合法 URL fragment 被拒绝 | DONE / DIFFERENTIAL | 8 | 11 | 原版失败、修复版通过 |
| [validators-374](https://github.com/python-validators/validators/issues/373) | private=False 错拒公共 IPv4 | DONE / DIFFERENTIAL | 9 | 13 | 原版失败、修复版通过 |
| [validators-317](https://github.com/python-validators/validators/issues/316) | 百分号编码 fragment 被拒绝 | DONE / DIFFERENTIAL | 11 | 14 | 原版失败、修复版通过 |
| [pyjwt-608](https://github.com/jpadilla/pyjwt/issues/599) | 显式 verify_exp=True 仍跳过过期校验 | DONE / DIFFERENTIAL | 6 | 9 | 原版失败、修复版通过 |
| [pyjwt-1040](https://github.com/jpadilla/pyjwt/issues/1039) | 非字符串 iss 编码未拒绝 | EXHAUSTED / NONE | 12 | 13 | 10 次读取/搜索、2 次格式错误；未生成候选。 |
| [attrs-1428](https://github.com/python-attrs/attrs/issues/1427) | pre_init 收到默认值而非传入值 | EXHAUSTED / NONE | 12 | 13 | 11 次读取/搜索、1 次格式错误；未生成候选。 |
| [attrs-1328](https://github.com/python-attrs/attrs/issues/1327) | 列表 converter 在赋值时出错 | EXHAUSTED / NONE | 12 | 13 | 11 次读取/搜索、1 次格式错误；未生成候选。 |
| [attrs-1319](https://github.com/python-attrs/attrs/issues/1284) | 生成的初始化代码出现 SyntaxError | EXHAUSTED / NONE | 12 | 13 | 11 次读取/搜索、1 次格式错误；未生成候选。 |
| [click-3152](https://github.com/pallets/click/issues/3084) | 可选参数缺值时未使用 flag_value | DONE / DIFFERENTIAL | 8 | 11 | 原版失败、修复版通过 |
| [click-3079](https://github.com/pallets/click/issues/3071) | 共享 flag 的默认值选错 | EXHAUSTED / NONE | 12 | 13 | 9 次读取/搜索、3 次格式错误；未生成候选。 |
| [click-3004](https://github.com/pallets/click/pull/3004) | Enum 默认值 help 显示错误 | EXHAUSTED / NONE | 12 | 13 | 4 次格式错误；第 12 步才写入候选，未执行。 |
| [click-2940](https://github.com/pallets/click/issues/2939) | stdin 行迭代错误中止 | EXHAUSTED / NONE | 12 | 13 | 10 次读取/搜索、2 次格式错误；未生成候选。 |
| [packaging-1360](https://github.com/pypa/packaging/pull/1360) | 显式空 platforms 被替换为宿主平台 | EXHAUSTED / SINGLE_OBSERVATION | 12 | 15 | 两次执行及语义核验均确认问题；对象 repr 内存地址变化使 confirm 拒绝重复失败，随后动作预算耗尽。 |
| [packaging-1332](https://github.com/pypa/packaging/pull/1332) | 公开 API 泄漏 InvalidSpecifier | DONE / DIFFERENTIAL | 9 | 12 | 原版失败、修复版通过 |
| [packaging-1328](https://github.com/pypa/packaging/pull/1328) | InvalidMetadata pickle 重建失败 | DONE / DIFFERENTIAL | 11 | 14 | 原版失败、修复版通过 |

Click 3004 的预期来自维护者合并的 Enum 默认值 help 回归；其关联 Issue 2911 讨论的是另一种 Choice.value 行为，两者没有混作同一需求。研究者在运行前已更正来源为 PR 3004。

## 成功证据与审查边界

8 个成功候选均真实调用目标源码，无 mock 替代；同一冻结测试在两个独立原版工作区失败，并在修复版通过。导出包又安装到全新的原版和修复版副本：原版退出 1、有实际 call 阶段失败；修复版退出 0、相同完整节点通过。每次都有完整 Probe、源码来源绑定和目标调用记录。

Codex 已阅读这 8 个生成测试，主断言均表达正确行为而非“接受原 Bug”。validators 405/317 有重复输入断言，374 的辅助对照可简化；未改动生成测试或抬高其分数。逐例测试审查记录在回执 codex_correctness_review，候选、运行、判定、控制日志与导出清单的 SHA256 也已记录。

尚无独立人类评审，因此 human_judgement 留空、effective_reproductions 为 null。费用未配置费率，cost 为 unknown/null，不能据 token 数声称真实花费。

## 未完成的 9 例暴露了什么

**6 例没生成候选**：attrs 三例、PyJWT 1040、Click 2940/3079。在可运行环境且 missing_information 为空的前提下，模型仍反复读源码/搜索，用完预算。PyJWT 1040 的宽泛 iss 搜索还命中了无关 GitHub 权限文件。应优先验证“预留生成/运行/提交动作、尽早写最小测试”的策略是否有效；当前结果未做这种策略干预。

**1 例生成但没运行**：Click 3004 直到第 12 步才写候选，其间有 4 次动作格式错误。不能把“生成了测试文件”当作完成复现。

**1 例只有一次观察**：validators 432 第 12 步才运行，之后没有提交/重放预算。契约将返回 ValidationError 表述为抛出，存在措辞偏移；实际候选同时检查返回/抛出及显式 r_ve=True，第一次真实失败已经验收，但没有修复版和独立导出证据，因此仍不计成功。

**1 例被重复确认拒绝**：packaging 1360 的三项测试两次都失败，两次语义核验都为 REPRODUCED，实际节点与源绑定也一致；但 Tag.__repr__ 含对象内存地址，pytest 的 crash.message 两次不同。当前 Verifier.confirm 对失败消息精确比较，判定独立重放未确认，下一动作又触及预算上限。最终只保留 SINGLE_OBSERVATION，导出诊断包，未进入修复版验证。

该例确认比较诊断：production_signatures_equal=false；仅在研究者分析中去掉 repr 地址及其截断尾数后，两组签名相同。诊断没有改产品判定、重算成功或继续付费尝试，详见回执 repeat_confirmation_diagnostic。**优先修复结构化失败比较**，保留测试节点、阶段、异常类型、断言位置和来源，谨慎处理易变 repr，不能简单删掉全部消息后判成功。

动作日志累计 25 个不能解析为单 JSON 的返回，常见为多个 JSON 或供应商工具标记；另有 1 次截断/不兼容返回计入动作预算、没有进入已返回响应 trace。5 次写候选被目录约束拒绝，其中同一案例可重复触发。它们是流程摩擦，与反复搜索同时发生，不应按互斥失败原因相加。

代码阅读还发现 SyntaxError 硬检查按异常字符串统一归为候选错误，可能错拒 attrs 1319 这类目标自身生成代码出错的 Bug。但该例本次未生成测试，因此这只是待验证风险，**不是本轮实际停因**。

## 用量、保留记录与下一步

新增轮共 **216 次 HTTP 尝试、1,093,322 token**（输入 971,739、输出 121,583），177 个动作预算；读取/搜索 116 次。所有 17 例均保留，包括 9 个失败，分母没有删除。
累计选定的最终记录共 244 次 HTTP 尝试、1,203,284 token；不包含旧案例原始及中间失败轮的额外用量。

建议按顺序做后续独立变更：复现签名稳定性 → 有限动作下尽早生成执行 → 单动作协议可靠性 → 测试目录/目标异常归因。修复后保留本轮作为新基线，使用相同 17 例、描述和提交另跑对照；不要覆盖当前失败回执。

- [完整新增结果与证据哈希](../evals/cases/expanded-historical-results.json)
- [累计 20 例统计](../evals/cases/cumulative-historical-results.json)
- [新增来源与审查表](../evals/cases/expanded-review.csv)
- [旧 3 例修复复测](historical-case-fixes.md)；[原始 0/3 基线](historical-case-validation.md)

本轮只增加评估材料和文档，产品源码/提示未改变；此前离线 122 passed、1 skipped 仍为上一轮记录，本轮没有重跑离线全套。独立模型比较、独立人类评审与跨平台 CI 仍待进行。
