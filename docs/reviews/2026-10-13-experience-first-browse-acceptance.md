# 首次经验目录浏览验收

验收状态：通过。最终完整离线回归 913 passed / 6 skipped，752.42s，退出码 0。

## 基线与交付

- 工作树：`C:/Users/Dell/.codex/worktrees/experience-first-browse/ReproAgent`。
- 分支：`codex/experience-first-browse`，当前产品/测试提交 `813fe2b`。
- 起点 `cdc13e9`；用户已有八个文件修改和已确认设计/计划单独保存为 `51d4826`，不覆盖 E 盘 main 工作区。
- 门禁状态实现 `b78c7a8`，运行时接入 `03cede4`，权限筛选兼容 `25a1dcb`，无库观测修正 `25af32e`。

## 检查表

| 检查 | 结果与依据 |
| --- | --- |
| 首次明确选分类 | SDK 测试验证缺分类被拒，显式分类成功 |
| 工具副作用控制 | 连续两次候选发布均被拒且没有候选文件，第三次可正常结束 |
| 完整结果交付 | 匹配实际调用 ID、工具名、成功状态与完整文本；下一轮 wire request 含目录 |
| 每轮当前状态 | 新的临时 SystemMsg 显示已满足状态，不累积到 SDK 历史；无工具时不追加提示 |
| 分类与分页 | 空分类解除门禁但允许切换；真实 SDK 翻后页后成功读取卡片 |
| 容量与 I/O | 目录容量不足、预期 OSError 均 bypassed，不冒充成功加载 |
| 原权限与发布预留 | SDK DENIED 和 RESERVED_FOR_PUBLISHING 不被覆盖；门禁拒绝仍进入 DENIED viewer 筛选 |
| 跨阶段 | 契约版本变化后状态与读取配额不重置 |
| 观测 | pending/satisfied/bypassed、原因和调用关联可见；无库也记录 bypassed/unavailable；观测失败不阻断工具 |
| 证据边界 | 沿用旧回归：历史正文不能作为源码引用，不进入 Verifier；经验库冻结/只读仍有效 |

## 定向验证

最近门禁/触发器/观测组合：52 passed，15.78s。旧失败的冻结库/锁占用/拒绝链组合：9 passed，14.85s。Claude 启动器：6 passed，8.30s。

使用真实 AgentScope Agent、middleware 与 toolkit，经 MockTransport 记录实际 HTTP 请求；不是只 mock 门禁返回值。开发过程执行 RED→GREEN：缺模块、权限未生效、当前状态缺失、trace 未打点、观测异常传播、权限筛选分类错误、目录 I/O 未绕过均先观察到失败再修复。

首次全套运行为 15 failed / 896 passed / 6 skipped：六项由新工作树缺少本地 `.venv` 引起；九项旧模拟脚本跳过首次目录浏览。已补齐隔离环境并更新模拟脚本，保留原断言所检验的真实错误/锁占用/验收结果，不放宽产品规则。

隔离 `.venv` 使用已安装的本地依赖及其 Windows 初始化，工作树 src 优先；未联网下载或改变主项目环境。原完整日志及修复前失败记录保存在 `.local/experience-first-browse/`。

最终全套命令：

```powershell
.venv/Scripts/python.exe -u -m pytest -q --basetemp C:/Users/Dell/Documents/Codex/tmp-first-browse-acceptance
```

最终结果为 913 passed / 6 skipped，752.42s，退出码 0；日志为 `.local/experience-first-browse/acceptance.log`。最终代码修正后全套重新执行，原失败项均已通过，没有仅凭定向回归宣称全套成功。

## 独立审查与边界

独立审查使用 gpt-6.1-sol，没有使用 Astra。初审没有 Critical/Important，指出无库初始决定未记录的 Minor；修正经 RED→GREEN 验证，复查结论 ready，无剩余可操作问题。

未调用真实 provider，未做真实 Bug 或 A/B；离线通过仅证明加载和门禁机制可工作，不能证明复现率提升。没有 push、PR 或合并。历史经验仍是建议，正文读取仍由模型按需选择；两次拒绝后的受控绕过必须在真实评测中独立统计。
