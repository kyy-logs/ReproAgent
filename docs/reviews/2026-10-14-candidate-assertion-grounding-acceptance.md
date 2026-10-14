# 候选断言依据检查：8906 / 11445

日期：2026-10-14（Asia/Shanghai）。起点 `dbdf7e7`，分支 `codex/candidate-assertion-grounding`，初始实现 `6272afa`。

## 本批范围与验收标准

用户要求修复上一轮剩余问题，按已建议顺序先处理 8906、11445 的候选断言质量。采用现有生成/验收流程内的局部修复：

- 改：断言位置清单、验收模型响应契约及覆盖/引用校验、分析/探索/验收提示、对应离线测试。
- 不改：Controller 状态机、工具权限、步骤/费用/时间上限、模型配置和 thinking、候选/业务源码保护、经验库、固定版隔离、默认官方 harness 及历史成绩。
- 通过标准：首个真实失败不能代替后续断言的审查；清单内漏审、重审、未知 ID 或只引用失败日志不能获得成功；明确否定不能经格式重试改成通过；新字段进入真实 SDK 请求；旧候选与修正候选的实际执行可独立对照。

此批不包含 8365、5221、8474 的后续真实模型评测，也不将人工修正候选的结果记成 Agent 自动成功。

## 核实的根因

### 11445：未执行的后续断言被首个失败掩盖

原候选先断言 index.html 中包含 `mypackage2`，随后要求 doctree 的首个子节点一定为 section，并检查它的 title。原版在首个断言失败；原两次 REPRODUCED 判定都只证明这个失败与 Issue 匹配，第二次甚至明确写出后续断言未执行，仍接受整个候选。

Issue 支持标题渲染/toctree 的检查，没有给出内部子节点顺序承诺。修复版让第一个断言通过后，后续 `first.tagname == 'section'` 因首节点为 substitution_definition 失败。

### 8906：过度约束旧措辞；放宽后又会误中元数据

冻结候选要求同一输出模式同时含旧文案 `Using pytest.skip outside of a test is not allowed` 与 `allow_module_level`。Issue 只要求错误指引说明该选项，没有要求保留旧句子；修复版改了句子，因此旧候选两边均失败。

本批先实际试了仅改成 `*allow_module_level*` 的诊断变体，发现原版也通过：完整 stdout 中的测试名称/路径已有该词。这一变体为 **[0, 0, 0]**，没有差分，明确保留为失败实验；不能当作修复。

最终诊断变体从原版 `_pytest.pytester` 确认 inline_run / getfailures 接口，从原版 `_pytest.nodes` / `_pytest.python` 确认 CollectError 的报告正文路径，直接检查 `pytest_collectreport` 失败报告的 longrepr。正确性检查限定到诊断正文，不从测试名称/路径/回显输入里找词，也不要求旧句子。原有正常 skip control 保留。

## 实现

`core/assertion_audit.py` 对完整、已校验哈希的 test-role Python 文件做 AST 扫描，定位 assert、常见 pytest 异常/告警检查、LineMatcher 和 assert* 调用，生成稳定的 A1… ID、路径、起止行和类型。它不执行候选，也不尝试证明语义。

数据文件仍完整送审；不把故意携带非法 Python 的 data fixture 当作候选语法错误。自定义断言、别名和动态调用不能保证完全静态识别，因此仍要求模型审阅全部候选文件；“清单覆盖完整”不等于“所有语义已经由程序证明”。

原有 verdict 请求增加 `assertion_sites`，response_schema 必填 `assertion_reviews`：

```json
{
  "assertion_id": "A3",
  "supported": false,
  "reason": "Issue 没有承诺内部首个节点必须是 section",
  "evidence_refs": []
}
```

- 程序要求清单内每个 ID 恰好一项，无漏项、重复或外来 ID。
- supported=true 必须带原始期望来源的精确 path/hash/line 引用；失败日志只证明实际发生了什么，不能代替期望依据。引用对象只含四个字段，不复制 source.text。
- 任一有效项 supported=false，整体转为 INVALID_CANDIDATE，反馈对应路径、行号和理由。明确否定即使夹在格式无效的回复中，也不能通过纠错请求晋升为成功。
- 旧的总体 booleans、程序硬检查、引用验证和独立重复确认均保留。没有逐项字段的旧模型响应走已有最多三次格式纠错，不能静默放行。
- JSON 本身非法或含重复键、非有限值、非对象根时无法可靠保留否定声明，直接返回 MODEL_PROTOCOL_ERROR，不再发送纠错请求；合法对象的字段/引用错误仍按原上限纠错。响应返回后先检查取消与全局预算，不能用协议失败覆盖更高优先级的停止。
- trace 新检查 `candidate_assertions_supported` 标为 provider_claim；来源是否语义上支持断言仍是模型判断，不冒充程序证明。

三类提示同步强调：不要把 reported_actual 的旧文案写成 expected；不要凭可见标题发明内部节点顺序；不要对完整测试转录搜关键词来代替目标结果/诊断。每项 reason 要说明断言实际读的值或通道。

仍使用原验收请求，不新增模型阶段；逐项输出会消耗现有模型输出额度，配置上限未增加。输入清单与 schema 纳入现有大小测量；超限仍拒绝审查并保留不确定性，不删候选/失败正文来凑数。

## 离线回归证据

| 检查 | 实际结果 |
| --- | --- |
| 原相关基线：verifier / candidate guidance / protocol regressions | 44 passed，48.82s |
| 新断言审查 RED | 4 failed、4 passed：缺清单、旧总体判断被接受、后续否定不能阻止接受、否定被重试 |
| audit / verifier / review_context GREEN | 32 passed，53.70s |
| 初次 SDK 提示交付 RED | 1 failed：缺新措辞边界 |
| SDK / protocol / audit / large context GREEN | 40 passed，48.51s |
| 非法 Python data fixture RED → GREEN | 先失败，随后 audit 9 passed，10.31s |
| 集中相关回归 | **109 passed，280.48s，exit 0** |
| 元数据误命中提示交付 RED → GREEN | 先 1 failed；补齐后 SDK + audit **10 passed，17.04s** |
| 审查后 BOM / 非法 JSON 否定保护 RED | 5 failed，7.65s：BOM 误拒；重复键、非有限值、语法错、非对象根均可被第二次回复晋升 |
| 真实 SDK 重复键否定保护 | 1 passed，3.95s；一次 HTTP 尝试及一次费用记账，无后续请求 |
| 审查补修后扩大回归 | 57 passed、1 failed，64.51s：发现取消被终止协议分支吞掉，未视为通过 |
| 补充任务过期优先级 RED | 1 failed，1.90s：预算到期也应先于协议结果 |
| 全部补修后的同范围最终回归 | **116 passed，261.40s，exit 0** |

集中相关回归及最终回归覆盖 audit、verifier、review_context、controller、model_protocol_regressions、candidate_quality_guidance、large_review_context、agentscope_backends、observability_end_to_end。测试组有重叠，不相加为唯一计数。最终日志保存在 `E:/ReproAgent/.local/assertion-audit-20261014/final-acceptance.log`；初次 109 项记录为同目录 `acceptance.log`。功能分支阶段未重新跑全仓测试；合并阶段全量验证见下文，此前 main 的 1055 项结果不代替本批验证。

SDK 验证使用 httpx.MockTransport，确认断言清单和必填响应 schema 到达实际请求，正常 DONE/差分路径、固定版隔离、原调用顺序和探索扣步不变。它证明请求交付和程序拒绝条件，不证明真实模型已学会逐条判断。

对两条原始历史记录只读构建新投影：8906 为 **26997 B**（2 个清单项），11445 为 **21839 B**（4 个清单项），均低于 32768 B。这只是大小/完整性测量；8906 原记录当时环境未通过，不声称它已经执行过语义审查。

## 实际案例对照：无模型调用

沿用 002 的原解释器和源码绑定；新原版快照逐文件哈希与冻结快照一致。新输出目录保存每个候选、执行 spec、探针、日志和回执，不修改原候选或预测文件。

| 案例 | 变体 | original 1 / original 2 / fixed 退出码 | 结论 |
| --- | --- | --- | --- |
| 11445 | 冻结候选 | 1 / 1 / 1 | 后续多余断言导致补丁版也失败 |
| 11445 | 依据 Issue 保留 HTML 检查，删除无依据内部结构检查 | **1 / 1 / 0** | 观察到差分 |
| 8906 | 冻结候选 | 1 / 1 / 1 | 旧句子约束导致补丁版也失败 |
| 8906 | 仅放宽为完整 stdout 关键词 | **0 / 0 / 0** | 误中元数据，无效变体 |
| 8906 | 读取实际 collection 失败报告正文 | **1 / 1 / 0** | 观察到差分 |

最终两条对照的原版两次、补丁版一次均为 EXITED，probe_complete / source_binding / protection / cleanup / temp_cleanup 全部正常。修正的候选由原 Issue 和原版接口人工推导，修改先于固定版执行。没有向生成或审查模型发送 gold/fixed 内容，也没有调用真实 provider 或 Astra。

证据根：`E:/ReproAgent/.local/assertion-audit-20261014/`：

- `executions/sphinx-doc__sphinx-11445/receipt.json`：11445 最终对照。
- `diagnostic-body/pytest-dev__pytest-8906/receipt.json`：8906 最终对照。
- `executions/pytest-dev__pytest-8906/receipt.json`：保留的关键词误命中实验。
- `replay_candidates.py`、`replay_diagnostic_body.py`、对应日志、`historical-projection.json`。

不将上述人工诊断候选写入官方 predictions，不覆盖原 4/10。真实模型重新生成候选、独立交付与官方新一轮判分尚未进行。

## 独立审查与交付状态

独立审查初轮集中 10 项通过，但确认 1 项 Important（严格 JSON 解析失败使否定声明不可见，后续回复可晋升成功）和 1 项 Minor（BOM 误拒）。两项均已修，审查者独立 14 项通过（17.59s）；随后发现并修复取消/期限优先级，审查者又独立运行取消、期限、SDK 重复键及合法 JSON 有界纠错，共 **4 passed，9.98s**，确认无新阻断问题。实现方最终同范围 **116 passed**。BOM 仅在 AST 解析时剥掉开头标记，发送文本、文件哈希及行号不变。

功能分支验收时尚未合并、推送；用户随后单独授权合并及 push，执行记录见下文。

实现期间 main 的另一会话新增了 `804ac7b`，仅提交待审阅的 `2026-10-14-verifier-failure-feedback-design.md`。本批未修改或实现那份设计。它对未来失败反馈功能写有“保持原六字段”的范围约束；本批独立增加的 assertion_reviews 是断言审查所需模型协议扩展，后续反馈实现需基于最终合并后的协议同步设计，不能假定该字段不存在。当前生产代码没有并发修改冲突。

## 合并阶段全量验证（2026-10-15）

按用户“push + 合并”授权同步远端，将功能分支 `696e456` 合入 main，合并提交 `b1e22f1`。保留 main 已有 `804ac7b` 设计文档；源码与测试和审查分支一致，无合并冲突。

主工作区实际执行：

```powershell
.venv/Scripts/python.exe -u -m pytest -q --basetemp C:/Users/Dell/AppData/Local/Temp/reproagent-assertion-merge-full-20261014
```

结果 **1071 passed、6 skipped，907.01s，exit 0**。日志：`E:/ReproAgent/.local/assertion-audit-20261014/merge-full.log`。未调用真实模型接口，未使用 Astra。`git diff --check` 通过。

合并前后，用户未提交的 `docs/reports/reproagent-问题与挑战记录.md` SHA256 均为 `a9eca4c9de48586dda5ed6fe298f1d7f5af58f1c309621636de665f5d6f5244c`；未跟踪的评测 HTML SHA256 仍为 `aaa7f31a825f9c89e80bb8249f364d2c2b2be59aceb972d29c76d0b67551c0e2`。两者不纳入本次提交。
