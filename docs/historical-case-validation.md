# 三个历史 Bug 的真实模型验证

日期：2026-10-05。

## 结论

本次选择了两个纯 Python 工具库中的三个公开历史 Bug。
三个案例都能在本地运行：独立人工对照测试在 Bug 版本失败，在修复版本通过。
现有 ReproAgent 使用 `deepseek-flash` 自动复现的结果为 **0/3**。
没有案例进入候选执行或生成成功复现包，不能声称完成重复复现、修复验证或导出重放。

这是一次小规模、手工选择的诊断性冒烟，只有一个模型、每例一次运行，不能推算一般复现率。
本次未修改产品代码或提示词，保留当前版本作为后续修正的对照基线。

## 案例与结果

| 案例 | 正确预期 | 人工 pytest 对照：原版 / 修复版 | Agent 状态 | 模型请求 | 耗时 |
| --- | --- | --- | --- | ---: | ---: |
| [more-itertools #462](https://github.com/more-itertools/more-itertools/issues/462)，[修复 #645](https://github.com/more-itertools/more-itertools/pull/645) | 空迭代器产生零个窗口 | 1 failed / 1 passed | EXHAUSTED，NONE | 13 | 26.22 秒 |
| [humanize #250](https://github.com/python-humanize/humanize/pull/250) | `naturalsize(1.0)` 和 `naturalsize("1")` 显示 `1 Byte` | 2 failed / 2 passed | EXHAUSTED，NONE | 13 | 36.72 秒 |
| [humanize #14](https://github.com/python-humanize/humanize/issues/14)，[修复 #254](https://github.com/python-humanize/humanize/pull/254) | 31/92/32 天分别显示 `1 month`、`3 months`、`1 month and 1 day` | 3 failed / 3 passed | NEEDS_INFORMATION，NONE | 1 | 21.34 秒 |

人工对照由本次研究者编写，位于 `.local/historical-cases/<case>/manual-controls`，
没有加入 Agent 的原始仓库副本，也没有提供给模型。人工对照通过不计作 Agent 成功。

第三例使用修复提交的父提交作为 Bug 版本，而原报告使用较早版本。
本地实际 92 天输出为 `3 months and 0 days`，与原报告的 `3 month and 0 days` 有单复数差别；
多余零天问题仍存在，31 天与 32 天的报告症状也已独立确认。

## 版本与输入

| 案例 | Bug 提交 | 修复提交 |
| --- | --- | --- |
| more-itertools-645 | `8a27da60d70fbfc348b124ce89733d8a7104977a` | `9a1168a13e38e35396d1f4722f4a204f585cdda1` |
| humanize-250 | `8059ebe1732c89177709476165f6e87cc76fe1b7` | `2968d44280a68f775db73ddeb2f2fc6c05edac84` |
| humanize-254 | `58d10b43175a22eb9ece2c6f3dcff6b6b681e2df` | `0f5d2948d674a88a611f8e341c0df520767b1b80` |

使用 GitHub 官方提交归档，来源正文、归档与实际输入文件的 SHA-256 保存于
`evals/cases/historical-smoke.json`。描述是从原报告及明确的行为说明整理的简述；
模型只收到该描述、Bug 版本的文件索引和工具读取结果。
修复代码、新增上游回归测试、人工对照与修复 diff 均未进入 Agent 输入。
模型是否曾在训练中见过这些公开材料未知，本次只能控制运行时输入。

Humanize 的 GitHub 源码归档缺少构建时生成的 `_version.py`。
本次在可丢弃构建环境安装 `hatchling` / `hatch-vcs`，用项目原生构建钩子生成版本元数据；
通过 `SETUPTOOLS_SCM_PRETEND_VERSION` 指定占位版本。原版与修复版使用同一个占位值，
该值不能当作精确提交版本，精确版本以以上提交 ID 为准。生成文件哈希已记录。
业务源码未修改，wheel 产物保存在仓库副本之外；目标运行环境仍只需 pytest 与标准库。

环境：Windows，工具和目标 Python 3.12.14，pytest 9.1.1。
模型：`https://api.deepseek.com` / `deepseek-flash`，`max_tokens=4096`，未覆盖供应商默认思考设置。
每例预算：12 个动作、主任务 240 秒、单命令 30 秒；其他预算使用当前默认值。
运行期间没有人工追加信息、修改测试或调整提示词。
合计 27 次 HTTP 模型请求，62182 输入 token、15363 输出 token，总计 77545 token。
未配置供应商费率，费用为 unknown。契约分析不占动作步数，因此请求数可能超过动作数。

## 失败定位

### 1. 工具 schema 只提供类型，没有提供枚举及用途

目前模型看到 `search_code.scope` 的类型是 `str`，却没有看到仅允许
`snapshot`、`candidates`、`all`。模型实际反复填写模块名或文件路径，例如
`more_itertools/more.py`、`src/humanize/filesize.py`、`tests/test_filesize.py`，被拒绝后仍重试。
反馈只说明 scope/query 无效，没有告诉模型允许的枚举值。

模型也尝试读取 `input/issue.md`。该路径是任务中的证据来源，read_file 实际接受的却是
冻结仓库或候选中的相对路径。工具说明没有充分区分这两类路径。

### 2. 非必需缺失信息阻止生成候选

前两例契约均提取出了正确输入与预期，但把其他输入类型、内部实现或版本信息也列为缺失项。
控制器因 `contract.missing_information` 非空而拒绝候选。
Humanize 文件大小案例中，模型实际尝试写候选，随后收到此拒绝反馈。
当前没有证据说明单纯增加步数即可解决该问题。

### 3. 契约输出的字段类型没有明确约束

第三例模型返回了合法 JSON，却把 `trigger` 写成对象，将 `expected` / `reported_actual` 写成对象列表。
核心契约要求这些字段为字符串，因此报 `invalid contract text` 并在分析阶段停止。
最终状态映射为 NEEDS_INFORMATION，但本次错误来自模型输出结构，不能归因于用户没有提供预期。

前两例还出现过空白文本，以及一次回复含两个 JSON 对象的动作输出。
JSON 模式本身没有保证应用要求的字段结构或恰好一个动作对象。

## 后续修正与复测顺序

1. 给模型完整的字段类型、允许枚举和工具调用示例；错误反馈列出允许值。
2. 将缺失信息限定为本次报告范围内真正阻止复现的前提，保留预期不明确时的来源要求。
3. 为契约协议错误提供有预算的反馈重试，并区分模型协议错误和用户信息不足。
4. 使用这里固定的三个输入、提交与预算复测。模型生成候选后，依次验证实际失败、
   独立重复失败、修复版通过、导出包在新副本中重跑。
5. 随后扩大到更多项目与历史 Bug，增加独立人类正确性审查。

本次由 Codex 核对公开来源、版本和症状，尚无独立人类评审。
这三个失败案例也保留在分母中；没有成功复现包，所以独立导出重放指标不适用。

## 本地证据

- 可分享案例定义与哈希：`evals/cases/historical-smoke.json`。
- 可分享完整结果统计：`evals/cases/historical-smoke-results.json`。
- 来源/环境核对表：`evals/cases/review.csv`。
- 每例模型动作记录：`.local/historical-cases/<case>/trace.jsonl`。
- 人工对照日志：`.local/historical-cases/<case>/manual-controls/buggy.log`、`fixed.log`。
- 原始任务与诊断包：`repro-results/historical/<case>`。

后两类本地目录默认忽略，不含需要公开发布的仓库资产。上述公开定义和报告不包含凭据。
