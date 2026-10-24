# AI 工具一键接入设计（Claude Code / Codex）

日期：2026-10-24
状态：待评审
分支：`feat/ai-tool-integration`

## 1. 背景与目标

ReproAgent 目前只有 Claude Code 的项目级接入，且需要手工复制全局 Skill、手工准备凭据；
Codex 没有任何入口。用户希望**一条命令**完成：

1. 把 `/reproagent` 入口安装到 Claude Code 和 Codex 的用户级目录；
2. 自动发现 ReproAgent 自己的 API key 来源；
3. 不复制、不打印、不持久化密钥值；
4. 不改动现有复现逻辑。

成功标准：

- Windows 与 Linux/macOS 上执行 `reproagent setup` 后，Claude Code 能识别 `/reproagent`，
  Codex 能发现并调用同名 Skill；
- `reproagent setup --check` 报告两个 host 的检测结果和凭据来源，且输出中没有任何密钥值；
- 重复执行幂等；`--uninstall` 只删除本工具写入且未被用户修改的文件；
- 现有 `run / inspect / trace / experience` 行为与全部离线测试保持不变。

## 2. 非目标（硬边界）

本设计**不修改复现逻辑**，以下路径不允许出现实现改动：

- `src/reproagent/core/`
- `src/reproagent/adapters/`
- `src/reproagent/runner.py`、`workspace.py`、`verifier.py`、`exporter.py`、`store.py`
- `src/reproagent/prompts/`
- `evals/` 的运行与判分逻辑
- 现有 `run` 的命令行参数、状态机、预算、验证与导出行为

同时不做：

- 不读取 Claude Code / Codex 的 OAuth 登录态或订阅凭据；
- 不新增 Anthropic / OpenAI 模型适配器；
- 不修改 `AGENTS.md`、`~/.claude/settings.json`、`~/.codex/config.toml`；
- 不提供 MCP 服务；
- 不自动安装目标项目依赖或启动服务。

允许改动的文件：

- 新增 `src/reproagent/integration/`
- 新增 `src/reproagent/resources/integration/`
- `src/reproagent/cli.py`：只增加 `setup` 子命令解析与 lazy 分发
- `pyproject.toml`：只增加 `resources/integration/*` 的 package-data
- 新增测试、文档；`README.md` 与 `docs/claude-code.md` 只做接入方式更新

## 3. 命令面

```text
reproagent setup                    # 默认安装 Claude Code + Codex（all）
reproagent setup --check            # 只检测，不写文件
reproagent setup --dry-run          # 打印将写入的路径与凭据来源，不写文件
reproagent setup --host claude-code # 只安装一个 host
reproagent setup --host codex
reproagent setup --uninstall        # 卸载清单内仍匹配的文件
reproagent setup --force            # 冲突时备份后覆盖

# 可选：指定生成到启动器里的绝对路径
reproagent setup --tool-python <解释器>
reproagent setup --model-config <model.json>
reproagent setup --repro-root <ReproAgent 安装根目录>
```

默认值：

- `--tool-python`：当前执行 `setup` 的解释器；生成前验证它能 `import reproagent`。
- `--model-config`：若 `<repro-root>/examples/model.deepseek.json` 存在则使用它；否则不嵌入默认值，
  Skill 正文要求调用时显式传 `-ModelConfig` / `--model-config`。
- `--repro-root`：由 `reproagent.__file__` 向上推断；pip 安装时可显式传入项目根，用于定位
  `.local/deepseek.key` 与 `.env`。
- 凭据变量名从 `--model-config` 的 `api_key_env` 读取；没有模型配置时默认 `DEEPSEEK_API_KEY`。

输出为单行 JSON，与 `run / inspect` 的机器可读风格一致。示例：

```json
{
  "action": "install",
  "hosts": {
    "claude-code": {"detected": true, "skill_root": "C:/Users/x/.claude/skills/reproagent", "files": ["SKILL.md", "scripts/reproagent.ps1"]},
    "codex": {"detected": true, "skill_roots": ["C:/Users/x/.agents/skills/reproagent", "C:/Users/x/.codex/skills/reproagent"], "files": ["SKILL.md", "scripts/reproagent.ps1"]}
  },
  "credential_source": {"kind": "environment", "name": "DEEPSEEK_API_KEY", "value_recorded": false},
  "changed": true
}
```

退出码：

- `0`：安装/卸载成功；`--check` 至少检测到一个 host
- `1`：`--check` 未检测到任何 host
- `2`：参数、路径或写文件错误

`--check` 与 `--dry-run` 保证不创建目录、不写文件。

## 4. 安装位置

| Host | 用户级位置 | 说明 |
| --- | --- | --- |
| Claude Code | `$CLAUDE_CONFIG_DIR/skills/reproagent/`，默认 `~/.claude/skills/reproagent/` | 与现有全局 Skill 路径一致 |
| Codex（标准） | `~/.agents/skills/reproagent/` | Codex 2026 文档中的用户级 Agent Skills 路径 |
| Codex（兼容） | `$CODEX_HOME/skills/reproagent/`，默认 `~/.codex/skills/reproagent/` | 兼容仍使用 `~/.codex/skills` 的版本与工具 |
| 清单 | `~/.reproagent/integrations.json` | 只记录路径、哈希与凭据来源 |

每个 Skill 目录自带：

```text
SKILL.md
scripts/reproagent.ps1   # Windows
scripts/reproagent.sh    # Linux/macOS
```

不写 `AGENTS.md`，不写 host 的配置文件。

## 5. 凭据发现契约

只探测 ReproAgent 自己的 key，按以下顺序：

1. 模型配置 `api_key_env` 指定的环境变量（默认 `DEEPSEEK_API_KEY` / `REPROAGENT_API_KEY`）；
2. Windows 下 ReproAgent 项目根 `.local/deepseek.key`（DPAPI 加密文件，只检测存在性）；
3. 当前项目的 `.env` 或 ReproAgent 项目根 `.env` 中**同名键**（只解析该键，不加载其他变量）。

记录的是来源，不是值：

```json
{"kind": "environment|dpapi-file|dotenv|missing", "name": "DEEPSEEK_API_KEY", "path": "..."}
```

规则：

- 安装器输出、清单、测试日志中不得出现密钥值；发现环境变量存在时，输出前仍做一次脱敏替换。
- 发现来源只代表“可读取”，不证明 key 有效；`--check` 不调用模型。
- 没有来源时仍安装入口，返回警告字段 `"credential_missing": true`，运行前由用户配置。
- 运行期由启动器重新发现并只注入子进程环境；安装器不复制 key，也不生成任何 key 文件。

## 6. 启动器行为

生成两份启动器，逻辑等价、平台适配：

### Windows `reproagent.ps1`

沿用现有 `.claude/skills/reproagent/scripts/reproagent.ps1` 的安全行为：

- 支持 `-Check`、`-TaskConfig`、`-Inspect`，以及 `-ModelConfig`、`-FixedRepo`、`-FixedPython`；
- 工具解释器优先级：`-ToolPython` → 生成时嵌入的绝对路径 → PATH 中的 `reproagent`；
- 凭据优先级：环境变量 → ReproAgent 项目根 `.local/deepseek.key`（DPAPI）；
- 解密值只设置到当前进程环境，`finally` 中恢复；
- 错误信息只报告来源缺失，不输出密钥。

### Linux/macOS `reproagent.sh`

命令形式与 Windows 对齐：

```text
reproagent.sh check
reproagent.sh inspect <结果目录>
reproagent.sh run <task.json> [--model-config <model.json>] [--fixed-repo ...] [--fixed-python ...]
```

- `set -eu`，参数原样转发给已安装的 `reproagent` CLI；
- 工具解释器优先级：环境变量 `REPROAGENT_TOOL_PYTHON` → 生成时嵌入的绝对路径 → PATH 中的
  `reproagent` → `python3 -m reproagent`；
- 凭据优先级：环境变量 → 当前目录 `.env` → ReproAgent 项目根 `.env`；
- `.env` 只按精确键名提取，去掉成对引号，不使用 `eval`、不 source 整个文件；
- 使用 `exec`，退出码与 CLI 一致；错误只报告来源缺失。

Windows 启动器的 `-Check` / `-Inspect` / `-TaskConfig` 与 Unix 的 `check` / `inspect` / `run`
一一对应；Skill 正文按 host 给出正确形式。两种启动器都只调用现有 CLI，不改变其参数与语义。

## 7. Skill 内容

Claude Code 与 Codex 共用同一套行为边界，仅 frontmatter 与调用方式不同：

- Claude Code：保留 `disable-model-invocation: true`，用户显式输入 `/reproagent`；
- Codex：`name`/`description` 必填；支持该字段的版本设置 `allow_implicit_invocation: false`，
  避免自动触发付费运行；不支持时该字段被忽略，正文仍要求显式调用。
  调用方式在正文中写明 `$reproagent` 或 `/skills`。

正文沿用现有 `.claude/skills/reproagent/SKILL.md` 的约束：

- 只调用 ReproAgent CLI，不读目标源码、不预探测环境；
- 不读取、不打印、不复制密钥；
- 不重跑失败任务来掩盖失败；
- `DONE` 不等于差分验证成功；只有 `DIFFERENTIAL_VALIDATED` 证明修复版通过；
- 目标环境依赖由用户提前准备；
- 修复版材料不进入生成输入。

## 8. 幂等、所有权与卸载

清单 `~/.reproagent/integrations.json` 记录每个已安装文件的绝对路径与 SHA256：

```json
{
  "schema_version": 1,
  "installer_version": "0.1.0",
  "hosts": {
    "claude-code": {
      "skill_root": "...",
      "files": [{"path": ".../SKILL.md", "sha256": "..."}]
    }
  },
  "credential_source": {"kind": "environment", "name": "DEEPSEEK_API_KEY"}
}
```

规则：

- 目标文件不存在 → 写入；
- 目标存在，且哈希等于清单记录 → 视为本工具所有，可更新；
- 目标存在，且哈希等于新模板但无清单 → 采纳为本工具所有（首次迁移）；
- 目标存在且内容不同 → 视为用户文件，拒绝覆盖并报告；`--force` 时先备份为
  `<path>.reproagent-backup-<UTC 时间戳>` 再写入；
- 清单记录的文件被用户修改（磁盘哈希 ≠ 清单哈希）→ 重装时保留并报告，`--force` 才备份覆盖；
- 卸载只删除“清单内且磁盘哈希仍匹配”的文件，并清理空目录；用户修改过的文件保留并报告；
- 所有写入使用临时文件 + `os.replace` 原子替换；写入前重新校验目标状态。

## 9. 代码结构

```text
src/reproagent/integration/
  __init__.py
  setup.py          # 命令入口、计划、安装/卸载
  hosts.py          # Claude Code / Codex 路径解析与检测
  credentials.py    # 只读来源发现
  manifest.py       # 清单读写、所有权判定、原子写
  render.py         # 从资源模板渲染 Skill 与启动器
src/reproagent/resources/integration/
  claude/SKILL.md.template
  codex/SKILL.md.template
  reproagent.ps1.template
  reproagent.sh.template
```

`cli.py` 只增加：

```python
setup_parser = sub.add_parser('setup')
# 参数...
...
if args.command == 'setup':
    from .integration.setup import run_setup
    return run_setup(args)
```

不触碰其他分支与函数。

## 10. 测试

全部离线，不调用模型、不访问网络、不触碰真实用户目录。

单元测试 `tests/unit/test_integration_setup.py`：

- 路径解析：`HOME`、`USERPROFILE`、`CLAUDE_CONFIG_DIR`、`CODEX_HOME`；
- Windows / Unix 启动器渲染内容；
- 清单往返与版本字段；
- 首次安装、重复安装幂等；
- 外来文件拒绝；`--force` 备份后覆盖；
- 用户修改过的文件在重装/卸载时保留；
- 凭据来源检测：环境变量、DPAPI 文件存在性、`.env` 精确键；输出不含密钥值；
- `--check` / `--dry-run` 不产生任何文件。

集成测试 `tests/integration/test_integration_setup.py`：

- 在临时 HOME 下执行 `python -m reproagent setup --host all`，断言 Claude Code 与 Codex 的
  `SKILL.md`、启动器均存在且内容正确；
- 再次执行，断言文件哈希不变、无新增备份；
- 修改一个已安装文件后执行卸载，断言该文件保留、其余删除；
- `setup --check` 在安装前后均不写文件；
- 生成的启动器执行 `-Check` / `--check` 等价路径，不调用模型；
- Windows 用 PowerShell 运行、Unix 用 `sh` 运行，CI 矩阵覆盖两平台。

## 11. 文档与发布

- 新增 `docs/ai-tool-integration.md`：一条命令、路径、凭据来源、卸载与故障排查；
- `README.md` 增加“一键接入 Claude Code / Codex”小节；
- `docs/claude-code.md` 保留现有历史验证记录，只把手工安装步骤改为 `reproagent setup`；
- 不修改既有评测报告与结论。

## 12. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| Codex 版本差异导致 `~/.agents/skills` 或 `~/.codex/skills` 只认其一 | 两个路径都安装，清单统一管理 |
| Windows 符号链接权限问题 | 一律复制文件，不使用 symlink |
| 用户已有同名 Skill | 所有权哈希判定；冲突拒绝，`--force` 才备份覆盖 |
| `.env` 解析执行任意代码 | 只按精确键名读取文本，不 source、不 eval |
| 日志泄露密钥 | 安装器不读取值；对已知环境变量值做输出脱敏 |
| 误改复现逻辑 | 文件白名单 + 独立模块 + 全量现有测试作为回归 |

## 13. 验收

- 现有完整离线回归（`tests/unit tests/integration`）保持通过；
- 新增单测与集成测试通过；
- Windows 实机：`reproagent setup` 后 Claude Code `/reproagent check` 成功，Codex 能发现 Skill；
- Unix CI：同一套集成测试通过；
- `--check` 输出无密钥值；`--uninstall` 后用户目录恢复安装前状态（除用户修改文件）。
