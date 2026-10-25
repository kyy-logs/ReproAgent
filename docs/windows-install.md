# Windows 一键安装

下载并解压完整项目，或在已有源码目录中，双击根目录的 **`install.cmd`**。
不需要先创建或激活虚拟环境，也不需要输入 Python 的绝对路径。

安装需要 Python 3.11+ 和可用的网络连接。脚本会依次：

1. 查找符合版本要求的 Python。
2. 在项目的 `.local/install-env` 创建专用工具环境。
3. 安装 ReproAgent 及运行依赖。
4. 调用现有 `reproagent setup` 安装 Claude Code、Codex 用户级入口，再检查状态。

安装窗口会保留到按 Enter 关闭；失败时可以直接看到原因。安装和检查不调用模型。
目标项目的 Python、pytest 和业务依赖仍由目标项目提供。

## Python 获取顺序

安装器按以下顺序查找 Python 3.11+：

1. `-Python <path>` 显式指定
2. `py.exe -3`
3. PATH 中的 `python.exe` / `python3.exe`
4. 当前仓库的 `.venv\Scripts\python.exe` / `venv\Scripts\python.exe`
5. 已存在的 `.local\install-env\Scripts\python.exe`
6. 自动安装（除非传 `-NoPythonInstall`）

显式 `-Python` 不存在或低于 3.11 时会直接报错，不会自动改用其他解释器。

自动安装优先使用 winget：

```text
winget install --id Python.Python.3.12 -e --source winget --silent
```

winget 不可用或失败时，下载 python.org 的 3.12 per-user 安装包，校验
Authenticode 签名（签名主体必须包含 `Python Software Foundation`）后静默安装。
安装器不修改 PATH，后续使用绝对路径；可能弹 UAC，企业策略或网络限制会导致失败。
失败时不会写入 Claude/Codex 入口。

离线或受控环境可以传 `-NoPythonInstall`，只使用已有的 Python：

```powershell
.\install.cmd -NoPythonInstall
```

## 目标项目环境检测

安装完成后可以只读检查目标项目：

```powershell
.\install.cmd -Project E:\work\myproject
.\install.cmd -Project E:\work\myproject -TaskConfig E:\work\myproject\task.json
.\install.cmd -Project E:\work\myproject -ProjectPython E:\work\myproject\.venv\Scripts\python.exe
.\install.cmd -Project E:\work\myproject -CollectOnly
```

默认检查：

- 目标 Python：`-ProjectPython` → task 的 `language.python` → 项目 `.venv` → `venv` →
  `py -3` → PATH
- Python 版本；能读取 `pyproject.toml` 的 `requires-python` 时一并报告
- `import pytest` 和 pytest 版本
- 测试目录（有 task config 时用 `candidate_parent`）
- 有 task config 时，检查 `target_modules` 能否导入，使用 `source_roots` 设置
  `PYTHONPATH`

加 `-CollectOnly` 才会运行：

```text
python -m pytest --collect-only -q -p no:cacheprovider
```

**目标检查只读**：不安装目标依赖、不修改目标源码；但导入目标模块会执行目标代码，
`-CollectOnly` 还会执行目标项目的 conftest/插件，因此只在明确传入 `-Project` 时执行。

结果打印到控制台，并写入 ReproAgent 仓库的 `.local\project-check.json`。
`Project Ready` 只代表上述检查通过，不代表 key 已认证、目标测试会通过或 Bug 能复现。

退出码：

- `0`：工具安装成功（目标检查未通过也仍是 0，用 `[ACTION]` 和报告区分）
- `2`：参数错误、Python 自动安装失败、工具安装失败

## 安装完成后

重启 Claude Code 或 Codex 会话，然后输入：

```text
# Claude Code
/reproagent 帮我复现当前项目中……这个 Bug

# Codex
$reproagent 帮我复现当前项目中……这个 Bug
```

入口会帮助准备任务配置；提供 Bug 描述即可开始收集信息，缺少正确行为或目标解释器时会询问。
首次真实运行会使用 ReproAgent 自己的模型配置和 API key，产生模型用量。

`[OK] ReproAgent installed` 表示工具和入口已安装；`[OK] Ready` 表示现有配置检查通过。
配置检查不证明密钥已认证，也不证明目标项目可以运行或 Bug 可以复现。

## 模型配置和凭据

默认复用 `examples/model.deepseek.json`。已有环境变量、项目 `.env` 或本机支持的
`.local/deepseek.key` 凭据来源会按现有安装器规则检测，见
[一键接入说明](ai-tool-integration.md)。安装脚本不会索取、复制或打印密钥。

如果提示 credentials 缺失，需要配置模型文件的 `api_key_env` 指定的变量。
例如使用默认 DeepSeek 时，可以在项目根目录的 `.env` 中设置
`DEEPSEEK_API_KEY=你的密钥`，保存后再次双击安装入口检查。
`.env` 已被 Git 忽略；不要把它上传或把密钥发到聊天里。

自定义模型配置可以在 PowerShell 中指定：

```powershell
.\install.cmd -ModelConfig C:\work\model.json
```

## 常见问题

| 提示 | 处理 |
| --- | --- |
| Python 3.11+ was not found | 安装器会尝试 winget/官方安装包；若被 `-NoPythonInstall`、UAC、企业策略或网络阻止，请安装 [Python](https://www.python.org/downloads/windows/)，启用 Python launcher 或加入 PATH，或用 `-Python` 指定 |
| 目标项目未 Ready | 查看控制台和 `.local\project-check.json`；补目标项目的 Python/pytest/依赖，或用 `-ProjectPython` 指定解释器 |
| Dependency installation failed | 检查窗口中的 pip 错误和网络连接，再次双击重试 |
| Host entry conflict | 已有入口保持原样；先核对文件。确认需要替换时执行 `.\install.cmd -Force`，现有安装器会先备份 |
| 配置未就绪 | 按提示补模型配置或凭据；安装完成与运行就绪分别报告 |
| Another installation may be running | 等另一安装结束。若上次崩溃，确认没有安装进程后再移除 `.local/install.lock` |
| install-env is not owned | 脚本保留该目录；先把自己的目录移到其他位置再重试 |

高级选项：

```powershell
.\install.cmd -Python C:\Python312\python.exe
.\install.cmd -Target claude-code
.\install.cmd -Target codex
.\install.cmd -NoPause
.\install.cmd -NoPythonInstall
.\install.cmd -Project E:\work\myproject -CollectOnly
```

安装环境与开发用 `.venv` 分开，不修改全局 Python 或目标项目环境。
为了固定安装目的地，脚本忽略 pip 配置文件和目的地重定向变量；需要镜像或代理时，
使用标准 `PIP_INDEX_URL`、`PIP_EXTRA_INDEX_URL`、`PIP_PROXY`、`PIP_CERT` 等环境变量。

运行包安装到专用工具环境，模型配置和凭据来源仍绑定当前源码目录。
更新源码后再次双击安装入口同步运行包；移动项目目录后也应重新安装入口。
卸载宿主入口可以使用 `.local/install-env/Scripts/python.exe -m reproagent setup --uninstall`；
此操作沿用现有安装器的所有权保护，不删除用户修改过的文件或模型凭据。
