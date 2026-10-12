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

完整日志与账本归档到 `E:/ReproAgent/.local/experience-catalog-acceptance/2026-10-12-final/`。最终验收源码提交为 27681a0390186c4aca587c30b5889bf232ba02cc；之后仅更新文档。

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
| 最终全量与查看器 | **876 passed / 6 skipped，exit 0，701.90 秒；查看器 8 passed / 0 failed** |

## 独立审查与修复

使用 gpt-6.1-sol 独立只读审查（非 Astra），Critical 0、Important 2、Minor 1。两项 Important 已在一次修复中完成：

1. 固定库真实位置，后续目录/旧文件读取和追加先复核，拒绝根或祖先 junction 替换；真实 Windows junction 复现包括同字节副本进入修复仓库、冻结评测停止后续 case、旧文件模式在锁创建前拒绝。
2. 迁移在生成临时子目录前提升路径表示，发布及清理使用一致表示；230 字符目标 / 226 字符父目录迁移成功，临时文件无残留。

审查复现 RED：三个场景失败（修正 fixture 固定源码哈希后，冻结评测也确实错误启动了第二 case）；旧文件祖先跳转附加测试 RED。修复 GREEN：61 passed / 2 skipped，覆盖 review repairs/migration/store/directory/evaluation。查看器 8 passed。

Minor 暂缓：直接调用经验 SDK 工具且 tool_response_bytes=10 时，受控错误 JSON 可超过该字节上限；如此小上限通常无法让完整任务的初始上下文进入探索。成功目录/正文始终按完整 UTF-8 JSON 上限计量并授权，本项不影响正常任务的成功加载边界。

审查未判断真实效果/live provider、最终全量、POSIX 锁压力、电源故障和文件系统操作内部同时攻击；真实效果需另做授权冻结 A/B，最终全量另跑，锁沿用既有实现，操作内部竞争不在本次已验证保证内。无权限的 native symlink 测试跳过，但本次真实 Windows junction 场景已覆盖。

## 设计取舍与限制

保留旧 ExperienceView 作为显式兼容 API，但产品 Service 与 SDK 工具链只用 ExperienceCatalogView；旧自动匹配注入不再执行。当前仍只允许成功读取一个正文，可浏览六页目录。旧卡兼容描述只从原 summary 派生，并不代表新推断了触发情境。索引持久化只是便于检查的缓存，读取不依靠它篡改正文。

未执行真实 provider 或付费 A/B，未开启 thinking，验收不用 Astra。本地检索不调用模型服务，但模型工具轮次和经验文本仍占现有步骤/token。离线闭环验证机制，不证明真实复现率提高。此分支尚未 push/合并。

## 最终通过标准

2026-10-12 本地 Windows 最终完整验收通过。命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp C:/Users/Dell/AppData/Local/Temp/reproagent-catalog-final-full
node --test tests/frontend/trace_viewer.test.mjs
git diff --check 627b375..HEAD
```

6 项跳过保留既有平台/权限条件；相关 native symlink 权限不足，但本次目录跳转回归使用实际 Windows junction，未跳过。目录正文/索引/只读工具/独立学习/迁移/观测/冻结评测功能与边界通过；独立审查两项 Important 已修复。未执行真实模型 A/B，不据此声称复现率提高。

执行取舍：PowerShell-native 账本替代 POSIX 脚本，没有产品行为变化；旧显式 ExperienceView 保留兼容，成本是额外维护，产品不走自动检索；审查安排在最终长回归前，验收没有放宽。平台锁压力/电源故障/同时攻击操作内部竞态未验证，保留既有锁实现，保证范围以已运行的顺序跳转回归为准。
