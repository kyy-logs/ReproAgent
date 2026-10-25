# 一键接入 Claude Code 与 Codex

`reproagent setup` 把 ReproAgent 的用户级 Skill 和薄启动脚本安装到 Claude Code
与 Codex。外层工具只负责调用现有 CLI 并解释结果；Bug 复现、预算、证据核验和导出
仍由 ReproAgent 执行。安装、检查和卸载都不调用模型。

## 一条命令

```powershell
.\.venv\Scripts\reproagent.exe setup
```

默认安装两个宿主：

| Host | 默认用户级位置 |
| --- | --- |
| Claude Code | `$CLAUDE_CONFIG_DIR/skills/reproagent`，默认 `~/.claude/skills/reproagent` |
| Codex | `~/.agents/skills/reproagent` |

每个目录包含 `SKILL.md` 和当前平台的启动脚本
（`scripts/reproagent.ps1` 或 `scripts/reproagent.sh`）。Codex 另有
`agents/openai.yaml`，其中 `policy.allow_implicit_invocation: false` 禁止隐式触发付费运行。

安装器不写 `AGENTS.md`、`~/.claude/settings.json` 或 `~/.codex/config.toml`。

常用形式：

```powershell
reproagent setup --check                         # 只检查，不写文件
reproagent setup --dry-run                       # 只输出计划，不写文件
reproagent setup --host claude-code              # 只安装一个宿主
reproagent setup --host codex --codex-layout legacy
reproagent setup --uninstall                     # 只卸载本工具拥有的文件
reproagent setup --uninstall --dry-run
reproagent setup --force                         # 冲突文件先备份再覆盖
```

`--codex-layout legacy` 只写入 `$CODEX_HOME/skills/reproagent`
（默认 `~/.codex/skills/reproagent`），不会同时写标准路径；旧版兼容不等于保证
旧宿主支持禁隐式调用。默认 standard 只写 `~/.agents/skills/reproagent`。

## 检查输出与退出码

`--check` 为每个所选宿主分别报告：

- `host_detected` / `detection_sources`：只看可执行文件或已有配置目录，不读取认证文件；
- `entry_status`：`missing`、`installed`、`modified`、`unmanaged_matching` 或 `conflict`；
- `files[].template_current`：受管文件是否与当前模板一致；模板更新不会被误报为用户修改；
- `launcher_importable`：指定 Python 能否在独立进程导入共享入口；
- `model_config_status`：`ready`、`missing` 或 `invalid`；
- `credential_source`：`environment`、`dpapi-file`、`dotenv` 或 `missing`；
- `run_configured`：配置有效且存在一个来源；
- `ready`：所选宿主入口完整、权限具备、入口可导入、运行已配置，且没有未完成事务。

`run_configured` 不代表认证成功、目标环境可用或复现通过。`ready` 同样不证明这些。
`--check` 不调用模型、不解密 DPAPI 凭据。
检查默认使用每个宿主安装时记录的配置路径，不需要重复传入安装参数；不同宿主可使用
不同模型配置。已安装的模型配置被移走时，入口仍报告 `installed`，运行配置报告 `missing`。
`inspect` 只查看已有结果，不要求模型配置文件或凭据存在。

退出码：

- `0`：检查所选宿主全部 ready，或安装/卸载成功；
- `1`：检查有未满足项；
- `2`：参数、冲突、IO、清单损坏或安装恢复错误。

stdout 只有一行 JSON。

## 配置与凭据来源

```powershell
reproagent setup --tool-python <python>
reproagent setup --model-config <model.json>
reproagent setup --repro-root <checkout-root>
reproagent setup --env-file <project/.env>
```

默认值：

- `--tool-python`：当前执行 `setup` 的解释器；生成前在独立进程验证可导入。
- `--model-config`：`--repro-root` 下存在 `examples/model.deepseek.json` 时使用它；
  否则安装仍可成功，但检查为 `model_config_status=missing`，运行时必须补传配置。
- `--repro-root`：仅从带 `src/reproagent/cli.py` 且 pyproject 名为
  `reproagent-local` 的源码 checkout 推断；wheel 安装时为 null，不把
  site-packages 当项目根。
- `--env-file`：显式绑定的项目凭据文件；默认只考虑 `repro-root/.env`，
  不随启动时工作目录搜索其他 `.env`。

存在配置时，凭据来源按以下顺序选择：

1. `api_key_env` 指定的非空环境变量；
2. Windows、`api_key_env` 恰为 `DEEPSEEK_API_KEY`、base_url 为
   `https://api.deepseek.com` 且 repro-root 存在时的 `.local/deepseek.key`
   （只检测存在性，运行期才 DPAPI 解密）；
3. 显式 env-file，然后 `repro-root/.env` 中的同名键；
4. 都没有则 missing。

不跨变量寻找替代密钥，也不把 DeepSeek 加密文件用于其他 provider。高优先级来源
存在但损坏时，运行返回 `credential_load_failed`，不会悄悄换低优先级账号。

安全边界：

- 不读取 Claude Code / Codex 的 OAuth 登录态或订阅凭据；
- 不复制、打印或写入密钥值；安装清单只记录文件所有权与非秘密路径绑定；
- `.env` 只解析精确键名，不做变量插值、`source` 或 `eval`；
- DPAPI 明文只在运行时解密并注入 ReproAgent 子进程，不修改调用方终端环境；
- 测试和检查使用假凭据。

缺少模型配置或凭据不阻止安装，但会输出 `model_config_missing` /
`credential_missing` 警告；真实运行会以受控错误退出，不能伪装成功。

## 幂等、冲突、备份与卸载

所有权清单位于 `~/.reproagent/integrations.json`，记录本工具写入的路径、SHA256，
以及每个宿主绑定的四个非秘密路径（tool Python、模型配置、repro-root、env-file）。
不保存配置正文或密钥。重复执行不会重写未变化的文件或清单。

- 用户已有不同内容的文件：默认整个安装计划拒绝，`--force` 才逐文件备份后覆盖；
- 备份名为 `<文件>.reproagent-backup-<UTC时间>-<随机后缀>`，排他创建，不覆盖旧备份；
- 卸载只删除清单内、路径受限且哈希仍匹配的文件；用户改过、未受管同内容文件和
  备份都保留并报告；
- 卸载不自动恢复 force 之前的文件；输出备份路径供人工恢复；
- `--force` 不能用于卸载。
- 安装路径或控制目录的现有上层组件含符号链接/junction 时拒绝操作；
- 每次写入前及临时文件落盘后核对目标是否变化，发现用户编辑或创建时返回
  `concurrent_file_change` 并回滚此前操作，`--force` 也不绕过；新建文件以不覆盖目标的
  方式发布。更新文件的校验与替换不是跨进程原子比较交换，不提供对恶意并发进程的隔离。

写入使用排他锁 `~/.reproagent/integration.lock` 和事务记录
`~/.reproagent/integration-transaction.json`。普通异常会回滚本次写入；回滚前核对
文件仍是本次写入的版本，不覆盖并发用户修改。进程崩溃可能留下事务记录：

- `setup --check` 报 `transaction_incomplete`；
- 后续写命令拒绝执行，`--force` 不绕过；
- 按事务记录中的路径和备份人工核对、恢复，再移除记录。

## 移动安装目录或更换宿主目录

移动 ReproAgent checkout 后，重新执行 `setup` 并传入新的
`--repro-root` / `--tool-python`。清单按当前用户 home 和显式
`CLAUDE_CONFIG_DIR` / `CODEX_HOME` 校验根路径；路径不再合法的旧记录不会被
用来删除任意文件。需要在原目录变量下显式卸载旧入口。

切换 Codex 布局时，新安装不会静默删除旧布局；`--uninstall --host codex`
会处理清单中登记的两种 Codex 布局。

## 与宿主订阅的关系

ReproAgent 使用自己的模型配置和 API key。接入 Claude Code 或 Codex 不会复用、
读取或修改它们的订阅登录态；宿主的登录状态变化也不会改变 ReproAgent 的凭据来源。
接入只提供调用入口，不改变复现率、模型能力或验收结论。`DONE` 与
`DIFFERENTIAL_VALIDATED` 的区别仍由 ReproAgent 的证据等级决定。

## 故障排查

| 现象 | 处理 |
| --- | --- |
| `tool_python_missing` / `tool_python_import_failed` | 用 `--tool-python` 指向已安装 ReproAgent 的解释器，重新 `setup` |
| `conflict` | 先检查目标文件；确认后 `--force` 备份覆盖，或手动处理 |
| `credential_missing` | 配置 `api_key_env` 指定的环境变量，或使用 `--env-file` |
| `model_config_required` | 传入 `--model-config`，并确保其中有 base_url/model/api_key_env |
| `transaction_incomplete` | 检查 `~/.reproagent/integration-transaction.json` 与备份；人工处理后移除记录 |
| `install_busy` | 另一个安装正在进行；确认后删除遗留锁前先核对没有运行中的安装 |
| 宿主未检测到 | 仅 warning；入口仍可预装，在宿主中显式调用 `/reproagent` 或 `$reproagent` |
