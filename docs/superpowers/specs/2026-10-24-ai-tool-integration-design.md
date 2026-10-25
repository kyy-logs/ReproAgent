# AI 工具一键接入设计（Claude Code / Codex）

日期：2026-10-24
状态：已按评审意见修订，供实现计划引用；尚未实现
分支：`feat/ai-tool-integration`（设计基线 `cef94f1`；执行前重新确认 HEAD）

## 1. 目标与边界

执行 `reproagent setup`，给 Claude Code 与 Codex 安装用户级 ReproAgent Skill 和启动脚本。外层工具只负责调用现有 CLI 和解释结果，复现、预算与验收仍由 ReproAgent 执行。安装、检查和卸载都不调用模型。

成功标准：跨项目可发现入口；重复安装幂等；检查结果区分入口、解释器、配置与凭据来源；冲突和失败不覆盖用户修改；发布 wheel 安装后也能工作。

禁止修改：`src/reproagent/core/`、`adapters/`、`prompts/`、runner/verifier/workspace/exporter/store，以及 `evals/`、现有 run 参数和复现/验收语义。不读 Claude/Codex 登录态，不新增模型适配器、MCP、第三方依赖，不写 AGENTS.md 或宿主配置，不安装目标依赖或启动服务。

允许修改：

- 新增 `src/reproagent/integration/`、`src/reproagent/resources/integration/`；
- `src/reproagent/cli.py` 只新增 setup 参数注册与 lazy 分发；不重构既有入口；
- `pyproject.toml` 只追加集成资源 package-data，保留现有规则；
- 新增测试和文档；README、`docs/claude-code.md` 只更新接入指引；可新增独立离线跨平台测试 workflow。

现有 `.claude/skills/reproagent/` 与其启动器不在本次修改范围；它们是行为参考，不被新安装器静默覆盖或迁移。

## 2. 命令与默认值

```text
reproagent setup                              # 默认安装两个宿主
reproagent setup --host claude-code            # all / claude-code / codex，默认 all
reproagent setup --host codex
reproagent setup --host codex --codex-layout legacy
reproagent setup --check                      # 只检查，不调用模型
reproagent setup --dry-run                    # 只输出计划，不写入
reproagent setup --uninstall                  # 只卸载清单中归本工具所有的文件
reproagent setup --uninstall --dry-run
reproagent setup --force                      # 安装冲突文件前备份

reproagent setup --tool-python <python>
reproagent setup --model-config <model.json>
reproagent setup --repro-root <checkout-root>
reproagent setup --env-file <project/.env>
```

- `--check` 与 `--uninstall` 互斥；`--dry-run` 可修饰安装/卸载，不与 `--check` 组合。
- `--force` 仅用于安装，不允许强制卸载用户修改文件；`--codex-layout standard|legacy` 仅用于包含 Codex 的安装，默认 standard，不同时复制两套。
- `--tool-python` 默认 `sys.executable`，验证该绝对路径可以在独立进程导入 ReproAgent。检查不联网。
- `--repro-root` 优先显式指定；自动推断仅接受有 `src/reproagent/cli.py` 与项目名为 `reproagent-local` 的 pyproject.toml 的源码 checkout。不能把 site-packages 当项目根。无法推断时为 null。
- `--model-config` 优先显式指定，否则使用确认过的 repro-root 下存在的 `examples/model.deepseek.json`；都没有则允许安装，但配置状态为 missing，运行时必须补传配置。
- `--env-file` 是可选、显式指定的项目凭据文件，绑定为绝对路径；默认只考虑 repro-root/.env，不随启动时工作目录搜索其他 .env。
- 安装时解析并固定路径，支持空格、中文、引号及 shell 元字符；不把用户路径直接拼接成可执行 shell 文本。显式指定但无效的路径报参数错误，不能静默换解释器或模型。

stdout 仅一行 JSON；退出码：安装/卸载成功为 0；检查有未满足项为 1；参数、冲突、IO、清单或安装恢复错误为 2。凭据/配置缺失不阻止安装，但报告 warning；缺失配置不能让真实 run 成功。

## 3. 路径与宿主发现

| 用途 | 路径 | 规则 |
| --- | --- | --- |
| Claude Code | `$CLAUDE_CONFIG_DIR/skills/reproagent`，默认 `~/.claude/skills/reproagent` | 用户级 |
| Codex 标准 | `~/.agents/skills/reproagent` | 默认唯一 Codex 安装位置 |
| Codex 旧版兼容 | `$CODEX_HOME/skills/reproagent`，默认 `~/.codex/skills/reproagent` | 仅显式 legacy 安装；不承诺所有旧版均支持禁隐式调用 |
| 所有权清单 | `~/.reproagent/integrations.json` | 文件路径、哈希、非敏感来源描述 |
| 并发锁/故障记录 | `~/.reproagent/integration.lock`、`integration-transaction.json` | 仅写入操作可创建 |

路径按当前用户 home 和显式宿主目录变量解析；宿主可执行文件或已有配置目录仅作为发现线索，不读取认证文件。检测不到宿主也可预装入口，报告 warning，不能把新建目录当作检测到宿主的证据。

检查和卸载读取清单中的所选宿主记录，包括其已登记的两种 Codex 布局；旧布局不因新安装被静默删除。自定义宿主根目录变更时，旧目录需在原目录变量下显式卸载；不能凭清单中的任意绝对路径删除文件。

每个目录：`SKILL.md`、当前平台的 `scripts/reproagent.ps1` 或 `scripts/reproagent.sh`；Codex 另外包含 `agents/openai.yaml`。Unix 脚本设为用户可执行；检查也核对执行权限。不使用 symlink。

## 4. 同一套配置与凭据规则

安装检查与运行启动复用 `integration/credentials.py`，不各自实现一套优先级。读取模型配置的 base_url/model/api_key_env；api_key_env 缺省沿用产品默认 `REPROAGENT_API_KEY`，必须符合 `^[A-Za-z_][A-Za-z0-9_]*$`。无模型配置则报告 `model_config_required`，不猜一个 DeepSeek 变量。

存在配置时，按顺序选来源：

1. 配置变量中非空的现有环境变量；
2. Windows、api_key_env 恰为 `DEEPSEEK_API_KEY`、base_url 为 HTTPS 且主机名恰为 `api.deepseek.com`、repro-root 存在时，考虑 `.local/deepseek.key`；
3. 显式 env-file 的同名键，然后 repro-root/.env 的同名键；相同路径去重；
4. 都没有则 missing。

不跨变量找替代密钥，不把 DeepSeek 加密文件注入其他 provider。高优先级来源存在但损坏时，运行报 `credential_load_failed`，不偷偷换低优先级账号。

来源描述示例：

```json
{"kind":"dpapi-file","name":"DEEPSEEK_API_KEY","path":".../.local/deepseek.key","present":true,"loadability":"unknown","authenticated":false}
```

- setup/check 只做来源与结构检测，不解密 DPAPI，不测试认证；文件存在不等于可解密、可读取或有效。
- .env 仅解析精确键名、非空值和成对单/双引号；忽略空行/整行注释，可接受 export 前缀；不做变量插值、转义执行、source 或 eval。同名重复/目标值引号不闭合报 `invalid_credential_file`，不输出原行；不注入其他键。
- 不承诺安装器“不接触任何值”：环境变量和 .env 的检测可能在内存接触文本；禁止的是返回、打印、复制到安装文件/清单/日志或持久化明文。DPAPI 明文仅运行时解密。
- 输出只用白名单字段与稳定错误码，不输出原始异常、环境字典或配置内容；测试用假值，不打开用户真实 key/.env。
- 运行时重新选择来源，仅为 ReproAgent 子进程构造环境；不修改调用方终端环境，不传密钥命令行参数，不复制凭据文件。

## 5. 启动器与共享运行入口

两种脚本仅做平台参数适配、选择工具 Python，再调用 `python -m reproagent.integration.launcher`；配置解析、来源选择、检查与 CLI 转发共用 Python 实现，不复制到 shell。脚本中的 base64 绑定只允许 tool_python/model_config/repro_root/env_file 四个路径字段，不嵌入模型配置正文、base_url、环境字典或凭据值；运行时重新读取模型配置。

工具 Python 优先级：Windows 显式 `-ToolPython` → `REPROAGENT_TOOL_PYTHON` → 安装时记录的解释器；Unix 显式 `--tool-python`（仅置于 action 前）→ 同名环境变量 → 安装时记录的解释器。选中路径失效则报错，重新 setup 或显式覆盖；不悄悄落到其他 PATH 安装。

Windows 保留 `-Check`、`-Inspect <dir>`、`-TaskConfig <task.json>`，支持 `-ModelConfig`、`-FixedRepo`、`-FixedPython`、`-ToolPython`、`-ReproRoot`、`-EnvFile`。Unix 为：

```text
reproagent.sh check [--model-config <model.json>]
reproagent.sh inspect <task-directory>
reproagent.sh run <task.json> [--model-config <model.json>] [--fixed-repo ...] [--fixed-python ...]
```

- `inspect` 不读取凭据、不要求模型配置；`check` 只返回 readiness，不解密或调用模型；`run` 才加载选定密钥。
- Python 入口以 argv 列表、`shell=False` 调用所选解释器的 `-m reproagent run --config ... --model-config ...` 或 inspect，原样传递路径与 fixed 参数；保持子进程退出码。现有 CLI 仍负责其完整配置/业务校验。
- Windows DPAPI 复用现有启动器的 CurrentUser 安全语义：只支持默认 ConvertFrom-SecureString 的 DPAPI 十六进制文件，使用系统 CryptUnprotectData，解码 UTF-16LE；不支持 AES/其他用户格式则受控失败，不输出解密内容。不实现新的加密算法、不写回 key 文件。
- Unix wrapper 使用 `exec`；所有平台均不启用命令回显。生成的字符串字面量需要平台正确转义，不能依赖 JSON 转义作为 shell 转义。
- 安装到用户目录后不能再依赖 `.claude/.../../../../..` 找项目；不能使用 `${CLAUDE_SKILL_DIR}` 作为 Codex 变量。

## 6. Skill 与调用策略

Claude Code：沿用 `disable-model-invocation: true`，用户显式 `/reproagent`。

Codex：SKILL.md 只放标准 name/description 等元数据；禁隐式调用写到 `agents/openai.yaml`：

```yaml
policy:
  allow_implicit_invocation: false
```

正文说明 `$reproagent` / `/skills`；只有显式用户请求才运行付费任务。兼容旧目录不等于保证旧宿主支持此 policy；须报告兼容性未验证，不能把正文要求当作宿主硬权限保证。

正文复用既有 CLI 边界，但区分输入：有现成 task JSON 时只读配置并立即运行，不预读目标源码/预探测环境；自然语言输入时可识别项目结构、准备 Issue/task JSON，缺信息就问，不代替内层 Agent 复现。不读凭据、不重复运行掩盖失败、不自动装依赖、不暴露 fixed 给生成输入；DONE 与 DIFFERENTIAL_VALIDATED 分开解释。

全局 Skill 内不得保留指向仓库的失效 `../../../docs` / `examples` 相对链接。提供自包含参数说明和最小 task JSON 字段说明，使用绑定的路径和实际 Skill 脚本目录，不虚构宿主变量。

## 7. 检查输出与状态

`setup --check` 分别报告：

- `host_detected`（线索与局限）；
- `entry_status`：missing / installed / modified / unmanaged_matching / conflict；
- `launcher_importable`：指定 Python 能否导入共享运行入口；
- `model_config_status`：ready / missing / invalid；
- `credential_source`：上述来源对象，authenticated 永远 false；
- `run_configured`：配置有效且存在一个来源；不代表认证、目标环境或实际复现通过；
- `ready`：所选宿主入口完整、所需权限具备、launcher 可导入、run_configured 为 true，且无未完成事务。

所选宿主全部 ready 时 check 退出 0，否则 1；结构损坏/IO/参数错误为 2。宿主检测不到是 warning，不单独否定入口完整性。启动器 `check` 检查自身运行配置而不是另一宿主的安装。`--check`、`--dry-run` 不创建集成目录/锁/清单/备份，不在集成目标树写 Python 字节码；探测子进程设置 PYTHONDONTWRITEBYTECODE=1。零写保证针对本功能管理的用户配置/集成树，不把 Python 启动前的解释器缓存行为冒充为本功能可控事项。

## 8. 所有权、冲突、失败与卸载

清单 schema_version=1，含 installer_version、所选宿主各个 root/layout、每个受管文件的相对路径/绝对路径/sha256，以及非敏感绑定配置与来源描述。根路径必须匹配当前解析的合法宿主根；文件名仅允许本功能清单中的 Skill/脚本/policy 文件；拒绝 `..`、symlink/junction/reparse-point 越界。清单不是删除任意路径的授权。

文件规则：

- 不存在：创建并登记。
- 有受管清单且哈希仍匹配：可幂等保留/更新。
- 无清单但内容等于新模板：报告 unmanaged_matching，默认不取得删除所有权；其他缺少文件可以安装。显式 force 才备份并接管。
- 外来不同内容或用户改过受管文件：默认整个安装计划拒绝；force 才逐个备份后替换。
- force 备份使用 `<path>.reproagent-backup-<UTC时间>-<随机后缀>`，排他创建，不覆盖旧备份；清单保留备份路径/哈希，只供人工恢复。

写入流程：先渲染并预检全部所选宿主（包括另一个宿主的冲突），再获取排他安装锁并重新核对状态，创建事务记录，逐文件以临时文件 + os.replace 写入，最后原子提交清单。无变化不重写文件/清单，不产生备份。

普通异常尝试恢复本次已修改文件与旧清单；恢复前核对当前文件仍为本次写入版本，不能覆盖并发用户修改。失败报告 `install_failed` 或 `rollback_incomplete`，保留必要故障记录和备份。进程崩溃不承诺跨文件原子性；遗留锁/事务时，check 报 incomplete_transaction，后续写命令拒绝并输出恢复记录位置，由用户核对记录处理，force 不绕过。不要删除用户文件来强行恢复。

卸载只处理清单内、路径受限且哈希匹配的文件；用户改动、未受管同内容文件、额外文件、备份均保留并报告。清理本工具创建且确实为空的目录；不删除宿主根目录。清单中保留未卸载的受管修改项，删除成功移除的条目；没有清单时不猜测所有权。

**卸载不自动恢复 force 之前的用户文件，也不保证整个用户目录回到原样。** 输出备份路径供人工恢复。此语义替代旧版“恢复安装前状态”的承诺。

## 9. 文件结构与打包

```text
src/reproagent/integration/
  __init__.py
  setup.py          # 参数注册、计划、检查/安装/卸载调度
  hosts.py          # 合法安装根和宿主发现线索
  credentials.py    # 配置与来源规则、精确 dotenv、运行时 DPAPI
  manifest.py       # 所有权、锁、预检、故障记录、原子写/恢复
  render.py         # 安全渲染资源
  launcher.py       # 跨平台共享检查和 argv/子进程入口
src/reproagent/resources/integration/
  claude/SKILL.md.template
  codex/SKILL.md.template
  codex/agents/openai.yaml.template
  reproagent.ps1.template
  reproagent.sh.template
```

package-data 在原有列表后追加以下显式模式，不用单层 `resources/integration/*` 代替子目录覆盖：

```text
resources/integration/*.template
resources/integration/claude/*.template
resources/integration/codex/*.template
resources/integration/codex/agents/*.template
```

cli.py 只注册 setup parser，并在 setup 分支 lazy import 调度；顶层不 import integration。全局 Skill 和启动器通过 `importlib.resources` 渲染，不依赖源码 checkout。

## 10. 测试与验收

全部自动测试离线、假凭据、临时用户目录；临时 HOME/USERPROFILE/CLAUDE_CONFIG_DIR/CODEX_HOME/APPDATA/LOCALAPPDATA 及 PATH 由测试控制，禁止碰真实配置或凭据。不得把仅修改 HOME 当作 Windows 完整隔离。

必须覆盖：

1. 默认两个宿主且仅一个 Codex 根；显式 legacy、目录变量、同名路径去重、policy 正确位置；
2. 安装/重装/检查/卸载、未受管同内容、修改文件、备份不覆盖、两宿主冲突提前拒绝；
3. 注入中途写入或清单写入失败的恢复，以及回滚中用户修改保护；遗留事务/锁受控拒绝；路径越界和链接拒绝；
4. check/dry-run 前后临时 HOME 和用户配置树字节与结构不变，无字节码；输出无任何假密钥；
5. 检查与 runtime 选相同来源：env 优先、DPAPI 仅正确 provider/变量、dotenv 精确键/重复/引号、不读其他宿主认证；
6. 真正执行生成的 PowerShell/sh 启动器，使用离线假 CLI 核对 argv、子进程 env、退出码、缺配置错误、inspect 无凭据；Windows 使用测试生成的 DPAPI 假值验证 OS 解密，不打开用户 key；
7. 从 wheel 隔离安装后，在源码目录之外生成三个 Skill/policy/脚本层次的资源并执行 check；不能只测资源字符串；
8. 现有完整离线回归通过，Windows/Unix 各自运行新增启动器测试；缺平台的项标未验证，不伪称通过。

Windows 实机宿主发现/显式 check 单独记录，无需真实复现或付费调用。不以接入成功宣称复现率改善。

## 11. 文档与执行交接

新增 `docs/ai-tool-integration.md`，更新 README 和 `docs/claude-code.md`，保留历史验证记录。写清默认/legacy 路径、policy 的宿主支持边界、缺配置/凭据、项目迁移后重装、备份手工恢复、遗留事务处理与“不改宿主订阅”。

执行代理先读取此 spec 和配套计划，重新检查分支/工作区。已有未提交的 4 个文件及所有未跟踪文件不属于任务，禁止回退、清理或纳入提交。执行仅在授权范围内；本次文档交付不安装入口、不读取凭据、不提交/push/merge。

## 12. 依据与修订摘要

- [OpenAI：本地 Skill 路径与同名入口](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)。默认标准路径；旧路径是显式兼容选项，不保证自动去重。
- [OpenAI：agents/openai.yaml 的调用策略](https://learn.chatgpt.com/docs/build-skills#optional-metadata)。禁隐式调用位于 policy.allow_implicit_invocation，不是 SKILL.md 的 frontmatter。
- 仓库依据：`.claude/skills/reproagent/scripts/reproagent.ps1`、同目录 SKILL.md、`src/reproagent/cli.py`、`core/models.py:ModelConfig`、`pyproject.toml`。启动器与核心配置不一致处以本 spec 的共享规则修复，现有复现核心保持原样。
