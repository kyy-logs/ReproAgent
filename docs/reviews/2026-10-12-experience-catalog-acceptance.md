# 分类经验目录与按需加载验收

实现分支：codex/experience-catalog；起点 627b375，包含尚未合并的受控拒绝诊断修复。配套规格与计划见 docs/superpowers/specs/2026-10-12-experience-catalog-design.md 与 plans/2026-10-12-experience-catalog.md。

## 已验证的链路

- 两个原生 AgentScope 只读工具：list_experiences -> read_experience -> write_candidate。目录只含 id/name/description；正文读取后才进入下一模型 wire request，其他正文未进入。
- 无错误码也可发现目录中的经验；实际错误和权限拒绝继续记录，不自动注入。
- 任务内冻结快照、跨阶段共享配额、未展示 ID 拒绝、分页完整性、中文 UTF-8 字节边界、小工具上限与成功读取一次限制。
- 分类 JSON、正文唯一事实源、缓存缺失/损坏/过期/伪造内容恢复、缓存写失败不撤销正文、正文替换失败保留旧版、锁竞争、三类共享容量、Windows 长路径。
- 旧库只读不升级，有效追加升级；一次性迁移保留源文件和旧 ID，目标存在拒绝，构建失败不发布部分目标。
- 目录/正文/权限/模型输入的 trace 关联；目录缓存变更不改变冻结评测正文指纹，正文变更停止启动新 case。
- 任务结束独立学习七字段卡，写入分类库，下一任务可浏览；当前任务不见新卡，Verifier 不见经验。

## 测试记录

完整日志在本工作区 `.superpowers/sdd/2026-10-12-experience-catalog/`，最终清理前将复制到持久验收目录。

| 检查 | 结果 |
| --- | --- |
| 元数据 RED -> GREEN | 3 failed -> 51 passed / 1 skipped |
| 分类存储 RED -> GREEN | 9 failed / 1 skipped -> 40 passed / 2 skipped |
| 目录视图 RED -> GREEN | 7 failed -> 13 passed |
| SDK 新链路 RED -> GREEN | 2 failed -> 50 passed（含 runtime/domain/snapshot） |
| 旧 SDK 场景迁移 | 90 passed；修正权限事件名后对应 16 passed |
| 迁移 RED -> GREEN | 4 failed / 1 passed -> 36 passed / 1 skipped |
| 观测 RED -> GREEN | 2 failed -> 15 passed（含冻结评测） |
| 端到端闭环及导出 | 7 passed |
| 附加存储/配额/迁移边界 | 24 passed / 1 skipped |
| 学习生命周期/CLI/元数据/观测 | 52 passed |
| 关闭经验时提示词不宣告工具 | 1 failed -> 16 passed（含原 Agent 策略和目录闭环） |
| 首次全量 | 863 passed / 6 skipped / 1 failed；失败是离线夹具写死旧卡 ID，已改为按实际目录结果选择并通过端到端复测 |
| 最终全量与查看器 | 待最终复跑记录，当前不宣告整分支验收通过 |

## 设计取舍与限制

保留旧 ExperienceView 作为显式兼容 API，但产品 Service 与 SDK 工具链只用 ExperienceCatalogView；旧自动匹配注入不再执行。当前仍只允许成功读取一个正文，可浏览六页目录。旧卡兼容描述只从原 summary 派生，并不代表新推断了触发情境。索引持久化只是便于检查的缓存，读取不依靠它篡改正文。

未执行真实 provider 或付费 A/B，未开启 thinking，验收不用 Astra。本地检索不调用模型服务，但模型工具轮次和经验文本仍占现有步骤/token。离线闭环验证机制，不证明真实复现率提高。此分支尚未 push/合并。
