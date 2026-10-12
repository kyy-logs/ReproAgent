# ReproAgent 分类经验目录与按需加载 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking. 本计划按当前会话逐项执行；本计划已完成实现；最终验收见 docs/reviews/2026-10-12-experience-catalog-acceptance.md。

> 后续预算修订：当前卡片/正文为 4096 B，目录预算不变。下列 2048 B 是原实现时的验收基线；预算修复记录另见 docs/reviews/2026-10-12-experience-card-budget-acceptance.md。

**Goal:** Agent 按需浏览三类简短经验目录，选择 ID 后读取正文，实现类似 Skill 的渐进式加载。

**Architecture:** 每分类一个 experiences.json 保存事实，一个 index.json 保存可重建的三字段目录。启动时冻结任务视图；两个只读工具负责目录浏览和正文读取，取代自动匹配注入。学习继续独立上下文，由程序校验并持久化，兼容旧单文件库。

**Tech Stack:** Python >=3.11、标准库 JSON/锁/哈希、AgentScope 2.0.9、现有 pytest/httpx MockTransport/OpenTelemetry；不新增依赖。

**Spec:** [分类经验目录设计](../specs/2026-10-12-experience-catalog-design.md)。实现者必须同时阅读规格及本计划。

## Global Constraints

- 三分类固定 framework/model/workflow；Agent 目录条目只有 id/name/description；正文为 JSON，不为每坑创建文件。
- name 1–64、description 1–160 Unicode 字符；新学习恰好七字段；单卡完整持久化记录 <=2048 UTF-8 字节。
- 三个正文合计 <=1 MiB，派生索引合计 <=1 MiB；只读不修写缓存或升级旧库。
- 每页 <=12 条且完整响应 <=4096 字节；任务成功浏览 <=6 次、目录累计 <=8192 字节；正文成功读取 <=1 次且完整响应 <=2048 字节。任务跨阶段共享配额。
- 学习输入 <=8192 字节，预算 30 秒、一次逻辑请求；触发条件、来源引用和 ID 的程序验证不放松。
- 原版证据、候选发布、Verifier、学习时机、探索自由度、总步骤/token 限制不变。不新增检索模型调用，不开启 thinking。
- 所有文件、锁、临时文件及迁移均走 workspace_path/directory_path 与既有原子写入；拒绝路径逃逸。
- 验收不使用 Astra，不未经授权调用真实 provider。pytest basetemp 必须在所有 Git 仓库之外。

## Review Focus

1. 正文已经提交但缓存更新失败：写入有效，下一任务重建；不返回笼统写入失败。
2. 小响应上限或全中文目录：完整条目分页，不能把不可见 ID 授权给正文工具。
3. 契约修订与探索重建：冻结视图、累计字节和正文配额不得重置。
4. 旧库接近 2048 字节：派生目录元数据不扩写旧卡；读旧库不改变哈希或 ID。
5. 目录内容变更、损坏或符号链接：评测停止启动新 case；缓存变更与正文变更明确区分，不能越界读取。

## 基线、改动范围与模块职责

计划基于 E:/ReproAgent/main 7948abe（2026-10-12 检查）。执行前检查 git status、当前 main、未合并分支；此前错误触发修复如已合并则保留，不回退受控错误与观测。新增目录协议会替代自动注入行为，而不会抹掉其错误诊断。执行前创建/复用隔离工作区。

| 文件 | 职责 |
| --- | --- |
| 新增 src/reproagent/experience_storage.py | 三类正文、派生索引、锁、快照哈希和迁移；兼容单文件 |
| 新增 src/reproagent/experience_catalog.py | 目录投影、游标、展示授权和任务配额，不做语义检索 |
| 修改 src/reproagent/experience.py | 现有卡/提案验证与生命周期；委托存储和目录组件，保留公开兼容入口 |
| 修改 src/reproagent/core/protocol.py、prompts/extract_experience.md | 七字段 schema 与提炼约束 |
| 修改 src/reproagent/adapters/agentscope/tools.py、runtime.py、middleware.py | 注册目录工具、原生 SDK 调用、权限/预算；移除自动注入入口 |
| 修改 src/reproagent/core/controller.py、prompts/explore.md | 首轮仅给分类导航，不塞匹配摘要 |
| 修改 src/reproagent/cli.py | 默认路径和一次性迁移命令 |
| 修改 src/reproagent/otel_projection.py 及必要的观测白名单/查看器模块 | 保留工具名和目录事件，按调用关联 |
| 修改 evals/swt_bench/run.py、必要评测测试 | 冻结多文件正文哈希及运行前后变更检查 |
| 修改 docs/experience.md、examples/task.experience.json | 新格式与旧路径兼容说明 |

不新增 experience_dir 配置字段；experience_file 兼容文件和目录。core/models.py 的 TaskRequest 不需改变；旧 ExperienceSummary/AgentContext 字段先保留默认空值兼容序列化，不做无关删除。learning_material.py 通过 learning_schema 获取新输出契约，不扩充事实采集。report.md 仍只列实际读取的经验 ID。

## Task 1：卡片目录元数据与严格写入契约

**Files:** 修改 experience.py、core/protocol.py、prompts/extract_experience.md；测试 tests/unit/test_experience_learning.py、test_experience_store.py。

**Interfaces:** ExperienceCard 尾部追加 name: str = '' / description: str = ''，保留旧位置构造；旧记录缺两字段有效，新提案必须有两字段。新增目录项数据类 `CatalogEntry(id: str, name: str, description: str)` 放在 experience_catalog.py。`entry_from_card(card: ExperienceCard) -> CatalogEntry` 兼容旧卡，只投影不修改。ID 仍基于原 category/tags/summary/triggers，不因目录标题改变而重算。

- [x] 写失败测试：新提案缺 name/description 返回 invalid_missing_fields 并列缺字段；额外字段、超长度、空白值拒绝；旧五字段模型提案不静默接受；旧存储卡仍有效。
- [x] 运行上述新增测试，确认针对新行为失败。
- [x] 更新 PROPOSAL_FIELDS、learning_schema、校验与提示词，七字段为 category/tags/name/description/summary/detail/evidence_ids；schema 示例、missing_fields 过滤与提示词必须一致。
- [x] 测试旧满尺寸卡仅内存投影，字节与 ID 不变；新卡大小包含 name/description；描述有实际适用情境，不以错误码作为必填字段。保留 EID/哈希拒绝测试。
- [x] 运行两个单元测试文件，全部通过后提交 `feat: define compact experience catalog metadata`。

## Task 2：分类 JSON 存储与可重建索引

**Files:** 新增 experience_storage.py；修改 experience.py；新增 tests/unit/test_experience_directory_store.py。

**Interfaces:** `load_experience_snapshot(path: Path, *, secrets=()) -> ExperienceSnapshot` 与 `append_experience(path: Path, card: ExperienceCard, context) -> str` 的原入口保留。新模块提供 `load_directory_snapshot(root: Path, *, secrets=()) -> ExperienceSnapshot`、`append_directory_experience(root: Path, card: ExperienceCard, context) -> str`、`directory_fingerprint(root: Path) -> str`。缓存诊断通过受控回调传递到 Service，不能改变原 append 字符串返回契约。

- [x] 写失败测试：三类物理隔离；分类文件 schema=2/category/items；索引三字段；正文与目录 ID 一致；缺缓存、损坏缓存、source_hash 不一致均从正文重建且不写盘；伪造正确 source_hash 但改动摘要/ID 的缓存仍被拒绝，条目必须与正文派生投影一致。
- [x] 运行新单元测试确认失败。
- [x] 实现统一根旁路锁与原子正文替换，加载在锁内固定三个文件；索引校验再使用，缓存失败不覆盖正文。snapshot.content_hash 为三类固定相对路径及正文原始字节哈希的 canonical_hash；缺文件使用显式 missing 标记，缓存不参与内容哈希。全库不存在 state=missing，有有效空正文 state=ready；坏正文/读取锁竞争关闭经验并保留受控原因。
- [x] 加故障注入测试：正文替换前失败无变化；替换后缓存失败仍 written，下一读重建；并发追加不丢卡；同 ID duplicate；重复 JSON 字段、非法 schema、跨分类重复 ID、超限、深嵌套拒绝。源记录不允许任意额外字段。
- [x] 加路径测试：根、旁路锁、子文件及符号链接真实目标不得进入受保护目录；锁稳定；长路径读写成功。缓存读上限先测量后解析，三个正文总量限制不能变成每类 1 MiB。
- [x] 运行新测试及旧 store 测试，通过后提交 `feat: persist category libraries with derived catalogs`。

## Task 3：按需目录视图、分页与共享配额

**Files:** 新增 experience_catalog.py；修改 experience.py 的 ExperienceView 委托；新增 tests/unit/test_experience_catalog.py；更新 test_experience_view.py。

**Interfaces:** `ExperienceCatalogView(snapshot: ExperienceSnapshot, *, secrets=())`；`list(category: str, cursor: str | None = None, *, max_bytes: int = 4096) -> dict`；`read(identifier: str, *, max_bytes: int = 2048) -> dict`；`read_ids` 属性。目录结果键固定 code/category/items/next_cursor，正文成功键固定 id/name/description/summary/detail。游标由服务端签发/登记，绑定快照、分类与下一位置，Agent 不可自填文件偏移。

- [x] 写失败测试：无错误码 workflow 经验能够列出；三个分类各自可浏览；目录只含三字段；按 ID 稳定排序；空分类 empty 且计成功浏览；目录码严格使用 ready/empty/invalid_category/invalid_cursor/catalog_limit/catalog_budget/response_capacity，读取拒绝使用 unknown_id/not_displayed/read_limit/response_capacity。
- [x] 运行新测试确认失败。
- [x] 实现按实际完整 canonical_bytes 响应装页，最多 12 条；小 max_bytes 减少数量，首条放不下返回 response_capacity，游标不跳过它。对 secret 脱敏后重新计量。
- [x] 实现任务配额：6 成功页、目录累计 8192 字节；正文一次且 <=2048 字节；每次实际返回都计量，包括重复页。错误结果不耗成功配额。仅完整成功页面的 ID 授权读取；不存在/未展示 ID、第二次读均受控拒绝。
- [x] 测试中文、响应结构开销、边界字节、末页、重复浏览、同一快照跨阶段不重置、全预算用尽；卡正文/历史引用不出现在目录中。保证最大页内容不超过余下目录预算。
- [x] 运行新视图测试与旧视图适用的兼容测试；旧自动选择行为测试改为明确已替代的目录协议，通过后提交 `feat: add bounded skill-style catalog browsing`。

## Task 4：AgentScope 两阶段经验工具与关闭自动注入

**Files:** 修改 tools.py/runtime.py/middleware.py/core/controller.py/prompts/explore.md；必要时修改 explorer.py；新增 tests/integration/test_agentscope_experience_catalog.py；更新已有 experience/triggers/runtime 测试。

**Interfaces:** 新 `ListExperiences` SDK ToolBase，输入 `category: Literal['framework','model','workflow'], cursor: str | None = None`；复用 `ReadExperience` 输入 id。新增 `EXPERIENCE_TOOL_NAMES = ('list_experiences', 'read_experience')`，启用工具表恰好 TOOL_NAMES + EXPERIENCE_TOOL_NAMES。Service.view 在整个任务只创建一次。

- [x] 写失败 SDK MockTransport 测试：首轮只有分类说明；模型主动列 workflow，看到三字段后读取 ID；下一模型 wire request 含目录及正文；未读取的其他正文不可见。
- [x] 运行新增测试确认失败。
- [x] 注册两个只读工具，均检查 budget/cancel，纳入 READER_TOOLS 与 RESERVED_FOR_PUBLISHING；实际调用仍消耗原预算。工具响应直接使用完整已计量结果，不经 `_capped` 截断后开放 ID。
- [x] Controller 停止 summaries(issue) 自动选择；Runtime 停止首轮摘要和 _experience_input 事件匹配注入。保留真实错误/权限诊断及历史 triggers 存储；禁止同一任务同时走自动注入与目录路径。移除已无效的运行时缓存状态，不修改协议纠错、源码引用或候选发布。
- [x] 探索提示词仅介绍三个分类、适用时可 list 再 read、一次正文限制及不可信建议边界；不强制阶段，不自动检索。未启用经验时不展示可调用经验工具说明。
- [x] 测试显式 null 恰好六工具，启用恰好八工具；SDK deny 优先、保留发布步骤拒绝、取消、契约修订共用视图、协议允许列表、没有模型额外请求。经验文本不能成为 revise_contract 可引用源码，Verifier 无经验输入。
- [x] 运行 SDK 目录、domain/snapshot/runtime/experience 测试，通过后提交 `feat: let exploration browse and load experience on demand`。

## Task 5：配置兼容与一次性迁移

**Files:** 修改 cli.py/experience.py/experience_storage.py；更新 examples/task.experience.json；新增 tests/integration/test_experience_migration.py；更新 test_experience_store.py/test_cli.py。

**Interfaces:** `migrate_experience_library(source: Path, target: Path) -> dict`，返回 code/count/source_hash/target_hash；CLI `reproagent experience migrate OLD_JSON NEW_DIRECTORY`。experience_file 保持同名和 Path 类型；现有文件或 .json 后缀为文件模式，其余为目录，现有非 JSON 文件拒绝。

- [x] 写失败测试：新默认目录、旧默认库回退、任务配置 > 环境变量 > 默认，显式 null 全关，相对路径只解析一次；只读旧库不升级。
- [x] 运行新增测试确认失败。
- [x] 实现模式选择，旧 schema=1 读取兼容、新单文件 schema=2；在第一次有效写入时锁内升级旧文件，保留每条旧卡 ID 和实际字段，不把内存回退目录文本写回旧卡。文档明确旧二进制不兼容新 schema，升级不是只读动作。
- [x] 实现迁移到不存在的同盘同级临时目录，校验三类数据/索引后发布；源、目标旁路锁以排序后的绝对路径顺序获取，目标已存在拒绝；发布前再次核对源哈希且源库不修改。禁止符号链接/受保护路径/源目标包含关系，临时残留不当作有效库。
- [x] 测试迁移中断、目标存在、空库、旧满尺寸卡、原 ID/refs/triggers 保留、重复 ID、源并发变化与第二次迁移拒绝。
- [x] 运行迁移、CLI、store 测试，通过后提交 `feat: migrate legacy libraries to category directories`。

## Task 6：观测与冻结评测适配

**Files:** 修改 otel_projection.py、实际匹配的查看器事件投影模块、evals/swt_bench/run.py；新增 tests/unit/test_experience_catalog_observability.py；更新 test_otel_projection.py、tests/integration/test_swt_batch.py/test_experience_evaluation.py。

**Interfaces:** experience.catalog payload = category/ids/code/bytes/has_more；experience.index payload = category/code，code 固定 missing/stale/invalid/index_update_failed；experience.read 保持 id。只成功返回时记录可见 IDs。目录事件不加入源证据白名单，不把任意文本作为 metric 标签。

- [x] 写失败测试：权限请求、实际目录调用、响应、下一轮模型输入按调用关联；list_experiences 不被投影成 unknown；正文读取与目录浏览区别可见。
- [x] 运行新增测试确认失败。
- [x] 在工具结果成功生成后记录事件；诊断事件写失败不能把已提交正文改成失败，也不能重复调用工具或 provider。无 trace 仍能浏览与学习。
- [x] SWT 原 load_experience_snapshot 接口冻结目录 snapshot.content_hash；每 case 前和轮次末检查三类正文，变更/坏正文返回 EXPERIENCE_SOURCE_CHANGED。仅缓存重建/变化不构成正文库变更；启动评测不写索引。
- [x] 测试目录正文任一修改、添加/删除分类文件、坏正文、缓存修改、库位置与输出冲突；两臂使用同八工具、有效空库作为控制时不隐藏工具。区分浏览成功和实际正文进入 wire request，未加载不能声称卡片效果。
- [x] 运行上述观测/评测测试与查看器相关离线测试，通过后提交 `feat: observe catalog loading and freeze category libraries`。

## Task 7：端到端验收与文档

**Files:** 修改 docs/experience.md、必要 README 经验用法；新增 tests/integration/test_experience_catalog_lifecycle.py、docs/reviews/2026-10-12-experience-catalog-acceptance.md。

- [x] 写生命周期测试：已有无错误码流程卡 -> list -> read -> 正常发布/验收；任务结束独立学习七字段卡 -> 分类正文/索引写成 -> 下个任务可浏览；当前任务快照不能读新卡。用 SDK MockTransport，不用真实 provider。
- [x] 运行失败测试，实施仅必要的连接修正，再运行至通过；学习失败不影响主退出码，取消/只读路径无写入，8192 材料和学习预算保持原值。
- [x] 更新文档：目录树、两工具、JSON 例子、分页/配额、迁移命令、默认回退、正文唯一事实源、旧二进制限制及“本地目录不发模型请求，但工具轮次和文本会占预算”。删除已替代的自动注入操作说明。
- [x] 用仓库外 basetemp 运行完整 suite 与查看器检查，检查 git diff --check。只在全部通过后记录命令/exit/测试数；未跑真实模型 A/B 明确写入验收限制。
- [x] 提交 `docs: document progressive experience loading and acceptance`。本计划不包含 push/合并或真实付费 A/B。

## 执行与最终检查命令

以下命令在实现隔离工作区根运行，使用该工作区实际虚拟环境；若不存在，按仓库依赖建立环境。准备一次仓库外临时根，不放在被复现项目或 ReproAgent checkout 内：

```powershell
$catalogTestRoot = Join-Path $env:TEMP ('reproagent-catalog-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $catalogTestRoot
```

各任务 RED/GREEN 命令格式（替换成任务 Files 指定的测试文件，每轮用新子目录）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_experience_catalog.py -q --basetemp (Join-Path $catalogTestRoot 'task3-green')
```

最终验收：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp (Join-Path $catalogTestRoot 'full')
git diff --check
```

不得仅用旧 trace HTML 或手写卡证明功能；必须验证真实 SDK 工具调用以及最终 wire request。失败验收文档应指明具体检查项、失败码和负责模块，不写“全部可用”。成功标准是目录可见、Agent 能按情境选择并加载、存储与来源正确、配额与既有安全边界不变；效果提升另测。

实现接口取舍与审查修复见验收文档；新增窗口/目录跳转和长路径迁移回归。最终源码 27681a0，全量 876 passed / 6 skipped，查看器 8 passed。
