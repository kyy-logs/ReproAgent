# 经验卡片 4 KiB 预算修复验收

## 改动

- 完整落盘卡片 CARD_BYTES：2048 -> 4096 UTF-8 字节。
- 产品目录视图正文 DETAIL_BYTES：2048 -> 4096 字节；仍最多成功读取一次。
- 目录累计预算 8192 字节、单页 4096 字节、学习材料 8192 字节、模型上下文和动作预算保持原值。旧显式 ExperienceView 的兼容限制保持原值；产品使用 ExperienceCatalogView。
- 提示词建议模型文字约不超过 2500 字节，预留程序引用、规范化标签、触发元数据空间；最终上限仍由程序精确校验，不截断正文、不删来源凑大小。
- invalid_card_size 附 needed_bytes / limit_bytes / exceeded_bytes，表示补齐所有程序元数据之后的最终卡片 JSON。数字进入 LearningResult、CLI 输出、experience.learning 事件、trace 学习汇总，以及 learn 节点的 budget 属性。非大小错误为 null，不回显正文或任意模型字段。

## 验证

离线 RED：5 failed，复现 2308 B 卡被拒、4096 B 边界尚未开放及缺少数字诊断。

GREEN：5 passed，包含单文件/分类库写入与目录读取、4096 B 接受 / 4097 B 拒绝、最终规范化元数据计入测量。

相关完整回归：**142 passed / 1 skipped，exit 0，131.49 秒**。覆盖经验存储、目录、提炼、受控触发、真实 SDK 学习生命周期、CLI/事件/trace 诊断、按需加载闭环、冻结评测。Windows 原生 symlink 权限测试沿用跳过。

命令在隔离工作区根运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_experience_card_budget.py tests/unit/test_experience_store.py tests/unit/test_experience_catalog.py tests/unit/test_learning_validation_errors.py tests/unit/test_experience_learning.py tests/unit/test_experience_triggers.py tests/integration/test_experience_card_budget_diagnostics.py tests/integration/test_experience_lifecycle.py tests/integration/test_experience_catalog_lifecycle.py tests/integration/test_agentscope_experience_catalog.py tests/integration/test_experience_evaluation.py tests/unit/test_experience_catalog_observability.py -q --basetemp C:/Users/Dell/AppData/Local/Temp/reproagent-card-budget-regression
```

本次没有再次运行仓库全量套件，没有调用真实 provider，没有重跑 8906 的真实教学轮或 A/B。2308 B 回归使用同大小的合法离线卡，证明预算与加载机制，不代表已重放用户那张真实卡或保证下一次模型提案一定有效。原版证据、字段 schema、来源哈希与主结果验收规则没有放宽。
