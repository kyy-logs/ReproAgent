# AI Tool Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 `reproagent setup` 安装 Claude Code / Codex 用户级调用入口；统一来源检查和启动逻辑，不改变 Bug 复现核心。

**Architecture:** setup 生成宿主 Skill、policy 和薄启动脚本，以受限路径、哈希清单和可诊断事务维护所有权。两个平台都委托同一个 Python launcher 处理配置、凭据和现有 CLI 转发；所有模型调用、预算和验收仍留在现有产品中。

**Tech Stack:** Python >=3.11 标准库（argparse/pathlib/importlib.resources/subprocess/ctypes）、PowerShell、POSIX sh、setuptools、pytest；不新增第三方运行依赖。

**Spec:** [AI 工具一键接入设计](../specs/2026-10-24-ai-tool-integration-design.md)。执行前通读该文档，尤其 §§4、7、8，不能仅按任务标题实现。

## Global Constraints

- 默认 `--host all`，Codex 默认 standard，只有显式 `--codex-layout legacy` 才安装旧目录；不默认双写。
- 禁隐式调用写到 Codex `agents/openai.yaml` 的 `policy.allow_implicit_invocation: false`。
- 不修改 core/、adapters/、prompts/、runner/verifier/workspace/exporter/store、evals、现有 run 参数或验收语义。
- 不修改现有 `.claude/skills/reproagent/`；不写 AGENTS.md/宿主设置，不读宿主认证，不调用模型或网络，不安装目标依赖。
- `--check`/`--dry-run` 零持久化写入；子进程禁写字节码。自动测试只用临时 home/宿主根和假凭据。
- 安装缺配置/凭据可成功但必须告警；check 必须区分入口可用、运行已配置和未验证认证。
- DPAPI 仅 Windows + HTTPS api.deepseek.com + 精确 DEEPSEEK_API_KEY；高优先级文件损坏不能暗中换账号。
- force 仅安装可用；先备份再覆盖。卸载不自动恢复备份，不删除用户修改或无清单文件。
- 所有改动以执行时分支内容为准；本计划记录的基线不是要求 reset 的目标。保留原有脏文件、未跟踪文件与其他会话改动。
- 本文是执行交接；当前文档任务不授权修改用户全局配置。自动化验证不需要用户真实 key，不产生付费调用。

## Review Focus

1. 路径包含空格、中文、单引号、美元符或分号：脚本参数应逐字到达假 CLI，不能执行其中字符。Task 4/6。
2. 环境变量与文件同时存在、DPAPI 属于别的 provider、启动 cwd 改变：check/runtime 必须选同一来源且不串用账号。Task 2/4。
3. 第二宿主冲突或写清单失败：不能留下无所有权记录的半套覆盖；用户并发修改不得被回滚吞掉。Task 3/5。
4. wheel 无源码 checkout、模板有多层目录：入口仍能生成并运行，不能把 site-packages 猜成项目根。Task 1/6。
5. 清单损坏、路径越界、同内容无清单、junction/symlink：不得借卸载或 force 删除/覆盖外部文件。Task 3/5。

## 执行环境与文件地图

从 `E:/ReproAgent` 的 `feat/ai-tool-integration` 开始检查，不自行 checkout/reset。设计提交为 cef94f1，但该提交之后的文档修改也是输入。已有 4 个修改文件：

```text
docs/reports/reproagent-问题与挑战记录.md
docs/reviews/2026-10-15-relational-contract-repair-acceptance.md
evals/swt_bench/results.py
tests/unit/test_swt_results.py
```

更新说明（2026-10-24）：上表中的 `docs/reviews/2026-10-15-relational-contract-repair-acceptance.md` 已由用户明确要求删除，不应恢复；不存在的文件不再作为保留或哈希检查对象，执行时以实时 git status 为准。

它们和全部既有未跟踪文件均不属于本任务。开始与结束保存 status 和这些文件的哈希；只 stage 明确列出的本任务文件，不用 git add -A。若用独立 worktree，必须先带入本 spec/plan 的已确认版本，不能假设它们已经提交。

| 文件 | 责任 |
| --- | --- |
| integration/hosts.py | 路径、平台、宿主线索和安装根白名单 |
| integration/render.py | 模板读取、安全字面量与绑定配置渲染 |
| integration/credentials.py | 配置绑定、来源描述、dotenv 与运行期 DPAPI |
| integration/manifest.py | 预检、所有权、锁、故障记录、提交/回滚、受限卸载 |
| integration/launcher.py | 独立 Python 模块入口、检查及 CLI subprocess 转发 |
| integration/setup.py | argparse Namespace 调度、安装计划和安全 JSON 输出 |
| resources/integration/ | 两份 Skill、Codex policy、两平台薄 wrapper |

下文 src 路径均在 `src/reproagent/` 下。新增 Python 文件避免顶层导入 core/app/AgentScope；ctypes Windows 对象只能在 Windows 解密函数内部创建。

测试命令的通用约定：使用已确认导入当前 checkout 的工具 Python，`python -m pytest ...`；显式指定仓库外的唯一 `--basetemp`。不要全局设置 PYTHONPATH 污染子进程安装测试。必要时在 pytest 父进程 sys.path 加当前 src 或给所选工具环境做正常 editable 安装。记录实际 `reproagent.__file__`。

## Task 1: 路径、资源与调用契约

**Files**
- Create: `src/reproagent/integration/__init__.py`, `hosts.py`, `render.py`。
- Create: `src/reproagent/resources/integration/claude/SKILL.md.template`、`codex/SKILL.md.template`、`codex/agents/openai.yaml.template`、`reproagent.ps1.template`、`reproagent.sh.template`。
- Test: `tests/unit/test_integration_hosts.py`, `tests/unit/test_integration_render.py`。

**Interfaces**
- `hosts.resolve_hosts(host: str, *, home: Path, environ: Mapping[str, str], codex_layout: str = 'standard') -> list[dict]`：每项 `{host, layout, root: Path, host_detected: bool, detection_sources: list[str]}`；只读，绝不 mkdir。
- `hosts.allowed_roots(*, home: Path, environ: Mapping[str, str]) -> dict[str, tuple[Path, ...]]`：Claude 当前根、Codex standard/legacy 两个合法根，供清单校验；不能把任意 manifest 根追加进来。
- `render.render_files(host: str, *, platform_name: str, binding: dict) -> dict[str, bytes]`：相对路径到 UTF-8 正文；只生成当前平台脚本；Codex 必含 policy。binding 的安全字段由 Task 2 定义。
- `render.encode_binding(binding: dict) -> str`：仅 tool_python/model_config/repro_root/env_file 四个路径的 UTF-8 JSON -> base64；不允许 base_url、配置正文、凭据值或完整环境。wrapper 内保存编码后的绑定，路径字面量分别用 PowerShell 单引号双写 / shlex.quote。

- [ ] **Step 1 — 写失败测试。** `test_default_resolves_two_hosts_one_codex_root` 断言 all 恰好 Claude+standard Codex、未创建目录；`test_legacy_selects_only_legacy_root` 断言 CODEX_HOME 生效且不双写；覆盖 home/CLAUDE_CONFIG_DIR、host 无法发现、重复根去重。

  最小断言示例（模块尚不存在时应先失败）：

  ```python
  def test_default_resolves_two_hosts_one_codex_root(tmp_path):
      home = tmp_path / 'isolated-home'
      roots = resolve_hosts('all', home=home, environ={}, codex_layout='standard')
      assert {item['host'] for item in roots} == {'claude-code', 'codex'}
      assert len(roots) == 2
      assert [item['root'] for item in roots if item['host'] == 'codex'] == [
          home / '.agents' / 'skills' / 'reproagent']
      assert not home.exists()
  ```

- [ ] **Step 2 — 跑红。** `python -m pytest tests/unit/test_integration_hosts.py -q`，应因新模块未实现失败，而非环境错误。
- [ ] **Step 3 — 实现 hosts。** 对无效 host/layout、相对/空宿主变量给明确参数错误；发现只用可执行文件/目录线索，禁止读取认证内容。
- [ ] **Step 4 — 写渲染失败测试。** `test_codex_policy_is_separate_yaml` 断言正确相对路径和 policy；`test_global_skill_has_no_checkout_relative_links` 断言不含旧 ../../../docs 和虚构宿主变量；`test_wrapper_literals_do_not_execute_path_characters` 保存包含空格/中文/'/$/; 的测试路径，先断言编码与解码逐字相等，真实执行在 Task 4。
- [ ] **Step 5 — 实现 render 与资源。** Skill 保留 spec §6 的现成 JSON/自然语言两条流程、显式调用与证据等级；wrapper 暂只定义 Task 4 将实现的共享入口调用契约，不伪造业务实现。Unix 的 `--tool-python` 仅放在 action 前，之后的参数交给 Python。
- [ ] **Step 6 — 跑绿并定点提交。** 两个新单测文件全部通过；只提交此任务文件，建议 `feat: define AI tool integration paths and resources`。

## Task 2: 共享配置与凭据来源

**Files**
- Create: `src/reproagent/integration/credentials.py`。
- Test: `tests/unit/test_integration_credentials.py`。

**Interfaces**
- `default_repro_root(module_file: Path) -> Path | None`：仅认可 spec §2 的源码 marker；已安装 wheel 返回 None。
- `load_binding(*, tool_python: Path, model_config: Path | None, repro_root: Path | None, env_file: Path | None) -> dict`：返回安全绑定 `{tool_python, model_config, repro_root, env_file, model_config_status, api_key_env, base_url, model}`，路径为绝对字符串或 null，无配置时不猜 provider/变量。
- `discover_source(binding: dict, *, environ: Mapping[str,str], platform_name: str) -> dict`：仅返回 spec §4 来源字段；无配置为 missing + reason=model_config_required。env 的 name 不公开 value；DPAPI 只查存在；dotenv 只公开同名非空项的来源。
- `load_child_environment(binding: dict, source: dict, *, environ: Mapping[str,str], platform_name: str) -> dict[str,str]`：运行期复制父环境，只注入选定变量；加载失败抛受控错误，不能回退换账号。返回值只交 subprocess，禁止传日志/报告。
- `decrypt_dpapi_file(path: Path) -> str`：仅运行时调用；系统 CryptUnprotectData/UTF-16LE，释放 OS 分配内存；支持当前产品使用的默认 SecureString DPAPI hex 格式。错误不包含文件内容。

- [ ] **Step 1 — 写失败测试。** `test_no_model_config_does_not_guess_deepseek`、`test_binding_uses_product_default_key_name`、`test_wheel_location_is_not_repro_root`，断言无配置 name=null、省略 api_key_env 时为 REPROAGENT_API_KEY、site-packages 不自动找 .local。
- [ ] **Step 2 — 跑红并实现绑定。** 测试文件应因缺实现失败；实现后上述用例通过。仅检查集成需要的字段，不替代现有 run 的完整配置校验。
- [ ] **Step 3 — 写来源表驱动测试。** `test_check_and_load_share_source_selection` 覆盖 env 优先、空 env、允许的 DPAPI、非 Windows、错误变量名、api.deepseek.com.evil、HTTP、其他 provider、显式 dotenv 高于根 dotenv、启动 cwd 改变不影响已绑定路径。用假 loader/假值，不读真实 key。
- [ ] **Step 4 — 写 dotenv/泄露边界测试。** 断言精确键、可选 export、单/双引号、无插值/执行、其他键不注入；重复/不闭合为 invalid_credential_file；DPAPI 检测不调用解密；源文件损坏不 fallback。把假密钥 canary 放入 env/文件/异常，断言来源 JSON 和错误文案均不含它。
- [ ] **Step 5 — 实现来源与 runtime 加载，跑绿。** 在 Windows 测试环境用临时 PowerShell `ConvertFrom-SecureString` 生成假值 fixture，验证 OS 解密结果；不支持格式/跨用户失败受控。非 Windows 仅真实 DPAPI 适配测试可按平台跳过，来源规则测试不得全跳。
- [ ] **Step 6 — 定点提交。** `feat: share integration credential discovery and loading`。

## Task 3: 所有权与可恢复文件操作

**Files**
- Create: `src/reproagent/integration/manifest.py`。
- Test: `tests/unit/test_integration_manifest.py`。

**Interfaces**
- `read_manifest(path: Path, *, allowed_roots: dict[str, tuple[Path,...]]) -> dict`：不存在为空 schema_version=1；损坏/未知 schema/越界记录受控拒绝，不默认为空并夺取已有文件。
- `classify_file(path: Path, desired: bytes, record: dict | None) -> str`：missing / owned_matching / unmanaged_matching / conflict，已改受管文件为 conflict。
- `preflight(plan: dict, *, manifest_path: Path, allowed_roots: dict) -> list[dict]`：只读，收集全部冲突、锁/故障记录、路径风险，既用于 dry-run 也用于真正写前复核。
- `apply_plan(plan: dict, *, manifest_path: Path, allowed_roots: dict) -> dict`：执行安装或卸载并返回安全状态，不直接打印。
- plan 结构由本任务固定：`{action, hosts, operations, preserved, conflicts, manifest_after}`；operation 至少含 `op=create|update|adopt|remove|keep`、host/root/relative_path/path、`expected_sha256: str|None`、`desired_sha256: str|None`、写入时的 `content: bytes`。内部 content 不能进入 JSON 输出。

- [ ] **Step 1 — 写所有权失败测试并跑红。** 覆盖首次安装、重装无文件/manifest mtime 变化、受管更新、外来冲突；`test_identical_unmanaged_file_is_not_owned_or_removed` 断言无清单同内容仅 keep，卸载不删；force 后备份+接管；重复 force 不覆盖旧备份。
- [ ] **Step 2 — 写路径边界失败测试。** `test_manifest_is_not_arbitrary_delete_authority` 覆盖 ..、非法文件名、伪造 root、受管目录外的相同哈希文件。每层父目录 lstat 检查，拒绝 symlink 和 Windows reparse-point/junction；不能先 resolve 走出根再以结果为合法根。真实链接测试按权限标 skip，路径验证规则仍需普通测试覆盖。
- [ ] **Step 3 — 实现 schema、分类与预检，跑绿。** 合法相对路径只允许 SKILL.md、当前/已登记平台脚本和 Codex agents/openai.yaml；备份只被报告，不当作可卸载文件。
- [ ] **Step 4 — 写事务故障测试。** `test_second_host_conflict_prevents_all_writes`；`test_file_write_failure_rolls_back_previous_files`；`test_manifest_write_failure_restores_old_manifest`；`test_rollback_does_not_overwrite_user_edit`；`test_incomplete_transaction_blocks_force`。通过替换原子写依赖在精确步骤抛 IO 错，不改测试期限。
- [ ] **Step 5 — 实现写入/卸载。** 先完整预检，写操作取得排他锁后再检查预期哈希；事务记录含 transaction_id、phase、文件预期/新哈希、受限暂存/恢复路径及旧清单，不含凭据。写入/清单均临时文件+os.replace。普通异常逆序恢复且先核对本次写入哈希；失败保留诊断，force 不绕过遗留锁/事务。卸载同样执行受限计划，保留修改文件/未受管文件/备份，仅清空本工具创建的空目录。
- [ ] **Step 6 — 跑完整新单测并提交。** `python -m pytest tests/unit/test_integration_manifest.py -q` 全绿；建议 `feat: manage integration files with ownership and rollback`。不把异常回滚等同于崩溃自动恢复，不新增未经 spec 授权的强制删除命令。

## Task 4: 真正可执行的共享启动入口

**Files**
- Create: `src/reproagent/integration/launcher.py`。
- Modify: `src/reproagent/resources/integration/reproagent.ps1.template`、`reproagent.sh.template`。
- Test: `tests/unit/test_integration_launcher.py`、`tests/integration/test_integration_launchers.py`。

**Interfaces**
- `probe_tool_python(tool_python: Path) -> dict`：子进程 `-c` 验证可导入 launcher，禁字节码，只返回可用性/版本/受控错误，不回显原始 stdout/stderr。
- `launch(action: str, target: Path | None, binding: dict, *, fixed_repo: Path | None = None, fixed_python: str | None = None, environ: Mapping[str,str] | None = None) -> int`：action=check/inspect/run。check 用 Task 2 来源描述不解密；inspect 不碰凭据；run 取子进程环境，用 argv 调现有 CLI，保持退出码。
- `main(argv: list[str] | None = None) -> int`：支持 `--binding-b64`（只含 Task 2 安全绑定）、check/inspect/run，以及运行时 model-config/repro-root/env-file/fixed 覆盖；wrapper 选择 Python 后，实际 CLI 的解释器使用 launcher 所在的 sys.executable。
- wrapper 只选择 Python/翻译参数；Python override 无效就报错。共享入口不再递归调用 wrapper 或 `setup --check`。

- [ ] **Step 1 — 写失败测试并跑红。** `test_inspect_without_model_or_credential` 断言不调用 discover_source/load；`test_check_does_not_decrypt_or_run_model`；`test_run_uses_exact_cli_argv_and_child_environment` 断言 `-m reproagent run --config ... --model-config ...` 和 fixed 参数、shell=False、父环境不变、子进程失败码原样返回。
- [ ] **Step 2 — 实现 launcher。** 缺配置、变量非法、来源加载失败以白名单 JSON/稳定错误码返回 2；检查未配置返回 1。不得把私有 env/result 对象放到诊断里；不捕获后打印含秘密的异常。run/inspect 的业务 JSON 与 stdout 沿用现有 CLI，不用安装器格式改写它们。
- [ ] **Step 3 — 写真实 wrapper 离线测试。** 在临时工具 Python/假 reproagent CLI 下实际运行渲染脚本，验证 ToolPython > env > pinned 优先级，路径含空格/中文/'/$/; 逐字传入且无额外文件执行；check、inspect、run、fixed flags、配置覆盖、退出 1/2/3/130；丢失工具 Python 不回落其他 PATH 安装。
- [ ] **Step 4 — 实现两个 wrapper 并跑绿。** Windows PowerShell -File、Unix sh 实际执行；Unix 脚本含 exec、无 eval/source/xtrace。Windows 无真平台时相应集成测试应明确 skip，而不是只查字符串就算验证。
- [ ] **Step 5 — 验证来源一致性。** check 的 descriptor 与真正 run 注入假值选择同源；Windows DPAPI fixture 用临时文件真实解密，只在测试断言内比较假值，日志不打印它。修改启动 cwd 不加载新的 .env。
- [ ] **Step 6 — 提交。** `feat: launch ReproAgent through shared integration runtime`。

## Task 5: setup 命令与安全状态输出

**Files**
- Create: `src/reproagent/integration/setup.py`。
- Modify: `src/reproagent/cli.py`，仅 `main` 中 setup parser 与 try 内 lazy dispatch。
- Test: `tests/unit/test_integration_setup.py`、`tests/integration/test_integration_setup.py`。

**Interfaces**
- `build_plan(options: dict, *, environ: Mapping[str,str], platform_name: str) -> dict`：使用 Tasks 1–3，生成内部 plan，不写入；options 是 argparse Namespace 的白名单字段，不能含环境值。
- `check_setup(options: dict, *, environ: Mapping[str,str], platform_name: str) -> dict`：按 spec §7 输出所有所选宿主分项；entry 未完整/modified/配置缺失不能仅因 host_detected=true 返回 ready。
- `run_setup(args: argparse.Namespace) -> int`：唯一 stdout 单行 JSON，安全地筛选 plan/结果字段；内部 bytes、模型配置正文、异常对象和 env 不可序列化到输出。

- [ ] **Step 1 — 写端到端失败测试。** `test_setup_default_installs_both_hosts_but_one_codex_root`、`test_setup_check_reports_each_readiness_dimension`、`test_setup_dry_run_has_zero_persistent_changes`。在完整临时用户环境中用 `python -m reproagent setup` 执行；未实现时 argparse/module 应失败。
- [ ] **Step 2 — 添加 parser/lazy dispatch 和调度。** 按 spec §2 完整注册参数及非法组合。顶层不得 import integration；现有分支函数不改动。all 安装遇另一宿主冲突须整体失败，不能先装 Claude 再发现 Codex 冲突。
- [ ] **Step 3 — 加生命周期测试。** 安装->重复幂等->修改文件->拒绝重装->force 备份->卸载保留备份；无清单同内容保留；显式 legacy 不写 standard；切换布局不擅自删除旧根；check/卸载读 manifest 所选宿主记录；伪造 manifest/链接绝不触碰外部 canary 文件。
- [ ] **Step 4 — 检查零写与脱敏。** check/dry-run 前后递归比较临时 HOME/宿主根的目录、正文、mtime，捕获 stdout/stderr，确认没有 .pyc/锁/清单/备份或假 key；即便 config/credential 无效，也不能回显原始文本。缺凭据安装 code=0+warning，check code=1；IO/结构错 code=2。
- [ ] **Step 5 — 跑相关回归。** 新增 integration 单元/集成测试全部运行；再运行现有 `tests/integration/test_claude_code_launcher.py` 与既有 CLI 测试，证明旧 launcher/CLI 行为未变。保留首次失败日志，不靠重跑掩盖产品错误。
- [ ] **Step 6 — 提交。** `feat: expose one-command AI tool setup`，只 stage 本任务文件。

## Task 6: wheel、跨平台回归与使用文档

**Files**
- Modify: `pyproject.toml` package-data 追加 spec §9 四个模式，禁止覆盖旧列表。
- Create: `tests/integration/test_integration_package.py`、`docs/ai-tool-integration.md`、`.github/workflows/integration-setup.yml`。
- Modify: `README.md`、`docs/claude-code.md` 的接入段落，保留历史记录。

**Interfaces**
- 继续使用以上公开命令，不新增安装路径/凭据策略；wheel 测试以实际已安装包为依据，不能注入源码路径补漏资源。

- [ ] **Step 1 — 写 wheel 失败测试。** 本地 `pip wheel . --no-deps --no-build-isolation`；构建环境需已具备 setuptools/wheel，不为测试联网下载。断言 zip 中五个模板全在，尤其 codex/agents/openai.yaml.template；资源当前未打包时测试应红。
- [ ] **Step 2 — 从 wheel 做隔离安装测试。** 临时 venv 用 ensurepip 和 --no-deps/--no-index 安装 wheel；用该临时环境的 .pth 仅追加工具环境的 site-packages 目录以复用已存在依赖，不运行其 editable .pth，也不得加入 checkout src 或全局 PYTHONPATH。从仓库外 cwd 导入，断言 reproagent.__file__ 属于该 venv，执行 setup/check/inspect 假材料；无 root/config 时明确告警而非误找 site-packages/.local，显式假配置可 ready。实际执行生成的本平台 wrapper。
- [ ] **Step 3 — 追加 package-data 后跑绿。** 保留既有 prompts/probe/resources 规则。若缺 setuptools/wheel，只报告环境前置条件；不得访问网络或把模板手工复制进测试环境冒充发布包。
- [ ] **Step 4 — 新增独立 Windows/Ubuntu workflow。** Python 3.11，依赖安装按仓库现有 CI 方式进行；pytest 阶段不访问模型。运行新增 integration* 单测、setup/launchers/package 集成测试，使用临时用户根，Windows/Unix 真正执行各自脚本。平台性 skip 在报告中列明，不能据 Windows 本地结果声称 Unix 已通过。
- [ ] **Step 5 — 写文档。** 一条命令、单宿主/legacy 选择、显式调用、check 各字段和退出码、缺配置、凭据来源、迁移后重装、--force 的备份/人工恢复、遗留事务和锁的诊断；解释订阅登录态与 ReproAgent API key 无关。注明安装不改变复现率，不编造真实宿主或模型验收结果。
- [ ] **Step 6 — 完整离线回归。** 串行运行 `python -m pytest tests/unit tests/integration -q --basetemp <仓库外唯一临时目录> --junitxml <验收目录/full.xml>`；显式提供已存在的 rg 路径。输出实际导入路径、平台/依赖、pass/skip/fail、skip 原因，保留首次失败。不要用旧套件数字作为本分支期望总数。
- [ ] **Step 7 — 范围复核与提交。** 与开始时 status/hash 比较，确认原有 4 个修改与全部未跟踪材料未被更改/纳入提交；检查只修改 spec 白名单。建议 `test: verify packaged AI tool integrations across platforms`。执行者可按用户后续授权提交本任务变更，但本计划不授权 push/merge 或安装到真实用户目录。

## 验收工件与交接

执行者在独立验收目录保存 `VERIFICATION.md`，必须包含：

- 实际 HEAD、当前工作区补丁摘要、实际导入模块路径；
- 默认/legacy 实际生成的文件清单和哈希；
- check/dry-run 零写前后对照、假密钥未泄露断言；
- 写入/清单失败、用户修改保护、越界/链接和遗留事务拒绝证据；
- wheel 内资源清单及仓库外已安装包运行结果；
- 全量测试及各平台真实 wrapper 结果；未执行项明确标出；
- 未读真实凭据、未调用模型、未写真实用户目录、未 push/merge。

只有离线与打包验收通过，才交用户做真实宿主发现/显式 check。宿主检查不要求付费复现。本计划到这里结束，不自动扩大到新的 Agent 功能或真实 A/B。
