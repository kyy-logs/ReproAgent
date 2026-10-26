# News / 更新日志

Product updates. For commit-level history, see [main commits](https://github.com/kyy-logs/ReproAgent/commits/main).

产品更新。逐提交记录见 [main commits](https://github.com/kyy-logs/ReproAgent/commits/main)。

## 2026-10

- **2026-10-25 · `CHANGE`** — Trim redundant top navigation [fe8360c](https://github.com/kyy-logs/ReproAgent/commit/fe8360cb77ea44522ae06a8915f317a6b1f59e1a)
- **2026-10-25 · `FIX`** — Repair Python fallback installer and cover setup in CI [6a40d02](https://github.com/kyy-logs/ReproAgent/commit/6a40d027c521f9152b1546166a90cd23c2b5789b)
- **2026-10-25 · `FEAT`** — Windows one-click setup for Claude Code / Codex: finds or installs Python 3.11+, creates a private tool environment, writes user-level entries, runs setup checks, and adds a read-only target-project check. [Install guide](windows-install.md)  
  **中文**：Windows 一键安装接入 Claude Code / Codex，自动查找或安装 Python 3.11+、创建私有环境、写入用户级入口、运行 setup 检查，并可只读检查目标项目。[安装说明](windows-install.md)
- **2026-10-25 · `DOCS`** — New product page and user-facing READMEs in English and Chinese. [Product page source](../site/index.html)  
  **中文**：新增产品页和中英文 README。[产品页源码](../site/index.html)
- **2026-10-15 · `FEAT`** — Verifier feedback now carries the cited evidence text and structured evidence gaps, making failures easier to locate. [Output notes](technical-highlights-and-output.md)  
  **中文**：验证反馈携带引用证据原文和结构化证据缺口，失败原因更容易定位。[输出说明](technical-highlights-and-output.md)
- **2026-10-13 · `FEAT`** — The experience library supports task-scoped catalog browsing and on-demand loading. [Experience library](experience.md)  
  **中文**：经验库支持按任务浏览目录、按需加载经验。[经验库](experience.md)
- **2026-10-10 · `FEAT`** — Local trace viewer with a step tree, timeline and tool-call inspection; nothing is uploaded. [Observability](observability.md)  
  **中文**：本地 trace 查看器提供步骤树、时间线和工具调用检查，不上传网络。[可观测性](observability.md)

<!-- news-auto: 5646f637286b5d744174c17add8e940746fdf9a8 -->
