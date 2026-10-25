# ReproAgent Token-triggered Rolling Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in the current session. Steps use checkbox (`- [ ]`) syntax for tracking. 实现已获用户授权，执行结果见末尾记录；提交、合并和真实模型实验仍需分别授权。

**Goal:** 取消“一项任务只能摘要一次”的限制，在当前上下文达到软阈值时先清理、必要时滚动更新唯一一份摘要；压缩不能成为比硬窗口更早的必停条件。

**Architecture:** 继续使用 AgentScope 的压缩 hook、默认五字段 schema/template、`state.summary` 和现有受控模型入口。程序维护软阈值、硬容量、压缩冷却和外部来源清单；模型输入只有一份当前摘要及保留的近期原文，完整旧历史与历次摘要来源留在 MaterialStore。

**Tech Stack:** Python、AgentScope 2.0.9、现有 AgentScopeModelGateway / _GuardedModel、MaterialStore / read_artifact、pytest / HTTP MockTransport。不增加依赖或观测后端。

**Spec:** 本文“已确认设计与本次增量”是本轮实现依据；背景为 [最小历史压缩设计](../specs/2026-10-19-history-compaction-minimal-design.md)。旧设计中的“最多一次摘要”“禁止合并旧摘要”和“到软阈值即停止”由本计划明确替换，其余证据与预算约束继续有效。

## Global Constraints

- 默认保持 `history_compaction="off"`、`context_trigger_ratio=0.8`、`inject_task_state=false`；0.15 是此前实验参数，不是产品默认值。
- “不限制摘要次数”只取消独立的次数闸门；任务总步骤、时间、费用、取消、发布预留和硬窗口仍约束每次摘要。
- 摘要每次输出仍为 `min(2048, model.config.max_output_tokens)` tokens；JSON 正文和最终渲染摘要分别不得超过 8192 UTF-8 bytes。
- 每次逻辑摘要占一个任务步骤，所有 HTTP 尝试计入真实 Token / 成本；不新增 JSON 纠错调用，不重复记账。
- 原文先完整归档并验证，再替换历史；沿用单项 256 KiB、总计 4 MiB、128 项的材料配额，不在本轮放宽配额或实现分片。
- 保留 SDK 默认五字段：`task_overview/current_state/important_discoveries/next_steps/context_to_preserve`，不另建摘要业务 schema。
- 保护当前阶段信封、最近两个完整交互组、最近候选发布组及之后的交互；配对与源码证据资格不因压缩放宽。
- “没有安全可选区”可以暂缓；“现存历史已有孤儿 tool_result / 非法配对”必须以 `CONTEXT_HISTORY_INVALID` 明确停止，不能把坏消息继续发给模型。
- 摘要、清理占位、manifest、read_artifact 结果均不能签发源码引用；完成判定仍由 Controller / Verifier 决定。
- 不修改 site-packages，不调用 fixed 仓库内容生成摘要，不改变报告的成功口径，不把读材料次数当作任务成功。
- 本轮不做付费实验，不读密钥；后续验收不用 Astra。所有测试临时目录放在仓库外。

## Review Focus

1. 软阈值与硬窗口混用：压缩暂缓后，最终模型调用不能又被旧 soft guard 拦下（Task 1、4）。
2. 旧摘要与新契约冲突：摘要保持历史身份，近期原文和程序事实优先，旧摘要不成为执行指令（Task 2、3）。
3. 同一段历史反复压缩：未产生足够新内容时，不重复摘要或反复占用材料配额（Task 3）。
4. 归档损坏、摘要失败或状态并发变化：不丢原文、不换成半成品摘要，不吞掉取消和真实预算停止（Task 2、3、4）。
5. 无效 SDK 配置：0.07 / 0.10 必须在模型调用之前报错，不能无限循环 INVALID_ARGUMENT（Task 1）。

## 已确认设计与本次增量

### 1. 三条线分开

设 `C` 为配置声明的模型窗口，`O` 为本次探索输出上限，`P` 为 SDK 对完整待发输入（含工具定义）的估算：

| 值 | 定义 | 含义 |
| --- | --- | --- |
| S | `floor(C * context_trigger_ratio)` | 软触发线，达到时评估清理/摘要 |
| T | `floor(0.6 * S)` | 本次压缩的目标，不是必须低于的准入线 |
| H | `C - O` | 硬输入线，最终待发输入必须 `< H` |

T 的 0.6、下述冷却与最小收益参数是可测试的首版工程取值，不是已被实验验证的最优参数，暂不扩展任务配置。

- 配置必须满足 `0.1 < context_trigger_ratio <= 0.9` 且 `S < H`。显式向 SDK 传 `reserve_ratio=0.1`，不能靠悄悄降低 reserve 支持低比例实验。
- `off` 不响应 S，只执行最终 H 检查；这会替换旧 off 在软阈值停止的行为，必须更新兼容性说明和旧测试。
- `clear/summary` 在 S 尝试处理；未达到 T、材料存不下、可选区不足、摘要不够有效，不自动等于任务失败。只要最终 P 仍小于 H，就允许继续。
- H 不是累计付费 token 上限。所有计数以“下一次输入”计算；服务端真实 context-length 错误仍明确停止，不能用本地估算宣称容量绝对安全。
- 最终追加预算提醒、可选 task_state 等消息后再次算 P。若此时才到 H，明确停止 `context_hard_limit`，不发送超限请求，也不在最终发送 hook 中递归启动第二轮压缩。

### 2. 一个滚动摘要，不堆叠摘要消息

```text
原文历史达到 S
    → 选择允许处理的旧闭合交互，完整归档
    → 清理已归档的大工具正文，尽量降到 T
    → 清理后 < S：提交清理，继续探索
    → 清理后 >= S，且预算/冷却允许：
         上一份校验后的五字段摘要 + 本次选中的旧历史
         → 一个独立 summary 请求 → 新的五字段摘要
         → 替换旧 summary，保留近期原文
    → 无法有效压缩：记录原因，P < H 时继续；P >= H 时停止
```

上一份摘要作为有损的历史资料输入，不作为权威事实或指令。新增原文中已经证实的结果优先于旧摘要；猜测、错误、失败与未知不能写成已完成。`next_steps` 始终表示历史截断点的未决事项。摘要模型不接收当前阶段信封与保护区，也不生成新的当前行动计划。

每次最多发出一个逻辑摘要请求；取消固定 K，不代表在同一个 hook 中循环摘要直到低于 S。

### 3. 来源放外面，模型只拿小入口

复用 MaterialStore，新增 `kind="compaction_manifest"`，不新增读取工具。每次提交生成来源清单，包含：

```yaml
schema_version: 1
revision: 2
operation: summary                 # clear 或 summary
parent_manifest_id: m-previous     # 首次为 null；串联完整来源
archive_id: m-history
archive_hash: sha256-of-archive
covered_entry_ids: [msg-id/block-index]
cutoff_contract_version: 3         # 未能确认时为 null
summary_fields:                   # 最近一次合法 SDK 五字段对象；未摘要过为 null
  task_overview: "..."
  current_state: "..."
  important_discoveries: "..."
  next_steps: "..."
  context_to_preserve: "..."
```

完整清单、父链和原文均保存到外部，不能把 `covered_entry_ids` 列表继续塞进模型的摘要尾部。渲染摘要附一个不超过 512 B 的 `<material>` 入口，仅含当前 manifest 的 `id/hash/revision/storage_complete/read_via`；按需读取 manifest 再找到原文。

对已清理的历史不伪装成模型又看过完整正文：后续摘要看到占位时必须保留“正文已外置，细节未知”的身份；完整原文通过父 manifest / 原材料入口可查。本轮不为摘要自动读回所有历史大正文。

仅在 `agent.state.middle_context['reproagent_compaction']` 保存当前 manifest 指针及冷却状态，不加入每轮 task_state。它属于 SDK 会话内部控制数据，本轮不承诺独立产品级断点恢复。

旧版 `state.summary` 存在但没有程序登记的 manifest 时，不猜测解析渲染文本、不无来源地递归摘要：允许安全清理；滚动摘要暂缓并记录 `legacy_summary_untracked`，到硬线才停止。

### 4. 防止压缩空转

- 每次候选处理区域生成 fingerprint：旧 manifest ID + 所选消息 ID/块序号/内容 hash + 当前契约版本。相同 fingerprint 已尝试过时，不重复归档/摘要。
- 软线下次重试要求：fingerprint 已变，且距上次尝试至少新增 2 个任务步骤，输入相对上次基线增长至少 `max(2048, ceil(0.1*S))` tokens。
- 成功后基线取压缩后 P；失败后取当次处理前 P。契约变化只使区域可重新评估，不免除上述增长与步骤门槛。
- 硬线紧急尝试可跳过冷却，但仍要求 fingerprint 未尝试过、区域安全和预算充足；一次尝试仍不够就停止，不循环。
- 摘要提交至少净减少 `max(512, ceil(0.05*P_before))` tokens；未达到 T 但满足减量可提交。清理只提交真实变短的合法结果。
- 清理已足以降到 S 以下时不摘要；无法选择安全区域、固定提示/保护区占比过高时记录 floor，不强删保护区。

## 源码与落地位置

实际实现根目录 **R**：`C:/Users/Dell/.codex/worktrees/task-state-context/ReproAgent`，现有压缩实现位于该 worktree，报告基线 `a438761` 加未提交修复。执行前重新核对 HEAD、diff 和导入路径，不能按旧 SHA 假定 clean。

本计划保存在 `E:/ReproAgent/docs/superpowers/plans/2026-10-21-token-triggered-rolling-compaction.md`。E checkout 有其他未提交工作，不能在 E 上误写本轮产品实现。下表文件均相对 R；执行需要写入 R 时使用正常权限审批，不绕过。

| 文件 | 改动职责 |
| --- | --- |
| 新增 `src/reproagent/core/context_policy.py` | 纯数值规则：ratio / reserve 校验、S/T/H；不依赖 SDK |
| 新增 `src/reproagent/adapters/agentscope/compaction_state.py` | 来源 manifest、受控滚动状态、冷却判断与小入口 |
| 修改 `src/reproagent/core/models.py` | TaskRequest 使用一致的 ratio 校验 |
| 修改 `src/reproagent/core/protocol.py`、`src/reproagent/core/controller.py` | SDK 初始化错误单次终止，保留正常工具拒绝的可恢复路径 |
| 修改 `src/reproagent/app.py`、`src/reproagent/adapters/agentscope/explorer.py`、`src/reproagent/adapters/agentscope/runtime.py` | 内置入口预检、显式 reserve、初始化错误边界 |
| 修改 `src/reproagent/adapters/agentscope/history_compaction.py` | 清理协调、滚动摘要、原子提交与明确暂缓 |
| 修改 `src/reproagent/adapters/agentscope/middleware.py` | 压缩 hook 与最终硬容量准入一致 |
| 新增/修改下述单测和集成测试 | 覆盖真实 SDK 构造、序列化请求、连续三次摘要及失败边界 |
| 修改原设计及配套验收文档 | 替换一次摘要限制、说明 off 语义变化，保留旧实验事实 |

不修改候选校验/Verifier 判分、源码引用账本、固定补丁隔离、经验检索写入、任务 STATE 默认值、trace HTML 和官方评测分数。不把实验目录 monkeypatch 导入产品。

## 执行准备

- [x] 核对 R 的 HEAD、工作树修改与现有测试文件；记录产品 patch hash，保留他人未提交工作。
- [x] 使用 R 自己的 Python，确认 `reproagent.__file__` 指向 R/src，且 AgentScope 版本为 2.0.9。
- [x] 设置 `$env:REPROAGENT_RG_PATH='E:/ReproAgent/.local/bin/rg.exe'`，验证文件存在；使用新的仓库外 pytest basetemp。保留测试自己注入的 PYTHONPATH，不建立覆盖它的路径规则。

以下命令均在 R 运行，解释器为 `./.venv/Scripts/python.exe`；示例 basetemp 位于 `$env:TEMP`，每次完整复跑使用新目录。此文列的是未来执行命令，不表示已经运行。

## Task 1：统一配置、软硬容量和确定性初始化错误

**Files:** 新增 `src/reproagent/core/context_policy.py`、`tests/unit/test_context_policy.py`；修改上表中的 models / protocol / controller / app / explorer / runtime / middleware 及 `tests/integration/test_agentscope_runtime.py`、`tests/integration/test_agentscope_explorer.py`、`tests/integration/test_cli.py`、`tests/unit/test_history_compaction.py`。

**Interfaces:**
- `validate_context_ratio(value: float) -> float`，常量 `CONTEXT_RESERVE_RATIO=0.1`。
- `ContextLimits(soft: int, target: int, hard: int)`；`context_limits(context_size: int, output_limit: int, trigger_ratio: float) -> ContextLimits`。
- `ExplorerInitializationError(Exception)` 只包裹运行时确定性构造错误，不包裹工具执行错误或模型返回的可修正协议错误。
- 保留 `HistoryCompactor.compact(agent) -> bool`；False 表示未提交历史改动，不代表必须停止。middleware 最终准入统一按 H。

起始数值测试：

```python
def test_context_lines_are_distinct():
    assert context_limits(128000, 4096, 0.15) == ContextLimits(19200, 11520, 123904)

@pytest.mark.parametrize("ratio", [0.07, 0.10, True, float("nan"), float("inf")])
def test_invalid_ratio_fails_before_runtime(ratio):
    with pytest.raises(ValueError, match="context_trigger_ratio"):
        validate_context_ratio(ratio)
```

- [x] **RED：数值与真实 SDK 边界测试。** 拒绝 bool / NaN / Inf / 0.07 / 0.10 / >0.9；0.1001 / 0.15 / 0.8 / 0.9 通过真实 Agent 构造，而不是只构造 ContextConfig。断言 `context_limits(128000,4096,0.15) == ContextLimits(19200,11520,123904)`，S>=H 的配置拒绝。
- [x] **RED：准入与初始化路径测试。** `off` 在 S<P<H 时发出一次真实 MockTransport 请求；P>=H 时不发送；追加临时提醒越过 H 时不发送。CLI 无效 ratio 在 contract 请求前失败，HTTP 次数为 0。受控注入 SDK 构造 ValueError 时 Controller 单次 `EXPLORER_INITIALIZATION_FAILED`，不循环 phase.error；正常工具 ValueError 仍走既有反馈路径。
- [x] 运行 `./.venv/Scripts/python.exe -m pytest tests/unit/test_context_policy.py tests/unit/test_history_compaction.py tests/integration/test_agentscope_runtime.py tests/integration/test_agentscope_explorer.py tests/integration/test_cli.py -q --basetemp "$env:TEMP/ra-rolling-t1-red"`，确认新增断言针对旧行为失败。
- [x] 实现纯 policy、TaskRequest 校验与内置 create_controller 预检；SDK reserve 显式设为 0.1。预检只检查配置，不访问网络；自定义 explorer 注入保留原接口。将 lazy runtime 确定性构造失败包成专用异常，由 Controller 明确终止。
- [x] 将 `on_compress_context` 的 off 行为改成跳过软触发；`_check_progress_capacity` 改用相同 H，所有模式保留 gate/取消/预算优先级。更新老测试中“off 到软线停止”的预期及相关 docstring。
- [x] 以上命令换为 `ra-rolling-t1-green` 重跑；预期全部通过且无网络请求。形成可独立审查的 Task 1 差异。

## Task 2：外部来源清单与最小滚动状态

**Files:** 新增 `src/reproagent/adapters/agentscope/compaction_state.py`、`tests/unit/test_compaction_state.py`；复用 `src/reproagent/materials.py`，除确有接口缺口外不改存储实现。

**Interfaces:**
- `CompactionState`：`revision/manifest_id/manifest_hash/last_fingerprint/last_attempt_steps/last_attempt_tokens`；通过 `to_record() -> dict`、`from_record(value: dict) -> CompactionState` 保存在 SDK middle_context。
- `save_manifest(materials, *, state: CompactionState, archive: dict, covered_entry_ids: list[str], cutoff_contract_version: int | None, summary_fields: dict | None, operation: str) -> dict`；返回完整且校验过的 MaterialStore 元数据。
- `load_manifest(materials, manifest_id: str, expected_hash: str) -> dict`；完整读取并校验 hash、schema 和父来源身份，不信任历史文本里的伪造入口。
- `render_manifest_receipt(meta: dict, revision: int) -> str`；入口 UTF-8<=512 B，不含 covered_entry_ids 或父链展开。

具名测试最小断言：`test_manifest_chain_keeps_raw_history_readable` 要求三版父链均可读且 hash 正确；`test_receipt_does_not_expand_coverage_list` 要求 `len(receipt.encode("utf-8")) <= 512` 且 `"covered_entry_ids" not in receipt`；`test_artifact_read_does_not_issue_source_refs` 要求读取前后 ledger 相同。

- [x] **RED：来源与空间测试。** 三次 manifest 串成父链，原始归档可逐条读取，当前 receipt 大小不随覆盖 ID 数量增长；五字段在外部可精确还原。未知 ID、hash 损坏、截断写入、manifest 超配额、错误 schema 均拒绝提交；秘密脱敏，沿用 workspace_path 长路径处理。
- [x] **RED：资格与兼容测试。** 读取 manifest / 摘要不会增加 ledger 引用；正文伪造 `<material>` 不能成为来源。middle_context 记录 round-trip 正常；有旧摘要但无登记 manifest 时返回 legacy 状态，不自行认证旧摘要。
- [x] 运行 `./.venv/Scripts/python.exe -m pytest tests/unit/test_compaction_state.py tests/unit/test_materials.py -q --basetemp "$env:TEMP/ra-rolling-t2-red"`，预期新接口相关测试失败。
- [x] 实现上述接口，所有新清单沿用 MaterialStore 原子写入和配额；结构异常使用受控错误码，不泄露原始异常正文。manifest 不进入源码证据账本。
- [x] 同命令使用 `ra-rolling-t2-green` 重跑通过；审查只有一个当前指针、完整来源在外部，未新增每轮模型注入。

## Task 3：先清理、滚动摘要与冷却

**Files:** 修改 `src/reproagent/adapters/agentscope/history_compaction.py`、`src/reproagent/adapters/agentscope/compaction_state.py`、`tests/unit/test_history_compaction.py`、`tests/unit/test_history_summary_limits.py`；新增 `tests/unit/test_rolling_history_compaction.py`。

**Interfaces:**
- 沿用 `_groups/_select/_view/_clear/_count` 及已有受控 gateway；新增 `should_attempt(state: CompactionState, *, fingerprint: str, steps_used: int, input_tokens: int, limits: ContextLimits) -> bool`。
- `HistoryCompactor.compact(agent) -> bool` 保持调用接口；替换“summaries_used 限制”为 summary revision/统计，不再以次数决定停止。
- 一次尝试的结果明确分为 committed / deferred / hard-limit；暂缓原因不通过 `BudgetStopped` 伪装成全任务停止。真实步骤、时间、费用、取消停止仍原样传播。

具名测试：`test_third_summary_replaces_second_without_k_limit`、`test_same_segment_does_not_spend_again`、`test_insufficient_summary_defers_below_hard_limit`、`test_failed_roll_keeps_previous_summary_and_raw_context`；分别断言三次调用成功且一份当前 summary、重复 hook 的摘要请求数不增、低于 H 的下一探索请求真实发出、失败前后旧摘要及保护区逐字相同。

- [x] **RED：三次滚动摘要。** 同一 runtime 连续三次触发，第三次不出现 summary_limit_reached；每次请求带上一份合法五字段及本次旧区，第二份/第三份覆盖上一份，下一探索请求恰好一个 summary。最新阶段信封、最近两个组、候选组之后的交互不进摘要；所有工具配对合法。
- [x] **RED：原文、冲突与预算。** MockTransport 捕获摘要原始请求：当前操作使用清理前完整旧区，历史旧摘要标为有损数据；新的已证实失败优先，未验证猜测/未知保持身份。旧清理占位不伪装成完整正文。每次摘要一个逻辑步骤，发布预留不足不发请求，所有实际 usage 仍只有 model factory 记一次。
- [x] **RED：冷却与退让。** 相同 fingerprint 重复 hook 不再归档/调用；改变区域但不足 2 步或增长门槛也不调用；满足两项后重试。未到 T 但减量达标可提交；减量不足保持原 summary，P<H 时下一模型仍可调用。达到 H、不可再安全处理时明确停止。
- [x] **RED：失败原子性。** JSON 为非对象/缺字段/错误类型/截断/超8192B、provider 错误、父 manifest 损坏、材料配额、并发 history_changed：旧 summary 不变、近期原文不丢，原因可见；独立成功的清理可以保留，但其原始归档必须先已验证。取消或真实预算耗尽不转成 deferred；实际历史配对已损坏时明确停止，零后续模型发送。
- [x] 运行 `./.venv/Scripts/python.exe -m pytest tests/unit/test_rolling_history_compaction.py tests/unit/test_history_compaction.py tests/unit/test_history_summary_limits.py -q --basetemp "$env:TEMP/ra-rolling-t3-red"`，确认失败覆盖一次摘要闸门、软线必停和累计元数据三个旧行为。
- [x] 实现规则：冷却通过后才归档；先在副本中清理，足够则提交；否则将旧合法五字段与新旧区作为数据交给默认 schema 的单次 summary 请求。输出额度仍 2048；完整请求也需容纳摘要输出，不删输入凑数。
- [x] 验证摘要与来源、计算减量、准备 manifest；最后再次检查 gate / 取消 / budget 和历史 fingerprint，在无 await 的提交段同时替换 context、summary、middle_context 指针。持久化失败不提交摘要；已写但未引用的材料允许留作诊断，重试不能持续复制同一份材料。
- [x] 以上命令使用 `ra-rolling-t3-green` 通过；确认没有 while-summary、没有累计 pinned 摘要、没有新的原文引用签发路径。

## Task 4：真实 SDK 执行链和可诊断事件

**Files:** 修改 `src/reproagent/adapters/agentscope/middleware.py`、`src/reproagent/adapters/agentscope/runtime.py`、`src/reproagent/adapters/agentscope/history_compaction.py`；修改 `tests/integration/test_history_compaction_runtime.py`、`tests/integration/test_history_summary_repair_runtime.py`；新增 `tests/integration/test_rolling_history_runtime.py`。复用已有 trace 采集，不改 viewer。

**Interfaces:** 继续 `context.compaction` 事件；保留 `code="committed"`，新增 `operation="clear"|"summary"` 和 `summary_generated_this_attempt: bool`。新增 `code="deferred"|"hard_limit"`，`reason` 为程序控制的码。

事件附 `soft/target/hard/input_tokens_before/input_tokens_after/reduction_tokens/summary_revision/manifest_id/archive_id/fingerprint/steps_remaining`；暂缓补下一次冷却条件。字段缺失时不伪造数值。旧 `summary_used` 如保留只作为兼容字段，不能用于统计本次摘要调用。

具名测试：`test_sdk_continues_through_three_rolls_to_controller_candidate` 必须捕获3次摘要及之后的实际探索 HTTP；`test_clear_with_existing_summary_is_not_counted_as_generation` 断言 `operation == "clear"`、`summary_generated_this_attempt is False`、摘要 HTTP 次数不变；`test_final_hard_guard_includes_ephemeral_messages` 断言最终超限时 transport 请求数不增。

- [x] **RED：跨三次摘要继续执行。** 用真实 AgentScope Agent + HTTP MockTransport 走 hook→摘要HTTP→校验→下一探索HTTP，至少3次摘要后仍能发布 candidate，Controller 继续执行和判定；测试输出不能由摘要直接标成 DONE。
- [x] **RED：边界优先级。** clear insufficient / summary insufficient / no_safe_segment 在 P<H 时继续；实际已完成 gate 不被 compaction 覆盖；取消、步骤/时间/费用停止仍生效；最终提醒把输入推过 H 时零超限发送。off/clear/summary 使用相同硬线。
- [x] **RED：观测准确性。** 只清理并沿用旧摘要时 `summary_generated_this_attempt=false`；失败只一条对应结果事件，含精确 error_code / needed_bytes / limit_bytes。成功只一次 committed；任一观测写入失败不替换业务结果。
- [x] 运行 `./.venv/Scripts/python.exe -m pytest tests/integration/test_rolling_history_runtime.py tests/integration/test_history_compaction_runtime.py tests/integration/test_history_summary_repair_runtime.py tests/integration/test_agentscope_model_factory.py tests/integration/test_task_material_tools.py -q --basetemp "$env:TEMP/ra-rolling-t4-red"`。
- [x] 完成 hook 联接与事件；事件只放计数、ID、hash 和受控原因，不复制整段摘要/原始请求。用默认采集容量检查可见性，不靠扩大 trace 容量掩盖问题。
- [x] 同命令使用 `ra-rolling-t4-green` 重跑通过；另以零模型调用的本地诊断报告分解一次真实待发输入的 system / tools / summary / receipt / retained history 大小，明确标注估算，不把软线误称为模型硬窗口。

## Task 5：回归、文档与实验交接

**Files:** 修改 `docs/superpowers/specs/2026-10-19-history-compaction-minimal-design.md`、`docs/reviews/2026-10-19-sdk-history-compaction-acceptance.md`；新增 `docs/reviews/2026-10-21-rolling-compaction-acceptance.md`。旧实验报告只作为来源，不修改旧分数或重写结论。

- [x] 将旧设计中一次摘要/软线必停/1024 输出等过期条目更新为本计划，明确滚动合并是本轮新能力，默认仍 off；记录 ratio 实际合法范围与 off 行为变化。将已批准计划纳入实现 checkout 的文档目录，避免修改只留在另一个 checkout。
- [x] 按顺序运行 Task 1–4 的测试文件，再串行运行 `./.venv/Scripts/python.exe -m pytest tests/unit -q --basetemp "$env:TEMP/ra-rolling-unit"` 和 `./.venv/Scripts/python.exe -m pytest tests/integration -q --basetemp "$env:TEMP/ra-rolling-integration"`。完整组使用新的外部 basetemp，不复用首次失败目录。记录首次失败及重跑结果，skip 原因单列，不能把重跑通过改写成首轮全绿。
- [x] 核对真实导入源码位置、HEAD+diff hash、AgentScope 版本、默认 off 不增加摘要请求；核对没有 fixed 内容进入任何摘要HTTP或材料，read_artifact 不新增源码引用。
- [x] 写验收文档，逐项给出通过/失败/未覆盖、对应测试及证据路径；明确离线通过证明机制可用，不证明复现率或成本改善。
- [x] 写下一轮真实验证的交接条件，**本轮不运行**：先用已有同点快照定点重放，验证至少3次摘要可继续且不提前停止；再挑2个 A 可成功且 B 确实会触发的任务，A/B各2次（最多8次），固定同一产品 patch、模型、硬容量、总预算、fixed 验证和交替顺序。

后续实验需单独明确授权与支出上限；预跑最多2次，不能为了追触发临时扩大调用。A 不得在 S 停止，B 未触发样本保留在 ITT 中并单列；真实完成仍看本地差分和 fixed 通过，必要时另交官方 harness。小样本只决定是否继续研究，不作统计显著提升声明。

首轮继续投入门槛：零无证据完成/引用失效/配对破坏；B 不再因摘要次数或软目标未达而提前停止；触发后的成功数不低于对应 A，且包含摘要请求在内的总 Token 有明确下降。只省钱但更早停止，不算通过；未触发不能证明摘要有效。默认开关不因这8次自动打开。

## 完成标准与排除项

- 第三次及后续摘要不被固定次数挡住，模型上下文始终只有一份当前摘要。
- 清理优先；暂缓压缩不等于终止探索，最终硬准入和任务预算仍有效。
- 不增加每轮 STATE / 大材料清单，不让来源元数据随历史线性占用模型上下文。
- 默认 off、无新依赖、无新的必须记住的 CLI 参数；原文可查、来源可验、引用边界不变。
- 不以“摘要生成成功/多走几步/read_artifact被调用”宣称任务能力提升。
- 不在本计划中实现模型智能选段、向量检索、多摘要并行、自动恢复、材料分片或动态 schema。
- 实现完成先交付可审查差异与验收记录。未获新的授权不提交、push、合并或启动真实模型实验。

## 计划自审

- 已对应：取消 K（Task 3）、软硬分离（Task 1/4）、小入口与来源链（Task 2）、滚动唯一摘要（Task 3）、空转保护（Task 3）、SDK 比例与初始化重试（Task 1）、默认关闭与证据口径（Task 5）。
- 五项 Review Focus 均有具名测试断言；真实语义忠实性仍需后续重放审查，结构校验不能代替语义证明。
- 编写计划时未修改产品代码或运行模型；后续执行结果见下方执行记录，离线通过不等于真实模型收益。


## 执行记录（2026-10-21）

- Task 1–4 已完成并通过相关回归；不计为已完成真实模型实验。
- 独立审查 gpt-6.1-sol 发现1项P2（history_changed使用旧计数），已先RED复现、再修复；42项针对性验收通过。
- Task 5 已完成：最终unit837通过/4跳过，integration477通过/2跳过，合计1314通过/6跳过/0失败。结果见 docs/reviews/2026-10-21-rolling-compaction-acceptance.md。
- 原有未提交基线已备份；本轮未提交、push、合并，未使用Astra或真实付费模型。
