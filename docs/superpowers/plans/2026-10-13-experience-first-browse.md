# Experience First Browse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 沿用用户已选择的当前会话逐项执行方式。

**Goal:** 有可用经验时，在源码探索之前完成一次模型选择分类的短索引浏览，正文仍按需读取。

**Architecture:** 一个任务级浏览门禁由 Runtime 持有，middleware 在工具权限检查中执行限制，并在下一次模型调用前确认目录结果进入实际 messages。目录工具只提供结果，不能自行宣布门禁成功。沿用现有冻结目录、分页、配额和验收边界。

**Tech Stack:** Python、AgentScope 原生 middleware/tool、pytest、现有离线 MockTransport 与 trace。

**Spec:** `docs/superpowers/specs/2026-10-13-experience-first-browse-design.md`

## Global Constraints

- pending / satisfied / bypassed 为独立任务级状态，不改主任务状态机。
- 只在任务开始约束一次；契约修订不重置。
- 首次目录调用显式选择 framework/model/workflow；成功 empty 也解除门禁。
- 连续两次门禁拒绝后 bypassed；第二次被拒的调用仍不执行，后续调用才恢复。
- 无库、空库、坏库直接正常探索；预算、取消和权限不能被门禁放行覆盖。
- 不提高现有预算，不增加模型调用；首次浏览消耗原有模型/动作预算。
- 历史建议不进入源码证据和 Verifier，不修改写入、触发器和迁移。
- 不调用真实 provider，不使用 Astra 验收。
- 工作区现有八个修改不得覆盖或混入本次提交；执行前确定基线，遇同文件并行修改先核对。

## Review Focus

- 目录函数成功但结果未进模型上下文：不能 satisfied，Task 3 验证。
- 第二次拒绝时副作用意外执行：该次仍拒绝，Task 2 验证。
- 目录权限本身被保留步骤机制拒绝：不应僵持，受控绕过且不覆盖原拒绝，Task 2 验证。
- 选空分类被误解为库为空：只解除首次门禁，其他分类仍可查看，Task 3 验证。
- 模型普通文本结束或协议纠错导致新阶段：不冒充浏览成功，不重置已有状态，Task 3 验证。

## 文件职责

- 新建 `src/reproagent/adapters/agentscope/experience_browse.py`：仅负责任务级状态、拒绝计数和待确认目录交付，不包含存储或 SDK 执行。
- 修改 `src/reproagent/adapters/agentscope/runtime.py`：初始化、生命周期及结构化输入。
- 修改 `src/reproagent/adapters/agentscope/middleware.py`：权限门禁、目录结果暂存、实际输入交付确认及事件。
- 修改 `src/reproagent/core/models.py`、`experience_catalog.py`：notice 增加各类别计数，保留既有字段及调用兼容性。
- 修改 `src/reproagent/core/agent.py`、`prompts/explore.md`：分类用途与首次工具动作说明。
- `tools.py` 仅在必需时调整首次参数验证；不改变后续无分类浏览的兼容能力。
- 新建单元门禁测试与 SDK 集成测试；更新相关 notice/prompt 测试和 `docs/experience.md`。

### Task 1：任务级状态与目录入口信息

**Files:** Create `adapters/agentscope/experience_browse.py`; Modify `core/models.py`, `experience_catalog.py`; Test `tests/unit/test_experience_first_browse.py`, `tests/unit/test_experience_catalog_metadata.py`。源路径均以 `src/reproagent/` 为根。

**Interfaces:**
- `ExperienceBrowseGate(enabled: bool)`，`state: str`，`reason: str`，`refusals: int`。
- `reject() -> bool`：增加拒绝次数，第二次变为 bypassed/noncompliant，返回本次仍须拒绝。
- `bypass(reason: str) -> None`、`satisfy() -> None`：终态不反复切换。
- `ExperienceCatalogNotice.category_counts: tuple[tuple[str, int], ...] = ()`；旧构造保持可用。

- [ ] 写测试：enabled=True 初始 pending，False 初始 bypassed；两次 reject 后 bypassed/refusals=2，不能假报 satisfied；跨阶段使用同一实例不重置。
- [ ] 写 notice 测试：冻结快照分别计算三分类计数，无卡时仍 None，序列化稳定，兼容原 count/categories。
- [ ] 运行新增单元测试确认 RED。
- [ ] 实现纯状态类和 notice 计数，不移动预算常量。
- [ ] 重跑门禁与 metadata 测试确认 PASS，定向提交这些文件。

### Task 2：权限门禁与失败绕过

**Files:** Modify `runtime.py`, `middleware.py`; Test `tests/unit/test_experience_first_browse.py` 及现有 middleware 测试。

**Interfaces:** Runtime 创建一次 `browse_gate` 并传给 `ExplorationMiddleware(..., browse_gate=None)`；默认 None 保持无经验路径行为。权限检查沿用现有 tool-call 关联键。

- [ ] 写测试：pending 拒绝 Read/Grep/Glob/read_experience/write_candidate/revise_contract/request_information 的执行；允许有效的显式分类 list_experiences。
- [ ] 写测试：首次缺 category、带 cursor、非法分类计为门禁拒绝；未 pending 时仍兼容目录原协议。第二次被拒调用副作用为零，第三次走既有权限。
- [ ] 写测试：SDK 原权限拒绝不能被门禁改成 ALLOWED；保留步骤拒绝目录时 bypassed 并保留 RESERVED_FOR_PUBLISHING；取消与全局耗尽仍正常终止。
- [ ] 运行测试确认 RED，然后在现有权限结果计算之后加入门禁；新受控码使用 `EXPERIENCE_BROWSE_REQUIRED`，不伪造既有错误类别或学习触发器。
- [ ] 目录结果 `response_capacity/catalog_budget/catalog_limit` 或真实不可恢复 I/O 导致 bypassed，非法参数/游标保持 pending。仅处理预期读取错误，编程错误不得宽泛吞掉。
- [ ] 重跑相关权限/预算测试确认 PASS，定向提交。工具集仍为已有六个业务工具加两个经验工具，不增加工具。

### Task 3：真实交付与提示词

**Files:** Modify `middleware.py`, `runtime.py`, `core/agent.py`, `prompts/explore.md`; Create `tests/integration/test_agentscope_first_browse.py`。

**Interfaces:** `ExperienceBrowseGate.stage_delivery(call_id: str, payload: str) -> None` 暂存成功目录响应；`confirm_delivery(messages) -> bool` 只在 actual messages 存在对应调用结果且完整 payload 匹配时 satisfy。SDK message 类型适配留在 middleware，纯门禁不依赖 SDK 类。

- [ ] 用现有 MockTransport 写脚本轮次：选择分类 → 完整目录 → 读正文 → 源码探索。断言第二轮 wire request 具有实际目录内容及对应 tool-call ID，并且门禁解除。
- [ ] 写失败交付测试：工具仅生成结果、错误 ToolChunk、截断结果、其他调用同文本均不解除；ready/empty 完整结果均解除。
- [ ] 写分页测试：相关卡在后页，翻页后可读取；所选分类 empty 后仍可换分类；未展示 ID 仍被拒；不改变累计配额。
- [ ] 写生命周期测试：修订契约及普通文本结束后重入探索，状态不重置；无库/关闭路径不要求目录动作。
- [ ] 运行集成测试确认 RED。
- [ ] 在 on_acting 完整成功目录结果暂存交付，不当场 satisfy；在 on_model_call 使用最终发往模型的 messages 确认交付，再执行原模型调用。取消先于调用时不得记录虚假的下一轮输入成功。
- [ ] 提示词列三分类用途、计数、先浏览一个分类以及“无关可继续探索”；首次浏览信息不添加卡正文。实际输入依旧走脱敏和上下文容量守卫。
- [ ] 重跑集成测试与既有 agentscope_experience/runtime 测试确认 PASS，定向提交。

### Task 4：观测、文档与整体验收

**Files:** Modify `middleware.py`, `runtime.py`, `docs/experience.md`; Test `tests/integration/test_agentscope_first_browse.py`、既有观测测试。

**Interfaces:** 事件 `experience.browse_gate`，字段 `state/reason/category/ids/bytes/tool_call_key`（无关联调用时允许省略）；只在初始化与真实状态转移记录，沿用 trace span 关联及脱敏，不建立指标高基数标签。

- [ ] 写测试：成功、empty、noncompliant、容量绕过分别记录真实原因；权限与执行两个观测点只代表一次工具调用。日志写失败不能阻断正常目录结果。
- [ ] 核验门禁状态和目录事件能与实际模型输入关联，正文未读与目录未看不能混淆；无需新增 viewer 页面或模型摘要。
- [ ] 更新 docs/experience.md 的首次浏览流程、分页、预算与绕过语义；预算数字从执行基线核对，不顺带扩容。
- [ ] 定向验证：门禁、catalog/metadata/view、agentscope_first_browse/experience/runtime、experience_catalog_lifecycle 与观测回归。PowerShell 使用仓库 `.venv/Scripts/python.exe -m pytest`；basetemp 指向仓库外可写临时目录，避免默认经验库和项目污染。
- [ ] 跑 `git diff --check`；检查 Verifier/学习材料没有历史经验泄漏。必要时跑全套离线测试一次，失败须报告具体项与文件。
- [ ] 写 `docs/reviews/2026-10-13-experience-first-browse-acceptance.md`，记录提交基线、命令、通过数、失败项和未执行的真实实验；定向提交。

## 真实对照的后续入口

真实调用另需授权，不能当作离线验收的一部分。冻结库后至少比较有库/无库两臂的目录交付、正文读取、重复拒绝、首个有效候选步骤和最终证据等级。出现 bypassed 必须单独统计，不能声称实验已经检验经验效果。

## 执行前检查

计划已自查：门禁接口统一、目录成功与实际交付分离、任务级生命周期明确、两次拒绝不执行副作用、故障绕过不覆盖预算和权限。现有未提交改动与本方案文件重叠，执行前须记录基线并确定隔离方式；不能从旧 HEAD 新建工作树后无声遗漏用户已改的目录行为。
