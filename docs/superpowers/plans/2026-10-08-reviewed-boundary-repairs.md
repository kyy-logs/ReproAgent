# Reviewed Boundary Repairs Implementation Plan

> 执行方式：当前会话逐项实现；使用 executing-plans / test-driven-development。用户已明确要求修复独立检查确认的问题，并拉取最新代码到本地。

**Goal:** 修复探索原始 Issue 缺失、Windows 导出子路径遗漏提升、独立 replay 目录保留窗口失败三个问题。

**Spec:** 用户确认的 [独立检查报告](E:/ReproAgent/docs/reviews/2026-10-08-agentscope-infrastructure-review.md)，架构约束沿用 AgentScope 基础设施迁移设计。

**Scope:** 不重做 SDK 迁移，不实现经验库，不声明模型能力提升。最新 origin/main 已快进到本地主分支；修复在已有 worktree 的 fix-reviewed-boundaries 分支进行，验证后带回本地 main；不自动 push。

## Global Constraints

- 原始 Issue 使用冻结到 input/issue.md 的原始 UTF-8 文本及哈希；探索请求脱敏已知密钥、计入现有 32768 字节输入预算，超限明确停止，不截断关键事实。
- 保留 AgentContext 旧调用的兼容默认值；Controller 产品调用必须传入原始 Issue。修订后的阶段仍使用同一份原始 Issue。
- Windows 文件/目录路径与目标解释器身份分开；独立 replay 只用 stdlib，不 import reproagent 或 agentscope。
- 不回退到错误 cwd，不降低保护、哈希、真实 pytest 执行或固定版隔离要求。
- 保留本地已有未提交文档，不覆盖其他代理的修改。

## Review Focus

1. 外部 issue_file 的事实进入 SDK wire，修订后仍能读取，不能泄漏已知 API key。
2. 大 Issue 加契约超限时零探索 HTTP，不能静默删除 Issue 以继续。
3. glob/rglob 的读、stat、is_file 全部使用正确实际子路径，报告路径不泄漏平台前缀。
4. replay 输出/安装父目录在 248–259 窗口实际执行 pytest，probe 存在；错误 cwd 明确拒绝。
5. SDK 原有预算、阶段完成、引用、独立 replay 以及 wheel 随包资源回归不退化。

### Task 1: 原始 Issue 进入探索上下文

**Files:** models.py、controller.py、adapters/agentscope/runtime.py、prompts/explore.md；tests/integration/test_agentscope_backends.py、tests/integration/test_agentscope_runtime.py。

**Interface:** AgentContext 追加 `issue: IssueDescription|None=None`。Controller 把原始 issue_text/issue_hash 传入所有阶段；runtime 将 issue 的 text/content_hash 序列化到阶段输入，重新验证哈希，脱敏已知 API key，整段计入容量。

- [ ] 外部 Issue 独特事实在分析和探索 wire 都存在；返回/抛出事实不会因契约摘要而消失；已知密钥不进入探索 wire 或 SDK history。
- [ ] 哈希不匹配或整体超限零探索 HTTP；原有旧调用兼容；写测试并运行 RED。
- [ ] 实现最小变化，运行相关 SDK/Controller 回归 GREEN；提交 `fix: preserve original issue facts in SDK exploration`。

### Task 2: 导出器的实际子路径访问

**Files:** exporter.py、tests/integration/test_windows_long_paths.py。

- [ ] 任务根 208/209 字符，先用 atomic_write 写存在的长路径预检日志；导出诊断包并校验日志哈希和 manifest，运行 RED。
- [ ] 所有 glob/rglob 结果进入 I/O 前提升路径表示；相对包路径继续通过 relative_name 计算，平台前缀不进入报告。
- [ ] 跑 Exporter/长路径/报告回归 GREEN；提交 `fix: normalize discovered export file paths before IO`。

### Task 3: 独立 replay 的目录与 cwd 边界

**Files:** resources/replay.py、tests/integration/test_windows_long_paths.py、docs/agentscope.md、README.md、docs/implementation-status.md。

- [ ] 真实导出包在全新原版/修复版目录重跑；输出目录长度 248/259 时 pytest 真执行，原版 1、修复版 0、probe 存在；安装文件父目录在窗口内正常 mkdir，运行 RED。
- [ ] stdlib 脚本补目录保留长度表示，进程 cwd 超过 OS 限制时具名拒绝且不安装候选、不创建输出；不改变解释器链接身份。
- [ ] 定点 GREEN、完整离线套件与 pip check；更新限制说明并提交 `fix: handle standalone replay directory boundaries`。
- [ ] 全分支独立审查；必要修复再走 RED→GREEN 与完整套件；本地 main 快进合入修复并刷新工具环境。不自动 push，不把离线测试当真实模型能力分数。
