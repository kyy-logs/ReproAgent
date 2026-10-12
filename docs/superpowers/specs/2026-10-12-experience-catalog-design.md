# 分类经验目录与按需加载设计

本设计落实已确认的讨论：Agent 选择 framework/model/workflow 分类，查看类似 Skill 的简短目录，根据适用情境选择经验 ID，再通过工具读取正文。索引只向 Agent 暴露 id/name/description，不要求先出现错误码，也不依靠 Agent 猜中搜索词。正文采用 JSON，不要求 Markdown。本文与同日实现计划配套。

## 存储与唯一事实源

```text
experiences/
  framework/experiences.json
  framework/index.json
  model/experiences.json
  model/index.json
  workflow/experiences.json
  workflow/index.json
experiences.lock
```

每个 experiences.json 合并存储该分类的多条经验，不为每条经验创建文件。文件根为 schema_version=2/category/items；卡保留现有 id/category/tags/summary/detail/source_task_id/evidence_refs/triggers，并允许 name/description。新学习必须生成 name（1–64 Unicode 字符）和 description（1–160 字符，说明什么时候需要加载）。旧卡无需改变 ID，可在内存中从 summary 前 64/160 字符生成兼容目录文本，截短时含省略号；这不是新学出的适用条件。

index.json 是可丢弃的派生缓存，根为 schema_version=1/category/source_hash/items，items 恰好包含 id/name/description。source_hash 对对应正文文件的实际字节计算。缓存使用前还须与正文派生的条目逐项一致，不能仅凭 source_hash 相同信任索引。正文才是唯一事实源，索引不能创造、覆盖或修改经验。读任务不修写索引；缺失、损坏或哈希不符时在内存重建并记录原因。坏正文关闭本任务经验功能，主复现继续。

共用根旁路非阻塞锁：读取三个正文并建立快照与写正文使用同一个锁。锁内重读、校验、去重、测量后原子替换目标分类正文，再更新其索引。正文写成而索引写失败，学习仍是 written，附带 index_update_failed 事件；下次从正文重建。任务只使用启动时的冻结快照，新经验不回流到当前任务。所有访问使用现有 Windows 长路径辅助函数，并检查根、锁及固定子文件的真实路径不进入被复现仓库、修复仓库或输出目录。

## Agent 加载协议

系统提示词只介绍三个分类与使用方法。启用经验时注册六个原业务工具加两个只读经验工具，共八个；关闭时仍六个。

1. `list_experiences(category, cursor?)`：浏览该分类简短索引，无 query、关键词计分或错误码硬过滤。按 ID 排序，默认最多 12 条，完整响应最多 4096 UTF-8 字节；返回 code/category/items/next_cursor，有更多内容时提供任务快照绑定的游标。
2. `read_experience(id)`：只读取本任务成功返回过的目录 ID，返回 id/name/description/summary/detail。不开放任意路径，也不返回源证据文件内容。

空分类正常返回 empty，并计入成功浏览。受控目录结果码为 ready/empty/invalid_category/invalid_cursor/catalog_limit/catalog_budget/response_capacity；读取失败码为 unknown_id/not_displayed/read_limit/response_capacity。一次页面必须完整容纳一个条目和响应结构，否则返回明确容量原因，不截断条目、不越过未展示条目。整个任务最多 6 次成功目录浏览、目录响应累计 8192 字节，正文仍最多成功读 1 次且完整响应最多 4096 字节；经验文本总上界 12288 字节。重复页也计入实际返回字节和浏览次数，不虚称重复响应免费。目录次数、正文读取和已展示 ID 跨探索阶段共享，不因契约修订而重置。失败不消耗成功配额，但照常消耗实际模型/动作预算。

目录工具与正文工具均走原有只读预算、取消、权限与保留发布步骤机制，不获得额外模型步骤。小工具响应上限可以降低页面大小，不能先标记展示再由通用截断器截掉。目录响应成功返回才允许读取其 ID。完整模型输入仍由现有字节/token 守卫限制，不提高全局模型上下文上限。

替换现有首轮关键词自动注入和运行时错误触发自动注入。真实错误及权限结果照常记录；历史 triggers 可保留为来源元数据，但不决定目录可见性。Agent 根据当前情境选择是否浏览，不强制探索阶段、不新增总结或检索模型调用。提示词中的历史经验内容都属于不可信建议，不能代替原版证据或进入 Verifier。

## 写入与迁移

学习时机、独立总结上下文、8192 字节材料预算、30 秒学习预算和最多一次逻辑学习请求不变。新的非空提案严格要求 category/tags/name/description/summary/detail/evidence_ids 七个字段。description 描述有来源支持的适用情境，不能只写“提高效率”或虚构建议已验证。程序验证字段、EID 和证据哈希后生成 ID、引用和受控 triggers，写入分类正文并派生索引。单卡最多 4096 字节，三个正文总计最多 1 MiB；派生索引单独总计最多 1 MiB，读取缓存同样有大小限制。

保留 experience_file 配置名，现有或后缀为 .json 的路径为单文件兼容模式，其他路径为目录模式；现有非 JSON 文件和目录冲突明确拒绝。旧 schema=1 可读，新单文件采用 schema=2，旧库第一次有效追加时在锁内整体升级，新读取版本可读，旧二进制不保证可读。只读不升级。

CLI 新默认为用户目录 `.reproagent/experiences`：已有新目录用新目录；没有新目录但有旧 experiences.json 时继续使用旧文件；两者都不存在用新目录。显式 null 仍完全关闭，环境和任务配置优先级不变。不自动挪走或删除旧库。提供一次性 `reproagent experience migrate OLD_JSON NEW_DIRECTORY`，目标必须不存在；校验后在临时同级目录构建三类数据/索引并验证，再同盘重命名发布，源库不变。中断后的临时目录不能被认为是有效目标。

## 可观测与验收

准备阶段批准并固定库的解析后绝对路径。目录与兼容文件的后续读取/追加，在模式选择与创建锁前重新核对该位置；根或祖先被 junction/symlink 重定向时拒绝，不因新位置内容哈希相同而放行。配置中的初始别名由准备阶段先解析，底层库接口接受固定路径。迁移生成未知长度的临时子目录时提前使用长路径表示，发布和清理的路径比较使用一致表示。

记录 experience.catalog（分类、实际 ID、字节数、分页与结果码）、experience.read、experience.index（受控缓存原因）及既有学习事件。不把“返回目录”“读取正文”“经验起效”混成一件事。 trace 能关联权限、工具输入、目录/正文输出和下一轮模型实际输入，不依赖 trace 才能工作。

验收覆盖无错误码的流程经验发现、分页后读取、未展示 ID 拒绝、跨阶段配额、坏缓存可读、坏正文隔离、并发与长路径、旧库/迁移、冻结评测不写库。离线 SDK MockTransport 验证工具链和实际 wire request；不调用真实 provider，不用 Astra。离线可检索不等于真实复现率提升，后者另做冻结库对照。

2026-10-12 预算修订：落盘卡片与正文读取改为 4 KiB；超限结果附实际字节数、4096 上限与超出量，统计最终程序元数据之后的完整 JSON。目录/学习材料/步骤预算保持原值。旧 2 KiB 卡片和 ID 保持兼容。
