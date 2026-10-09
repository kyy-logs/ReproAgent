# 冻结保留集 10 例的 issue 类型标注：2026-10-09

这份标注是 [架构验收记录](2026-10-08-agentscope-acceptance.md) 的**读法说明**。那一份里的数字（谁出了候选、
谁 EXHAUSTED、官方判了几分）必须对着这一份读，否则会把三种完全不同的东西混成一个数字：

1. **产品缺陷**（收集崩溃、重放闸门漏归一化——都已修）；
2. **模型质量**（候选对 pytest 7 用了 `py.path` 的 `isdir()`）；
3. **产品前提不适用**（这条 issue 根本没有可落地的预期行为）。

## 分类规则

- **bug 报告**：描述某个**正在发生的错误**（异常、报错、错误的输出），并给出触发条件或复现步骤。
  预期行为由 issue 本身确定——"它不该这样"。
- **提案 / 开放讨论**：要**增加或改变**某个行为，而当前状态不是"错"。作者用"it would be useful"、
  "I think"、"might"、"potentially" 这类措辞，或明说方向未定。

判断依据一律引用 issue 原文，读者可以不同意某一行。

## 10 例标注

| 用例 | 类型 | 判定依据（原文） | 预期行为能否从 issue 落地 | 本轮结果 |
| --- | --- | --- | --- | --- |
| pytest-**5221** | 提案 | "**It would be useful to** show fixture scopes…" 全篇无格式说明 | **否** | EXHAUSTED，0 候选 |
| pytest-**5227** | 提案（带确切格式） | "**I think** `name` would be very useful here" —— 但随后给出 `DEFAULT_LOG_FORMAT = "%(levelname)-8s %(name)s:%(filename)s:%(lineno)d %(message)s"` **和期望输出样例** | **原则可以**，但见下节：被我们自己的证据规则挡住 | EXHAUSTED，0 候选 |
| pytest-**8365** | **bug 报告** | "tmpdir creation **fails** when the username contains illegal characters" + 复现环境 | 是 | **DONE / REPEATED_OBSERVATION**（修复版对照未通过，见验收记录） |
| pytest-**8906** | 开放讨论 | "**This is potentially about** updating docs, updating error messages **or** introducing a new API." 作者自己列了三个互斥方向 | **否** | EXHAUSTED，0 候选 |
| sphinx-**11445** | **bug 报告** | "### Describe the bug … do not render the heading correctly" + "### How to Reproduce" | 是 | DONE / DIFFERENTIAL_VALIDATED，**官方 RESOLVED** |
| sphinx-**8474** | **bug 报告** | "suddenly the following warning started popping up in our builds" | 是 | EXHAUSTED（容器判不了，见验收记录） |
| sphinx-**8627** | **bug 报告** | "**Describe the bug** … `class reference target not found: Struct`" + "**Expected behavior**" | 是 | DONE / DIFFERENTIAL_VALIDATED（容器判不了） |
| sympy-**15678** | **bug 报告** | "idiff doesn't support Eq… Both should be easy to correct." + traceback | 是 | 未执行（无环境） |
| sympy-**18621** | **bug 报告** | 转换 `BlockDiagMatrix` 抛异常 + traceback | 是 | 未执行（无环境） |
| sympy-**24213** | **bug 报告** | "does not detect equivalent dimensions in addition" + 完整复现 + traceback | 是 | 未执行（无环境） |

**8 条是 bug 报告，2 条（5221、8906）是开放讨论，1 条（5227）是带确切方案的提案。**

## 这解释了先前那个"pytest 差、sphinx 好"的印象

先前的观察是"pytest 四条全 0 候选、sphinx 三条有产出"，很容易读成能力差异。按类型看是另一回事：

| 类型 | 用例 | 结果 |
| --- | --- | --- |
| bug 报告 | 8365、11445、8474、8627 | **都有候选**（8365 复现两次、8627 差分通过、11445 官方 resolved） |
| 提案 / 开放讨论 | 5221、5227、8906 | **全部 0 候选** |

**决定结果的不是仓库，是 issue 的类型。** pytest 那批恰好以提案为主。

## 5227 那一类：我们自己的证据规则与数据集真值不一致

`pytest-5227` 值得单独记，因为**它的预期行为在 issue 里是写明的**（给出完整的目标格式串和期望输出样例），
但产品的规则把它判成不可落地：

`analyze_issue` 第 6 条要求——"提案的接口或显示名必须先由原始文档、签名或既有测试确认，否则只能进
`missing_information`，**绝不能因为 issue 自己的示例就写进 `expected`**"。

而 SWT-Bench 的真值（gold patch 及其测试）**正是把维护者采纳的那个提案当作标准**。

所以对这类实例，产品**必然**失败——不是能力问题，是**证据门槛比数据集真值更严**：它拒绝把"提议的显示
格式"当作正确性标准，而数据集恰恰是拿它当标准的。这是一条有代价的策略选择，值得单独记着：

- 好处：它挡住了"照着 issue 里的例子臆造接口"这一类假复现；
- 代价：凡是"issue 提议了确切方案"的实例，我们**永远拿不到候选**。

## 对筛选的意义

先前提的筛子（**金标能 `discriminating`**）只筛得掉**环境坏**的实例。它筛不掉类型不匹配。要筛掉后者，
需要一条语义规则：

> 这条 issue 描述的是**一个正在发生的错误**，还是**一个希望增加/改变的行为**？

10 条人读几分钟即可；量大了可以先用模型分类，再抽样人工复核。**按类型筛过的样本，才真的在测架构。**

## 这些标注改变什么、不改变什么

- **不改变**已修的两个产品缺陷的结论（探针、重放闸门）——它们在任何类型上都是缺陷。
- **不改变**官方判分：`11445` 仍是唯一 resolved，`8627`/`8474` 仍因容器缺陷判不了。
- **改变**的是"0 候选"该怎么读：`8365` 的 0 是候选质量（已在重跑中变成 DONE），`5221`/`8906` 的 0
  是**产品前提不适用**，`5227` 的 0 是**证据门槛与数据集真值不一致**。三者不能合并计数。

## 证据在哪

| 内容 | 位置 |
| --- | --- |
| 10 条 issue 原文 | `.local/swt-bench/holdout-issues.txt`（由 snapshot 的 `problem_statement` 导出） |
| 5221 的契约与修订理由 | `repro-results/oneshot-pytest-5221/cases/pytest-dev__pytest-5221/task/contracts/` |
| 单例清单与绑定生成器 | `.local/swt-bench/one-case-manifest.py` |
