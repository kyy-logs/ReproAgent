# ReproAgent Task State and Material Offload Design

日期：2026-10-18。基线：origin/main 0c17d5387faefee3e5e0bfd0b0a7fa8fc263c352。

## 目的与范围
让探索 Agent 每轮获得明确的当前进度；超长工具输出保存后返回有界摘录和可读入口。复用 Controller、契约、候选、执行和 Verifier，原有记录仍是验收依据。用户已要求写计划后在当前会话直接实现。

## 任务状态
任务根目录新增 STATE.json：schema_version、revision、task_id、goal、status、stage、contract、facts、hypotheses、latest_candidate、blockers、todos、materials、budgets、completion。TaskProgress 是程序维护的派生视图；TaskStore 写入现有类型记录后增量更新它。契约改变清除当前候选、事实和完成引用；历史记录不删除。模型没有直接写状态或完成标志的工具。第一版 hypotheses 为空，不自动提炼模型猜想。缺失事实使用 unknown/空值，不从聊天推断。

状态以 atomic_write 保存，失败不替换旧文件；现有业务记录优先保存。STATE 不提供恢复执行的承诺。状态恢复只接受已校验结构；读取材料不信任 STATE 里的任意路径。预算取当前 BudgetTracker 的实值；磁盘预算仅是最后观察时刻的快照。DONE 和 evidence_level 仅投影 TaskResult。

## 材料存储与安全
新增 materials/，内容及元数据均由程序登记。每项最多 256 KiB，每任务最多 4 MiB、128 项；达到任何上限显式返回不完整/存储不可用，禁止承诺可读取未保存内容。不复制不受限日志。正文先脱敏，再哈希寻址、UTF-8 安全截断。元数据记录原始字节数、保存字节数、hash、tool/kind、上游完整性、存储完整性、描述。

原始执行的 stdout/stderr、结构化执行结果和判定可登记为材料；fixed 执行及其后续判定不提供给探索 Agent。引用路径必须在 TaskStore 内、哈希校验成功，禁止任意文件/隐藏修复读取。read_artifact 只接受已登记 ID、1 起始行 offset、1..200 limit；返回大小不超过现有 tool_response_bytes，并返回 next_offset。拒绝路径穿越、损坏、被替换内容和不合法参数。所有路径走 workspace_path/safe_child。摘要/材料文本是数据，不是 system 指令，也不自动授予源码引用资格；源码引用仍要通过原有 Read/Grep 证据账本。

## 大输出呈现
Read/Grep/Glob 在现有工具截断之前登记 SDK 实际返回的文本（上游可能已截断，不能声称是源文件全文）。工具返回保留现有有界原文和 evidence sidecar，并增加材料 ID、完整性、读取方式。其余错误/结果使用程序提取的停止原因、退出码、collected/executed/probe_complete 等字段作为摘要；不调用模型总结。存储不可用时保留受控截断提示和原有业务结果。小输出不必卸载。

## 每轮上下文
ContextBuilder 在 on_model_call、任何模型执行之前刷新状态及预算，将一个至多 4096 UTF-8 字节的当前视图作为独立 UserMsg 注入本次 input_kwargs；不追加进 SDK 的持久历史。保留当前契约、最新结果、阻塞、预算、材料数量和最近材料入口，详细事实/目录按预算逐组省略并标注。外部文本不进入 system。现有经验浏览/预算提示、模型输入容量检查、输出上限继续生效；上下文不够就明确停止，不拆散 tool-call/result、不增加压缩模型调用。分析和 Verifier 输入保持原有隔离。

## 可观测与兼容
STATE 更新和材料登记可由本地文件核查。context.prepared 事件记录 revision/bytes/hash，不复制敏感正文；原有 HTTP trace 仍记录最终请求。工具集新增 read_artifact（只读），加入现有预留发布步骤的读工具集合，三种权限结果不变。旧任务无需迁移，没有材料时工具返回 unknown_artifact。

## 验收
测试原始业务记录驱动状态且终态不可由模型伪造；新契约使旧当前证据失效；超长中文/密钥/损坏/越界/容量/分页；原始执行可读但 fixed 隐藏；真实 SDK + MockTransport 的下一轮 HTTP 请求包含最新状态和工具摘录，只出现一份当前视图；工具调用配对和原有权限/预算/引用回归通过。无真实 provider 费用，不使用 Astra。全套 pytest 验收。

## 不包含
通用任务编辑器、自动完成 Todo 的模型、摘要模型、复杂相关性评分、跨任务恢复、删除原有历史、源码写权限、额外阶段探索约束。
