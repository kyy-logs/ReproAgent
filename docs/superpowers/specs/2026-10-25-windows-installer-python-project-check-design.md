# Windows 一键安装：Python 获取与目标项目环境检测设计

日期：2026-10-25
状态：设计已确认，待实现
范围：`install.cmd` / `scripts/install-windows.ps1` / `scripts/install.py`，新增
`scripts/project_check.py`；不新增 `reproagent doctor` 产品命令。

## 1. 背景与目标

现有 `install.cmd` 能找到 Python 时，可以创建 `.local/install-env`、安装依赖、
接入 Claude Code/Codex，并运行 `setup --check`。当前限制：

- Python 必须已经出现在 `-Python`、`py.exe` 或 PATH 中；项目已有 `.venv` 时不会自动
  使用，因此本机曾需要手动加 `-Python`。
- 全新机器没有 Python 时，只能提示用户安装。
- 安装后只检查工具自身，不检查目标项目的 Python、pytest、目标模块和测试目录。
- 没有一个不调用模型的“整条链路可用”检查。

本设计增加：

1. 先找现有 Python（含项目 `.venv` / `venv` / `.local/install-env`），找不到再自动安装
   Python 3.12。
2. `-Project` 目标项目只读环境检测；默认轻量，`-CollectOnly` 可选深度检查。
3. 安装后执行 `setup --check`、生成启动器 `-Check` 和目标项目检查；全程不调用模型。

## 2. 非目标

- 不安装目标项目的依赖或修改目标项目环境。
- 不运行真实 Bug 复现，不调用真实模型，不验证 API key 认证。
- 不新增 `reproagent doctor` CLI；目标检查只在安装器里。
- 不内置 Python runtime，不做 Linux/macOS 安装器。
- 不绕过 UAC、winget 企业策略或网络限制。
- 不改变现有 `setup` 的所有权、备份、manifest 和 host 入口规则。

## 3. 已确认的取舍

| 决策 | 选择 |
| --- | --- |
| Python 获取 | 先找现有，找不到再自动装（winget 优先，官方安装包兜底） |
| 目标检测入口 | 安装器 `-Project`，只读 |
| 检测深度 | 默认轻量；`-CollectOnly` 才运行 pytest 收集 |
| 产品 CLI | 不改；检测逻辑放在 `scripts/` |
| 模型调用 | 安装、检查、目标检测均不调用模型 |

## 4. 总体结构

```text
install.cmd
  -> scripts/install-windows.ps1
       - Python 发现 / 自动安装
       - 调 scripts/install.py
  -> scripts/install.py
       - 校验参数、锁、私有环境、pip、源码来源
       - setup 安装 + setup --check
       - 调 scripts/project_check.py（可选）
       - 生成启动器 -Check（可选或无模型检查）
```

`project_check.py` 只依赖标准库，使用目标项目的 Python 子进程做检测；它不导入
ReproAgent 的产品模块，避免目标检查依赖工具私有环境。

## 5. Python 获取

### 5.1 发现顺序

`install-windows.ps1` 按顺序尝试：

1. 显式 `-Python <path>`
2. `py.exe -3`（找不到再试 `py.exe -3.12`、`py.exe -3.11`）
3. PATH 中的 `python.exe` / `python3.exe`，排除 `Microsoft\WindowsApps` 存根
4. 当前仓库 `.venv\Scripts\python.exe`
5. 当前仓库 `venv\Scripts\python.exe`
6. 已存在的 `.local\install-env\Scripts\python.exe`
7. 自动安装（除非 `-NoPythonInstall`）

每个候选都执行同一个探针：

```text
<python> -c "import json,sys; sys.exit(1) if sys.version_info < (3,11) else print(json.dumps(sys.executable, ensure_ascii=True))"
```

解析 ASCII-safe JSON，确认返回路径是存在的文件。失败则继续下一个候选；所有
stderr 不进入最终 JSON/报告。

### 5.2 自动安装

只有没有找到任何合格 Python 且未传 `-NoPythonInstall` 时执行。

优先 winget：

```text
winget install --id Python.Python.3.12 -e --source winget --silent
  --disable-interactivity --accept-package-agreements --accept-source-agreements
```

winget 不存在、不可用、返回非零或安装后仍找不到 Python 时，使用官方安装包兜底：

1. 从 python.org 下载固定 3.12.x 的 per-user 安装包到 `%TEMP%`。
2. 用 `Get-AuthenticodeSignature` 校验签名为 `Valid`，签名主体包含
   `Python Software Foundation`。校验失败则删除文件并停止。
3. 静默安装：

   ```text
   /quiet InstallAllUsers=0 PrependPath=0 Include_pip=1 Include_launcher=1
   ```

4. 接受退出码 0 或 3010（需要重启，但安装算成功），然后重新在常见位置查找：
   `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`、
   `%ProgramFiles%\Python312\python.exe`、`py.exe -3`。
5. 删除临时安装包。

约束：

- 不修改 PATH；所有后续调用使用绝对路径。
- UAC 无法绕过；winget/官方安装包可能弹 UAC。用户拒绝时安装失败。
- 企业策略、无网络、签名失败都返回明确错误；**不写宿主入口**。
- `-NoPythonInstall` 时只报告缺少 Python 和手动安装命令，退出码 2。
- 自动安装的版本固定为 3.12.x；实现时使用一个经过验证的补丁版本常量，并在文档中
  写明升级方式。

## 6. 目标项目检测

### 6.1 参数

```text
install.cmd -Project <目录>
install.cmd -Project <目录> -TaskConfig <task.json>
install.cmd -Project <目录> -ProjectPython <python.exe>
install.cmd -Project <目录> -CollectOnly
```

- `-Project`：目标项目根目录；必须存在且是目录。
- `-ProjectPython`：目标解释器显式覆盖。
- `-TaskConfig`：可选；读取 `language.python`、`source_roots`、`target_modules`、
  `candidate_parent`、`pytest_args`。task 里的 `repo` 与 `-Project` 不一致时只报告，
  不自动修改或覆盖。
- `-CollectOnly`：运行 pytest 收集；必须同时有 `-Project`，否则参数错误。
- 参数在安装开始前校验；无效参数退出码 2，不创建私有环境、不改宿主。

### 6.2 目标 Python 发现顺序

1. `-ProjectPython`
2. `-TaskConfig` 中的 `language.python`（相对路径按 task 文件目录解析）
3. `<project>\.venv\Scripts\python.exe`
4. `<project>\venv\Scripts\python.exe`
5. `py.exe -3`
6. PATH 中的 `python.exe` / `python3.exe`

报告实际使用的路径和来源。显式 `-ProjectPython` 或 task config 中的
`language.python` 如果缺失或低于 3.11，直接报告 `ready=false`，不自动改用其他
Python；只有这样才不会把“项目声明的解释器坏了”误报成“换一个解释器就好了”。
没有显式指定时才继续尝试项目 `.venv`/`venv`/`py`/PATH。找不到时报告
`ready=false`，不安装 Python 到目标项目。

### 6.3 默认轻量检查

- Python 版本：运行 `python -c "import sys; print(sys.version)"`，报告版本。
- `requires-python`：读取 `<project>\pyproject.toml` 的 `project.requires-python`；
  只做简单下界（如 `>=3.9`）判断，复杂说明符原样报告、不强制判定。
- pytest：运行 `python -c "import pytest; print(pytest.__version__)"`；失败报告
  “pytest 不可用”。
- 测试目录：有 task config 时用 `candidate_parent`；否则依次检查 `tests`、`testing`、
  `test`。报告实际存在的目录。
- 目标模块：有 task config 时，对每个 `target_modules` 运行
  `python -c "import importlib; importlib.import_module(name)"`，设置
  `PYTHONPATH` 为项目根加 `source_roots`（`src` → `<project>\src`），
  `PYTHONDONTWRITEBYTECODE=1`，超时 30 秒。报告每个模块是否可导入。
  没有 task config 时报告“未指定目标模块，未检查”。

默认轻量检查不运行 pytest，不执行目标测试，不安装依赖，不写目标目录。导入目标模块
本身会执行目标代码；文档必须写明这一点。

### 6.4 `-CollectOnly` 深度检查

运行：

```text
<目标 python> -m pytest --collect-only -q -p no:cacheprovider
```

工作目录为目标项目根；设置：

- `PYTHONPATH`：项目根加 task 的 `source_roots`（有 task config 时）
- `PYTHONDONTWRITEBYTECODE=1`
- `PYTEST_ADDOPTS=`（清空调用方环境变量；项目自身 pytest 配置仍生效）
- 超时 120 秒；超时记为失败

保留项目自身的插件自动加载，因为要检查真实项目环境；文档写明这会执行目标项目的
conftest/插件代码。解析输出中的收集数量；收集为 0 时报告 warning，不把“没有测试”
当成通过。`-p no:cacheprovider` 避免写 `.pytest_cache`；`PYTHONDONTWRITEBYTECODE`
避免写 `__pycache__`。

### 6.5 安全边界

- 目标检查子进程移除 `DEEPSEEK_API_KEY`、`REPROAGENT_API_KEY` 和其他已知模型密钥
  环境变量；保留 PATH、SystemRoot、TEMP/TMP、PATHEXT 等运行必需变量。
- 不读取目标项目的 `.env`，不读取 Claude/Codex 登录态。
- 不打印密钥；报告只包含路径、版本、导入结果和 pytest 输出摘要。
- `-Project` 本身就是执行目标代码的授权；`-CollectOnly` 是更深的显式授权。

### 6.6 `ready` 判定

只有以下条件全部满足才报告 `ready=true`：

- 找到目标 Python，且版本 >= 3.11；`requires-python` 是简单下界时也满足。
- `import pytest` 成功。
- 测试目录存在。
- 有 task config 且指定 `target_modules` 时，所有目标模块可导入。
- 运行了 `-CollectOnly` 时，pytest exit 0 且收集到的测试数 > 0。

没有指定 `target_modules`、没有运行 `-CollectOnly` 时，只报告“未检查”，不因此
判失败，但在 `limitations` 中写明。`ready=false` 时仍保留完整报告，便于修正。

### 6.7 报告与退出码

写出 `.local\project-check.json`（位于 ReproAgent 仓库的 `.local`，不是目标项目），
并在控制台打印摘要。结构至少包含：

```json
{
  "schema_version": 1,
  "project": "E:\\target",
  "task_config": "E:\\target\\task.json",
  "python": {"path": "...", "version": "...", "source": "project_venv"},
  "requires_python": ">=3.9",
  "pytest": {"available": true, "version": "..."},
  "test_dir": "tests",
  "target_modules": [{"module": "pkg", "importable": true, "error": null}],
  "collect_only": {"ran": false, "exit_code": null, "collected": null},
  "ready": true,
  "limitations": ["does not authenticate the model", "does not run tests"]
}
```

退出码：

- 工具安装失败：2。
- 工具安装成功、模型配置/凭据缺失：保持现有行为，0 + `[ACTION]`。
- 工具安装成功、目标检查未通过：0 + `[ACTION]`，报告中 `ready=false`。
  安装成功与目标就绪分开报告，不回滚宿主入口。
- 参数错误、Python 自动安装失败：2，且不写宿主入口。

## 7. Smoke 检查

安装结束后的检查顺序：

1. 私有环境/源码来源校验（已有）。
2. `setup --check`（已有）。
3. 生成启动器 `-Check`：从安装清单中找到已选宿主（优先 Claude Code，`-Target all`
   时两个都检查）的 `scripts/reproagent.ps1`，运行
   `powershell.exe -NoProfile -File <script> -Check`，确认 binding、模型配置和凭据来源；
   不调用模型。检查失败只报告 `[ACTION]`，不回滚已装好的入口。
4. 有 `-Project` 时运行目标检查。
5. 打印结果：

   ```text
   [OK] ReproAgent installed. No model was called.
   [OK] Ready. ...                 # 工具结构就绪
   [OK] Launcher check passed.     # wrapper 可用
   [OK] Project ready. ...         # 仅目标检查通过时
   [ACTION] Project not ready. ... # 目标检查未通过时
   ```

明确说明：`Ready` 不代表 key 已认证，也不代表能复现 Bug；目标 `ready` 只代表
Python/pytest/模块/目录/可选收集检查通过。

## 8. 接口汇总

`install.cmd` / `install-windows.ps1` 新参数：

| 参数 | 含义 |
| --- | --- |
| `-NoPythonInstall` | 禁止自动下载/安装 Python |
| `-Project <dir>` | 目标项目只读检查 |
| `-ProjectPython <exe>` | 目标解释器覆盖 |
| `-TaskConfig <json>` | 目标 task 配置，用于模块/根目录/测试目录 |
| `-CollectOnly` | 运行 pytest 收集（需要 `-Project`） |

`install.py` 新参数：`--project`、`--project-python`、`--task-config`、
`--collect-only`。`install-windows.ps1` 负责把 PowerShell 参数转成这些参数。
`-NoPythonInstall` 只在 PowerShell 层生效。

## 9. 测试与验收

新增测试至少覆盖：

- Python 发现顺序：显式、py、PATH、仓库 `.venv`、`.local/install-env`；
  旧 Python 被拒绝。
- 无 Python + `-NoPythonInstall`：退出 2，不调用 install.py，不写宿主。
- 自动安装决策：winget 成功/失败、官方安装包签名失败、安装后找不到 Python；
  测试使用假 winget/假安装器，不下载真实安装包。
- 目标检查：Python 版本、pytest 可用/缺失、模块导入成功/失败、测试目录、
  `-CollectOnly` 成功/失败/超时、收集 0 个测试。
- 目标检查不写目标目录；子进程环境中没有模型密钥。
- 参数错误：`-CollectOnly` 无 `-Project`、`-Project` 不存在、task 不可读。
- 现有 21 项安装测试、现有凭据/安装测试不回归。
- 真实无 Python 机器或模拟 winget 的验收记录；真实目标项目的轻量 + CollectOnly
  验收。

验收标准：

1. 全新机器没有 Python，允许自动安装且有网络：能安装 Python 3.12，创建私有环境，
   接入两个宿主，`setup --check` 和启动器 `-Check` 通过。
2. 没有 Python 且 `-NoPythonInstall`：明确失败，不写宿主。
3. 有仓库 `.venv` 但 PATH 没有 Python：直接使用 `.venv`，不触发自动安装。
4. 目标项目有 `.venv` + pytest + tests：`-Project` 报 ready；`-CollectOnly` 能收集到
   测试；目标目录内容/mtime 不变。
5. 目标项目缺 pytest/模块：报 `ready=false`，工具安装仍成功；不自动安装目标依赖。
6. 安装、检查、目标检测全程没有模型 HTTP 请求；日志/报告没有密钥。
7. 文档、README 和验收记录更新。

## 10. 文档

更新 `docs/windows-install.md`：

- Python 发现顺序、自动安装、`-NoPythonInstall`、UAC/网络/签名。
- `-Project` / `-ProjectPython` / `-TaskConfig` / `-CollectOnly` 示例。
- 目标检查只读，但会导入/执行目标代码；CollectOnly 会执行 conftest/插件。
- 安装成功、工具 Ready、目标 Ready、真实复现四者分开。
- 退出码和 `.local/project-check.json` 位置。

## 11. 风险与边界

- winget 包 ID/版本、python.org 安装包 URL 和签名主体可能变化；实现时固定并测试，
  失败时给出手动安装路径。
- UAC/企业策略/无网络无法由脚本绕过。
- 目标项目检查会执行目标代码；必须显式传 `-Project`，文档写明。
- `requires-python` 只做简单下界判断，不实现完整 PEP 440。
- 目标项目可能使用非 `.venv` 的 Conda/poetry/pipenv 环境；通过 `-ProjectPython`
  显式指定，检测器不猜测。
- 不提供目标依赖安装，不提供真实模型 smoke，不把 `Ready` 当成复现成功。

## 12. 实施顺序（供后续计划展开）

1. `scripts/project_check.py` + 单元测试。
2. `install.py` 参数、目标检查编排、报告/退出码。
3. `install-windows.ps1` 的 Python 发现顺序和自动安装决策。
4. `install.cmd` 参数透传。
5. PowerShell/CMD 集成测试与假 winget/安装器测试。
6. 文档、验收记录。
7. 在真实环境验证 `.venv` 发现、目标轻量检查、`-CollectOnly`、`-NoPythonInstall`。
