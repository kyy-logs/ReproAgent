# VERIFICATION — AI Tool Integration (Claude Code / Codex)

Status: implementation complete, awaiting user review. **Not committed, pushed or merged.**
Date: 2026-10-24 (Asia/Shanghai)

## 2026-10-25 独立审查后的修复验收

本节对应当前未提交修复，后续原报告保留为上一版实现记录。仍未提交、push 或合并。

- 安装目标和控制文件的现有父组件也检查 symlink/junction；读取清单、逐文件写入、回滚和锁释放都拒绝沿重定向路径操作。
- 写入前、暂存/备份后以及目标临时文件 fsync 后核对预期 hash/不存在性。检测并发变化返回 `concurrent_file_change`；强制安装和卸载同样保护用户修改。新建发布采用 Windows 排他 rename / POSIX 排他 link，避免覆盖并发创建。
- 清单按宿主保存四个非秘密路径绑定，`setup --check` 默认检查已安装绑定；`template_current` 与用户 `modified` 分开。配置被移走后入口与运行配置状态分别报告。
- `inspect` 在加载模型配置之前分发，不需要模型 JSON 或凭据存在。
- Windows Skill 脚本路径使用单引号字面量；Codex 的可选参数按生成平台给出。

最终验证命令覆盖原九个 integration 接入模块，以及 `tests/integration/test_cli.py` 和 `tests/integration/test_claude_code_launcher.py`。使用仓库外临时 basetemp、假凭据与临时 HOME，在 Windows 原生进程执行：

**147 passed / 3 skipped / 0 failed，63.01 秒，exit 0。**

3 项 skip 为既有目录 symlink 权限条件 1 项、Windows 上的 POSIX launcher 测试 2 项；新增的两个真实 junction 测试没有跳过。测试包含按 Skill 完整文本执行 PowerShell 命令、DPAPI 假字符串和 wheel 在源码外的隔离安装。DPAPI 测试进程显式使用 Windows PowerShell 标准 `PSModulePath`。

证据：`repro-results/ai-integration-repair-20261025/publish-final.xml`，修复说明见同目录 `REPAIR.md`。原有失败日志均保留：首轮缺失 basetemp 父目录、wheel basetemp 位于源码内、沙箱限制 junction/hardlink 等属于实验环境问题，没有改测试断言掩盖。已复现的产品失败另有 red2.xml、commit-red/发布红测试的终端输出和对应回归用例。

没有重新运行全量离线套件；不将原报告的 1267 项算作本轮独立验收。没有真实宿主调用、真实认证或付费模型请求、Linux/macOS 原生执行或断电级测试。更新已有文件的 hash 校验与替换仍不是跨进程原子 compare-and-swap，不承诺对恶意并发进程的隔离。

Branch: `feat/ai-tool-integration`
HEAD at start: `cef94f17472f48f8386db3619f90e0d9128a4a1a`
Working tree: implementation and tests are uncommitted; the four pre-existing modified
files and all pre-existing untracked files were left untouched.

## 1. Environment and import path

| Item | Actual value |
| --- | --- |
| OS | Windows 11 Home China 10.0.26200 |
| Tool Python | `E:\ReproAgent\.venv\Scripts\python.exe`, Python 3.12.14 |
| Imported module | `E:\ReproAgent\src\reproagent\__init__.py` |
| pytest | 9.1.1 |
| AgentScope / httpx | 2.0.9 / 0.28.1 |
| PowerShell | `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe` |
| POSIX shell | `E:\git\Git\usr\bin\sh.EXE` (Git Bash on Windows) |
| ripgrep | **not installed**; `shutil.which("rg")` is `None` |

`pip check`: `No broken requirements found.`

## 2. Scope check

Added:

- `src/reproagent/integration/` (`hosts.py`, `render.py`, `credentials.py`, `manifest.py`,
  `launcher.py`, `setup.py`);
- `src/reproagent/resources/integration/` (Claude/Codex Skill templates, Codex policy,
  PowerShell and POSIX wrappers);
- integration unit/integration tests, `docs/ai-tool-integration.md`,
  `.github/workflows/integration-setup.yml`.

Modified only:

- `src/reproagent/cli.py`: setup parser registration and lazy dispatch (+14 lines);
- `pyproject.toml`: four package-data patterns appended;
- `README.md`, `docs/claude-code.md`: access instructions.

No tracked change under `core/`, `adapters/`, `prompts/`, `runner.py`, `verifier.py`,
`workspace.py`, `exporter.py`, `store.py`, or `evals/` running/scoring logic. The only
diff under `evals/` is the user's pre-existing `evals/swt_bench/results.py` modification,
whose SHA256 is unchanged.

During execution another session concurrently reorganized `docs/` (many historical
reviews/plans moved or deleted) and rewrote parts of `README.md`. Those changes are not
part of this task; they were neither reverted nor staged. The setup sections this task
added to `README.md` and `docs/claude-code.md` remain present.

Pre-existing modified files, SHA256 before → after:

```text
3cc2f1b6… → a150dd0e…  docs/reports/reproagent-问题与挑战记录.md   (changed externally, see below)
00bb1bd8… → 00bb1bd8…  docs/reviews/2026-10-15-relational-contract-repair-acceptance.md
a30093bc… → a30093bc…  evals/swt_bench/results.py
f67551dc… → f67551dc…  tests/unit/test_swt_results.py
```

The first file changed at 22:34 during the concurrent `docs/` reorganization described
above (the diff adds a P12 section and rewrites archive links). This task never wrote to
it and did not revert the external change. The other three hashes are unchanged.

The revised spec and the implementation plan remain uncommitted inputs and were not
reset or overwritten.

## 3. Test results

### 3.1 New feature tests

```text
107 passed, 3 skipped in 52.89s
```

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/test_integration_hosts.py tests/unit/test_integration_render.py `
  tests/unit/test_integration_credentials.py tests/unit/test_integration_manifest.py `
  tests/unit/test_integration_launcher.py tests/unit/test_integration_setup.py `
  tests/integration/test_integration_launchers.py `
  tests/integration/test_integration_setup_cli.py `
  tests/integration/test_integration_package.py -q -rs
```

Skips:

- `test_symlinked_parent_is_rejected`: Windows symlink privilege unavailable;
- two POSIX wrapper tests: Windows host; see §3.5 for the manual Git Bash smoke.

Before the final refinements the same suite reported `104 passed, 3 skipped`; targeted
tests after the refinements reported `45 passed, 1 skipped` plus `24 passed` for setup.
First recorded full-suite result before final refinements:

```text
1264 passed, 13 skipped in 1130.11s (0:18:50), exit 0
```

Final full-suite result on the final code:

```text
1267 passed, 13 skipped in 1284.24s (0:21:24), exit 0
```

The three additional passes are the tests added after the first run (POSIX executable
permission, invalid-dotenv run code, and dry-run conflict reporting). No first-run
failures were observed. Final log and JUnit XML are in the acceptance directory.

The 13 full-suite skips are: 5 Windows symlink-privilege tests, 4 ripgrep-dependent
tests (no real `rg` on this host), 2 POSIX-wrapper tests skipped on Windows, 1 long-path
link test and 1 venv-link test. None is a new-feature regression; the two
`test_integration_launchers` skips are covered by the Git Bash smoke and by the new
Ubuntu CI workflow (not yet executed).

### 3.2 Existing CLI and launcher regression

```text
12 passed in 7.19s
```

Command: `pytest tests/integration/test_cli.py tests/integration/test_claude_code_launcher.py -q`

### 3.3 Default and legacy generated files

Isolated temporary HOME, fake `DEEPSEEK_API_KEY`, fake model config; `setup` exited 0.

Default (`--host all`, standard Codex):

```text
.agents/skills/reproagent/SKILL.md                    f9c5dda71d11bddd...
.agents/skills/reproagent/agents/openai.yaml          a1499d95abd84475...
.agents/skills/reproagent/scripts/reproagent.ps1      93ff01f1ad4900bd...
.claude/skills/reproagent/SKILL.md                    6abb883ec83344ee...
.claude/skills/reproagent/scripts/reproagent.ps1      93ff01f1ad4900bd...
.reproagent/integrations.json                         1f138744110d6694...
```

Legacy (`--host codex --codex-layout legacy`):

```text
.codex/skills/reproagent/SKILL.md                     cf35c767d6853904...
.codex/skills/reproagent/agents/openai.yaml           a1499d95abd84475...
.codex/skills/reproagent/scripts/reproagent.ps1       93ff01f1ad4900bd...
.reproagent/integrations.json                         4da9e73f0943a744...
```

Full hashes are in `.tmp-.../generated-default.json` and `generated-legacy.json` under
the acceptance directory.

### 3.4 Zero-write and secret-output evidence

- `setup --check` in an empty isolated HOME: exit 1, HOME still absent, tree before/after
  identical.
- `setup --dry-run` in an empty isolated HOME: exit 0, HOME still absent, tree unchanged.
- Both records are in `zero-write-check.json` and `zero-write-dryrun.json`.
- Integration tests compare the full temp-HOME tree (contents + mtime) before/after
  `--check` and `--dry-run`, and assert a fake key never appears in stdout/stderr.
- `test_cli_dry_run_reports_a_conflict_without_writing` confirms dry-run reports the
  conflict while leaving the foreign file and the other host untouched.

### 3.5 Launchers actually executed

- **PowerShell (Windows):** rendered wrapper executed with a fake `reproagent` package on
  `PYTHONPATH`; `-Check`, `-Inspect`, `-TaskConfig` (+ model/fixed flags), tool-Python
  priority (`-ToolPython` > env > pinned), missing tool exit 2, and child exit-code
  propagation were verified. Paths contained spaces, Chinese, `'`, `$` and `;` and arrived
  byte-exact; no path character executed.
- **POSIX sh (Git Bash on Windows, supplementary):** rendered wrapper executed `check`
  and `run` with a fake package; binding and path arguments arrived exactly. This is a
  syntax/forwarding smoke only — **it is not Linux/macOS verification**.
- **DPAPI (Windows):** a fake value encrypted with PowerShell
  `ConvertFrom-SecureString` was decrypted by `decrypt_dpapi_file`; unsupported hex was a
  controlled `credential_load_failed` with no file content in the error.
- An early isolated-environment test lacked `SystemDrive`/`ProgramData`, so PowerShell
  created a literal `%SystemDrive%/` cache directory in the repository root. It was
  identified as test debris, removed, and the isolated test environments were extended
  with the missing Windows variables plus a temp working directory. This was not a
  product-code failure.

### 3.6 Failure, rollback and ownership evidence

Covered by tests:

- second-host conflict prevents all writes;
- write failure rolls back previously written files;
- manifest write failure restores the old manifest bytes;
- rollback preserves a concurrent user edit and leaves an incomplete transaction;
- leftover transaction blocks even `--force`;
- existing lock blocks apply;
- identical unmanaged file is not owned or removed;
- user-modified files survive reinstall and uninstall;
- manifest cannot authorize an outside root, `..`, absolute or unapproved relative path;
- symlinked parent is rejected when the platform allows creating one.

### 3.7 Wheel and isolated install

Final wheel built with `pip wheel . --no-deps --no-build-isolation` after all source
refinements:

```text
reproagent_local-0.1.0-py3-none-any.whl
sha256 ad8e10174ca24c20c45d0d83eacea7feca0d997e111040ba01ab036bae3063fb
```

Wheel contains all five templates:

```text
reproagent/resources/integration/claude/SKILL.md.template
reproagent/resources/integration/codex/SKILL.md.template
reproagent/resources/integration/codex/agents/openai.yaml.template
reproagent/resources/integration/reproagent.ps1.template
reproagent/resources/integration/reproagent.sh.template
```

`tests/integration/test_integration_package.py` (3 passed) creates a temporary venv with
system site-packages, installs the wheel with `--no-deps --no-index`, runs from outside
the checkout, asserts `reproagent.__file__` is under the venv (not the source tree), runs
`setup`, `--check`, and the generated platform wrapper. No templates were copied by hand.

## 4. Unverified / not executed

- **Linux/macOS real execution:** not available on this Windows host. The new CI
  workflow runs the POSIX wrapper tests on `ubuntu-latest`; that workflow has not been
  executed here. The Git Bash smoke is not a substitute.
- **Real Claude Code / Codex host discovery and `/reproagent` invocation:** not run
  against the real user profile, by the task's no-real-user-directory constraint.
  Isolated-HOME tests cover entry placement, discovery warnings, and readiness.
- **Real credentials / authentication:** no real key or host login state was read. DPAPI
  was tested only with a locally generated fake value.
- **Paid model calls / network verification:** none.
- **Crash-level cross-file atomicity:** exception rollback and leftover-transaction
  refusal are tested; a power loss or process kill between files is not.
- **Windows symlink creation:** skipped because the host lacks the privilege.
- **Independent human review:** not performed.

## 5. Remaining risks

- `--check` readiness is structural only; it does not authenticate the key or prove the
  target project environment or reproduction works.
- Codex versions that do not read the standard `~/.agents/skills` path, or do not support
  `agents/openai.yaml` policy, may ignore the entry/policy; legacy layout is explicit and
  compatibility is not guaranteed.
- A user who edits an installed file must use `--force` (backup then overwrite) or resolve
  the conflict manually.
- An interrupted install leaves a transaction record by design; automatic crash recovery
  is not provided. The record and backups must be inspected manually.
- The installer does not modify host subscriptions, model settings, or repro acceptance.

## 6. Reproduce this verification

Acceptance directory:
`C:\Users\Dell\AppData\Local\Temp\reproagent-ai-tool-integration-20261024`

It contains `full.log`, `full.xml`, `full-final.log`, `full-final.xml`, wheel build logs
and contents, generated default/legacy file hashes, zero-write records, and the
pre-existing file hash baseline.
