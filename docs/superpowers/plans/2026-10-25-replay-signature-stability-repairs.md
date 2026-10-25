# Replay Signature Stability Repairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 这是独立实施单；不得因为另一份经验修复已获准就自动执行本计划。

**Goal:** 对同一候选的两次原版失败，识别有程序来源的对象地址噪声，避免错误的 REPLAY_UNCONFIRMED 让模型反复改测试。

**Architecture:** 保留严格的绑定、完整性、测试集及失败值比较。优先使用已有完整断言操作数；仅为缺少比较操作数的 pytest 布尔断言补充有界运行时身份依据，并让重跑拒绝原因可诊断。

**Tech Stack:** 现有 Python/pytest probe、Verifier、TaskStore、pytest；无新依赖。主程序 Python >=3.11；probe 必须继续兼容目标案例使用的 Python 3.9 / 历史 pytest 4.4。

**Spec:** 本文件“修复契约”固定此次有限修复；架构背景见 [AgentScope 基础设施](../../agentscope.md)。经验卡与提醒不属于此计划，另见 [经验修复计划](2026-10-25-experience-card-and-reminder-repairs.md)。

## Global Constraints

- 基线为 E:/ReproAgent、feat/ai-tool-integration、a89871d 加工作区；不得 reset、覆盖其他代理改动或从裸 HEAD 漏掉现有保护。
- 保留 Verifier.confirm 的 bool 接口、两次独立 original 执行要求、全部绑定与保护检查、fixed 隔离。
- 不通过降低重跑次数、增大步数预算、忽略失败文本或只比较 REPRODUCED 标签来解决问题。
- 不全局删除 `0x...`、数字、repr 内容或 quoted 字符串。不能把诊断用的宽松正则放进生产。
- 旧记录缺少可信身份依据时保持保守；不能给历史 JSON 人工补 ID、改 verdict、重写 task.json 后宣称修好了。
- 不改模型、提示词、经验分类/触发器、评分标准或 SWT 金标；不调用模型做历史重判。
- 实现、测试和最终报告分开；真实模型仍须另行授权，默认不提交/push/merge。

## 已核对的事实

证据目录：`repro-results/experience-draft-decision-20261025/5227-authorized/run/`。

- 10 个发布候选，17 次 original 执行和 17 份 verdict，另有 1 次 fixed 执行。
- 候选 1/2/4/7/8/9：两次 verdict 都是 REPRODUCED，但 confirm=False，产生 6 次 REPLAY_UNCONFIRMED。
- 候选 3/5 存在 semantic_response_valid 问题；候选 6 为 INVALID_CANDIDATE/provider_semantic_claims；候选 10 两次确认及 fixed 验证通过。这 3 个单次执行问题不在本计划中修复。
- 只读诊断中，临时忽略 pytest 展示里的 str 对象地址可让上述 6 对中的 5 对确认成功；再处理 logging.Formatter 对象展示地址后，6 对都可确认。诊断只在独立进程中临时替换函数，未写产品或历史产物；它定位差异，不构成安全生产算法。
- 典型失败为 `assert formatter.format(record).startswith('DEBUG')`，assertion_comparison=None；pytest 自动解释含每个进程不同的字符串对象/Formatter 地址。最终候选改为完整字符串比较，已有操作数路径可稳定确认。

## 修复契约

1. `==`、`in` 等已有完整 assertion_comparison 的断言继续按真实 operator/left/right 比较；不改其语义。
2. 没有比较操作数的布尔断言，只有获得失败断言对应的程序运行时身份依据时，才可归一化相应显示地址。
3. 优先从失败测试帧中的 pytest 重写临时值取证；不得枚举并持久化用户全部 locals，不执行用户 repr、属性、迭代器或任意代码。
4. 仅记录有界整数 ID 与来源标记；对象类型、断言位置、实际消息与业务值仍被比较。没有可信依据就保留原严格比较。
5. 两次运行的真实值、异常类型、测试位置、执行阶段、测试集、契约或保护结果不同，必须继续拒绝。

## Review Focus

1. str.startswith 与嵌套 Formatter.format 展示地址：Task 1/2 必须用真实 pytest 子进程复现。
2. 业务文本恰好含 `0x...`、伪造对象 repr、quoted 地址：Task 2 必须继续严格区分。
3. 自定义 repr/descriptor/iterator 有副作用或抛错：Task 1 的采集不能额外执行它们。
4. 缺失、畸形、超量身份字段与历史记录：Task 1/2 必须保守降级，不因元数据异常崩溃或放宽。
5. 地址噪声与真实异常/操作数变化同时出现：Task 2/3 必须仍拒绝，不能只修正向例。

## Task 1: 失败断言展示的运行时身份取证

**Files**
- Modify: `src/reproagent/adapters/languages/python_pytest/probe/reproagent_pytest_probe.py`。
- Verify/modify only if required: `src/reproagent/adapters/languages/python_pytest/collector.py`。
- Test: `tests/integration/test_pytest_probe.py`。

**Interfaces**
- probe 内新增 `assertion_display_identities(item, call, limit=64)`，返回 `None` 或 `{"provenance":"pytest_rewrite","object_ids":[...]}`。
- test_phase 可选字段精确为 `assertion_display`；没有依据的失败用 null/缺省，不伪造空证明。
- 最多 64 个正整数 ID，最多观察 256 个对象；只跟随精确内置容器和精确 MethodType/BuiltinMethodType 的 __self__，不遍历任意用户对象。
- 取证限于实际失败测试帧中 `@py_assert` 前缀的重写临时值；只有在失败行可对应无自定义消息的 AST Assert 时启用。bound-method 的真实 __self__ 身份可记录，即使它是 str；不能因此把所有普通字符串都加进旧的 object_identities。
- 原有 `object_identities(left/right)` 的比较操作数契约不改，不为这个修复把所有字符串无条件当地址噪声。

- [ ] **1. 先做特征测试并见红。** 用真实 pytest 子进程执行不带自定义消息的 startswith 布尔断言；证实失败对应的重写临时值及其 bound-method __self__ 可取得。覆盖 Formatter.format(...).startswith(...)，确认身份字段包含显示地址所对应对象，而非只凭异常文本猜数字。
- [ ] **2. 明确可信边界。** 仅接受与失败断言帧/行匹配的 pytest 重写临时值；不能证明来源、使用自定义断言消息、非重写运行或不支持的 pytest 版本时返回 None。此处特征测试若不能成立，先记录阻塞并修订取证方案，不得换成宽松正则继续。
- [ ] **3. 最小实现并落入原始 test_phase 记录。** 只保存来源标记和数字 ID；不存 locals 值，不扩散到模型上下文。不改变异常、不吞取消/退出信号，取证失败不改变原测试结果。
- [ ] **4. 加反向测试。** 自定义 repr、descriptor、iterator 的计数在取证前后不变；超过上限有界停止；含业务地址的自定义 AssertionError 不获“可归一化”的证明。
- [ ] **5. 验证 collector 保留且检查新字段。** 确认原始 probe JSONL 和 ExecutionObservation.tests 一致；来源标记、ID 类型或数量不合法时不使用该字段。不能仅改内存观察却不保留原始依据。

Run:
```powershell
python -m pytest tests/integration/test_pytest_probe.py -q --basetemp=<仓库外唯一目录>
```
Expected: 真子进程能取到依据；不支持输入保守回退；目标 pytest 4.4 / Python 3.9 另做本地兼容检查，不能把未覆盖当通过。

## Task 2: 仅用已证明身份稳定比较失败

**Files**
- Modify: `src/reproagent/core/failure_signature.py`（仅确有必要时）、`src/reproagent/core/verifier.py:confirm`。
- Test: `tests/unit/test_failure_signature.py`、`tests/unit/test_verifier.py`；新增 `tests/integration/test_replay_display_stability.py`。

**Interfaces**
- `stable_failure_message(message, object_ids=())` 的现有参数和默认保守行为保留。
- `Verifier.confirm(contract, candidate, first, second, verdicts) -> bool` 返回类型不变。
- 完整比较操作数优先；仅 fallback crash.message 路径使用通过 Task 1 校验的 assertion_display.object_ids。

- [ ] **1. 补成对真执行回归并见红。** 使用相同冻结候选、两次全新 original 执行，startswith/Formatter 的展示地址不同，但目标行为、断言位置与失败值相同，confirm 应为 True。必须经真实 probe→collector→Verifier，不能手工塞 object_ids。
- [ ] **2. 最小接线。** 将已校验的身份依据传给原有保守归一化函数；保留路径规范化、绑定/完整性、preconditions、collected/completed 测试集等全部检查。
- [ ] **3. 补负例并见绿。** 真实字符串值不同、operator 不同、异常类型不同、crash 行号/路径不同、不同失败阶段、固定版混入、保护失败均为 False；quoted 或未经证明的业务地址不得改变。
- [ ] **4. 保留旧记录保守性。** 历史无 assertion_display 的两个记录可以继续 False；不能把“旧档案被宽松洗成一致”作为验收条件。
- [ ] **5. 本地重新执行历史候选。** 从原任务读取候选 1/2/4/7/8/9 的冻结内容，在新任务目录、同原版和解释器下重跑两次，采集新依据，再验证 confirm。只用已有冻结契约/候选与记录型 verdict 夹具，不请求模型生成新 verdict；报告须注明这是机制重放而非新的真实模型成功率。

Run:
```powershell
python -m pytest tests/unit/test_failure_signature.py tests/unit/test_verifier.py tests/integration/test_replay_display_stability.py -q --basetemp=<仓库外唯一目录>
```
Expected: 同一可证明失败能确认，所有真实差异负例仍拒绝。

## Task 3: 程序重跑诊断与最终验收

**Files**
- Modify: `src/reproagent/core/verifier.py` 的 confirm 内部诊断。
- Test: `tests/unit/test_verifier.py`、`tests/integration/test_replay_display_stability.py`。
- Document: `docs/agentscope.md`；机制验证证据保存在独立 repro-results 目录。

- [ ] **1. 添加 replay.compared 事件和 trace 标记，不改 bool 接口。** 带 candidate_id、两次 run_id、confirmed 及受控原因：CONFIRMED / VERDICT_MISMATCH / BINDING_OR_INTEGRITY_MISMATCH / PRECONDITIONS_UNSUPPORTED / TEST_SET_MISMATCH / NO_FAILURE_SIGNATURE / FAILURE_SIGNATURE_MISMATCH。
- [ ] **2. 差异只记录索引与字段名。** 例如失败 phase 的 nodeid/when 和 difference_fields，不把原始日志、模型正文或完整业务值塞入诊断。TaskStore/trace 写失败不改变 confirm 结果。
- [ ] **3. 跑完整离线回归并保留首次失败、skip 理由与源码哈希。** 不并行跑多组重型完整回归造成不必要的 finalize 时限干扰；不因测试压力随意提高产品期限。
- [ ] **4. 独立审查。** 重点尝试 quoted 地址、业务十六进制值、缺失身份依据和真假差异混合反例；正例修复不能替代负例证明。
- [ ] **5. 用户单独授权后才跑一次真实 5227。** 分开报告候选数、original 执行数、语义验收数、程序重跑拒绝数和 fixed 结果。减少 REPLAY_UNCONFIRMED 是机制指标，不预设一定少于 10 个候选，更不能据单例声称整体收益。

## 最终交付要求

- 两份计划分别报告，不因经验写卡成功就声明重跑问题修好，反之亦然。
- 提供 RED/GREEN、原始 probe、新执行记录、差异事件与完整回归结果。
- 不运行或修改用户经验库，不改历史 official 分数，不自动提交、push、合并。
