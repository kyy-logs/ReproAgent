# 迁移到 E 盘

日期：2026-10-05。

项目主目录为 `E:\ReproAgent`。后续开发、修改配置和运行命令均使用这个目录：

```powershell
cd E:\ReproAgent
.\.venv\Scripts\reproagent.exe --help
```

迁移前目录为 `C:\Users\Dell\Documents\Codex\2026-10-03\ni`。完整复制了源码、设计与实现文档、工具及目标环境、历史复现结果和本机加密凭据；79,530 个文件、22,935 个子目录、596,437,369 字节均已逐文件核对 SHA256，一致后才切换入口。迁移审计在 `C:\Users\Dell\Documents\Codex\2026-10-05\reproagent-migration`，不包含凭据明文。

工具虚拟环境已在新位置离线刷新并重新安装本地包。已确认解释器为 `E:\ReproAgent\.venv\Scripts\python.exe`，导入包位于 `E:\ReproAgent\src\reproagent`，CLI 启动及 `pip check` 通过。基础 Python 仍使用本机既有的 Codex runtime，位于用户 C 盘缓存；这次迁移没有移动机器级运行时。

当前用户的全局 Claude Code Skill 已更新为 E 盘启动器。在项目外实际执行 `/reproagent check` 成功，使用 E 盘工具解释器。该检查只确认就绪，不认证密钥，也不调用 ReproAgent 模型接口。原全局 Skill 已在迁移审计目录备份。

新目录的完整离线测试结果为 **151 passed、1 skipped，111.34 秒**。迁移后重新核对业务源码、评估文件和加密凭据，内容保持一致；旧地址的 `validators-432` 历史结果可通过 `inspect` 读取，状态为 DONE。

旧根目录被现有进程占用，Windows 不允许整体改名。因此保留了旧根目录，其中 16 个子目录均为指向 E 盘对应目录的 junction；顶层三个文件约 9 KB，是兼容副本。源码、虚拟环境和历史数据实际位于 E 盘。顶层文件不自动同步，请在 E 盘主目录编辑。

已删除 C 盘完整原始备份，清理完成记录见迁移审计目录的 `cleanup.json`。C 盘不再保留完整项目副本。

历史结果和配置中的 C 盘绝对路径没有批量改写，兼容链接使它们继续可访问，也保留了原始证据及其哈希。不要单独删除这些兼容链接，除非已处理历史路径并退出依赖旧目录的会话。

设计文档：

- [产品设计](superpowers/specs/2026-10-05-reproagent-design.md)
- [项目架构](superpowers/specs/2026-10-05-reproagent-architecture-design.md)
- [实现计划](superpowers/plans/2026-10-05-reproagent-mvp.md)
