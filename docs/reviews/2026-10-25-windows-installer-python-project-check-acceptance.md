# Windows 一键安装：Python 获取与目标项目检测验收

日期：2026-10-25
分支：`feat/ai-tool-integration`
状态：定向测试通过；真实新机器自动安装 Python 尚未执行（见未覆盖项）。
未提交、未 push、未合并。

## 范围

本次改安装器、测试和文档，并包含一个由验收暴露的集成编码修复：

- 修改：`scripts/install.py`、`scripts/install-windows.ps1`、`docs/windows-install.md`
- 修改（集成编码修复）：`src/reproagent/integration/render.py`，让生成的 `.ps1`
  使用 UTF-8 BOM；见下文“执行偏差”
- 新增：`scripts/project_check.py`
- 新增/修改测试：`tests/unit/test_windows_project_check.py`、
  `tests/unit/test_windows_bootstrap.py`、`tests/integration/test_windows_bootstrap_entry.py`、
  `tests/integration/test_windows_bootstrap_offline.py`
- 设计/计划：已随 2026-10-25 文档清理移出；本验收只保留实现与测试记录。

`src/reproagent/integration/render.py` 只改生成 PowerShell wrapper 的编码，不涉及
`core/`、`adapters/`、`runner.py`、`verifier.py`、`workspace.py`、`exporter.py`、
`store.py` 或任何业务逻辑；工作区原有的未提交修改全部保留。
`install.cmd` 不需要改，`%*` 已经把新参数透传给 PowerShell。

## 定向测试

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/test_windows_bootstrap.py `
  tests/unit/test_windows_project_check.py `
  tests/integration/test_windows_bootstrap_entry.py `
  tests/integration/test_windows_bootstrap_offline.py -q -rs
```

结果：

```text
37 passed in 36.84s
```

AI 工具接入回归（BOM 修复后）：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/unit/test_integration_hosts.py tests/unit/test_integration_render.py `
  tests/unit/test_integration_credentials.py tests/unit/test_integration_manifest.py `
  tests/unit/test_integration_launcher.py tests/unit/test_integration_setup.py `
  tests/integration/test_integration_launchers.py `
  tests/integration/test_integration_setup_cli.py `
  tests/integration/test_integration_package.py -q -rs
```

结果：

```text
135 passed, 3 skipped in 48.23s
```

3 个 skip：manifest 目录 symlink 权限 1 项，Windows 上 POSIX wrapper 2 项。

凭据回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_integration_credentials.py -q
```

结果：

```text
26 passed in 0.92s
```

## 覆盖的行为

### Python 获取

- 显式 `-Python`、`py -3`、PATH、仓库 `.venv`/`venv`、`.local/install-env` 的候选顺序。
- `NoDefaultCurrentDirectoryInExePath=1` 环境下用 `.\install.cmd` 显式当前目录。
- 没有 Python + `-NoPythonInstall`：exit 2，不调用 install.py，不写宿主。
- 伪造 winget 成功：走自动安装分支并继续调用 install.py；不下载真实安装包。
- 伪造 winget 失败 + 未签名安装包：签名校验拒绝，exit 2，不写宿主。
- 仓库 `.venv` 发现：用复制出来的 venv Python + `pyvenv.cfg` 作为临时仓库 `.venv`，
  在 PATH 没有 Python 的情况下被选中，未触发自动安装。

### 目标项目检测

- 轻量检查：目标 Python、版本、pytest、测试目录、`requires-python`。
- 有 task config 时：`target_modules` 导入、`source_roots`、`candidate_parent`。
- `-CollectOnly`：真实 `pytest --collect-only -q -p no:cacheprovider`，收集数 >= 1。
- pytest 缺失、收集失败、任务配置无效都落到 `ready=false`，不抛未处理异常。
- 目标检查子进程移除 `DEEPSEEK_API_KEY`、`REPROAGENT_API_KEY`、`PYTHONPATH`、
  `PYTHONHOME`；测试断言这些变量不进入子进程环境。
- 轻量检查前后目标文件内容一致；不创建 `.pytest_cache`。
- 离线集成测试：真实私有环境安装 + 宿主入口 + `--project --project-python
  --task-config --collect-only`，报告 `ready=true`、收集数 >= 1、目标文件不变，
  模型 endpoint 请求数为 0。

### 安装编排

- `--collect-only` 缺少 `--project`、目标目录不存在、task 配置不可读：安装前拒绝，
  不创建私有环境、不改宿主。
- `-Project` 检查报告写到 `.local/project-check.json`；检查异常转为 `[ACTION]`，
  不回滚已成功的工具安装。
- 生成启动器 `-Check`：从 `setup --check` 的 host root 定位
  `scripts/reproagent.ps1`，运行 `-Check`；不调用模型。

## 执行偏差：UTF-8 BOM 修复

目标检查的 launcher smoke 在包含中文的临时 checkout 路径上失败。手动运行生成的
`reproagent.ps1 -Check` 得到：

```text
ReproAgent tool Python is missing: re-run reproagent setup or pass -ToolPython.
```

原因是 `render.py` 把 `.ps1` 写成 UTF-8 无 BOM；Windows PowerShell 5.1 按 ANSI
代码页读取无 BOM 的 `.ps1`，导致 `$reproPinnedPython` 里的中文路径变成乱码。
给同一个脚本加 UTF-8 BOM 后，手动的 `-Check` 立即返回 `ready=true`。

修复：

- `src/reproagent/integration/render.py` 只在生成 `.ps1` 时使用 `utf-8-sig`；
  `SKILL.md`/`.sh`/YAML 仍是 UTF-8。
- 手动 BOM 验证通过；AI 工具接入回归 135 passed / 3 skipped。
- 这是集成 launcher 编码修复，不涉及业务逻辑。

影响：旧的真实宿主 wrapper 是修复前生成的；下次执行
`.\install.cmd -Force` 时会先备份再更新为 BOM 版本。当前 `E:\ReproAgent` 是 ASCII
路径，旧 wrapper 仍可工作。没有为这个修复再次运行真实宿主安装。

## 前一版一键安装验收（合并）

以下是 2026-10-25 早先“一次性双击入口”验收中独有、且本文件没有重跑的记录。它早于本文件的 Python 获取、项目检查与 BOM 修复，不能当作当前完整回归或当前状态。

- **隔离与来源**：早先验收在 `C:/Users/Dell/.codex/worktrees/windows-one-click-install/ReproAgent` 进行，基线 `cef94f1`；将 23 个既有依赖文件原样复制到隔离工作区，SHA256 回执为 `repro-results/windows-one-click-install-20261025/integration-dependency-baseline.json`。这些既有文件不宣称是本次安装入口的实现成果，后续集成保留原作者改动。
- **安装器行为**：双击入口调用 PowerShell 查找 Python 3.11+，由仅依赖标准库的 Python 引导程序创建私有 `.local/install-env`，使用普通包安装（非 editable），校验 `direct_url.json` 的来源匹配当前源码；不修改开发 `.venv`、全局 Python 或目标项目依赖。安装锁、`-NoPause` 和非零退出码均已验证。缺少模型配置或凭据时安装可以完成，但不会显示 Ready。
- **三个审查修复**：清除 pip 目的地变量、禁用 pip 配置文件并使用 `--no-user`，避免把包写到专用环境之外；用 ASCII-safe JSON 传递解释器路径，覆盖控制台代码页无法编码路径的情况；Python 3.12/CP936 下把 editable `.pth` 改为普通包安装，避免中文路径导入失败。
- **早先整仓离线回归（历史记录）**：1311 passed / 1 failed / 9 skipped，1333.54 秒，exit 1。唯一失败为既有 `tests/unit/test_integration_credentials.py::test_dpapi_file_round_trip`，原因是测试子进程继承了 PowerShell 7 的 `PSModulePath`；只修正测试进程的 `PSModulePath` 后，凭据测试 26 passed、1.46 秒。本文件后来的 26 项凭据测试计时取代这次计时；这份整仓结果仍是当时唯一的完整套件记录，不能拼成一次全绿。
- **交付状态**：该轮未运行付费模型案例、未执行真实用户级安装、未提交或合并；交付回执为 `final-validation.json`，23 个既有依赖文件逐一核对未变。

## 未覆盖 / 未验证

- **真实新机器自动安装 Python**：没有在无 Python 的虚拟机上实际执行 winget 或下载
  python.org 安装包；只验证了决策分支、伪造 winget 成功和未签名拒绝。UAC、网络、
  企业策略和真实签名成功路径仍需手工验收。
- **真实 winget 包版本/安装路径**：`Python.Python.3.12` 的实际安装位置和当前用户
  权限未在本机验证；`Find-InstalledPython` 覆盖常见路径和 `py -3`，但不同 winget
  版本可能不同。
- **完整离线套件**：没有重跑整仓；只跑了上述 37 项定向测试和 26 项凭据测试。
- **真实目标项目**：没有用大型真实项目验证；当前离线测试使用最小临时项目。
- **真实宿主 Skill 调用**：本次没有通过 Claude Code/Codex Skill 机制发起调用；
  安装器/启动器检查不等于真实宿主发现。
- **真实模型认证/复现**：没有调用模型，不验证 key 是否被服务端接受，也不证明
  Bug 能复现。
- Linux/macOS：本安装器仍为 Windows 专用。

## 本机状态说明

- 之前已用 `-Python E:\ReproAgent\.venv\Scripts\python.exe` 完成过一次真实安装；
  新的 `.venv` 自动发现逻辑没有再次改动真实宿主。下次在项目目录执行
  `.\install.cmd -Force` 时应直接发现 `.venv`，不再需要 `-Python`。
- 工作区存在别的任务留下的未提交 `src/reproagent` 修改（mtime 早于本次工作）；
  本次没有修改它们，也没有 reset/覆盖。
- 为自动化真实安装添加的 `.claude/settings.json` 是未跟踪文件，只包含一条窄权限规则；
  不属于产品代码，可按需删除或移到 `.claude/settings.local.json`。
- 未提交、未 push、未合并。
