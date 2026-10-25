# Windows 一键安装验收

## 范围与来源

用户批准的最小改造：在现有 `reproagent setup` 外增加 Windows 双击入口，让使用者不必先创建虚拟环境或输入解释器绝对路径。安装与真实复现分别进行；安装不调用模型。

隔离工作区：`C:/Users/Dell/.codex/worktrees/windows-one-click-install/ReproAgent`，基线 `cef94f1`。

主工作区的现有 setup 实现尚未提交。为避免重写或改动这批实现，将其23个必要依赖文件原样复制到隔离工作区进行组合验证，具体SHA256记录于：

`E:/ReproAgent/repro-results/windows-one-click-install-20261025/integration-dependency-baseline.json`

这些既有文件不是本次安装入口的实现成果，未替它们作提交或合并；后续集成应保留原作者改动。

本次新增文件（另在 README 安装段增加入口链接）：

- `install.cmd`
- `scripts/install-windows.ps1`
- `scripts/install.py`
- `docs/windows-install.md`
- `tests/unit/test_windows_bootstrap.py`
- `tests/integration/test_windows_bootstrap_entry.py`
- `tests/integration/test_windows_bootstrap_offline.py`
- 本验收记录

## 行为

双击入口调用 PowerShell 查找 Python 3.11+，然后由仅依赖标准库的 Python 引导程序创建 `.local/install-env`、安装运行包和依赖，并调用既有 setup 安装、检查宿主入口。

使用普通包安装，不使用 editable。更新源码需要重新运行安装入口同步运行包；模型配置和凭据来源仍通过 `--repro-root` 绑定源码目录。

安装器验证实际模块位于专用环境、解释器前缀一致、包的 `direct_url.json` 来源匹配当前源码，且并非 editable。宿主入口是否冲突、是否备份和哪些凭据来源可用，仍由现有 setup 判定。

不修改开发用 `.venv`、全局 Python 或目标项目依赖。只有完整专用环境的自有标记匹配时才复用；未知目录保留。并行安装锁不被另一实例删除。失败窗口保留，命令行可用 `-NoPause`；错误返回非零退出码。

缺少模型配置或凭据时，安装可以完成，但不显示 Ready。安装和认证有效、目标环境可运行、真实 Bug 复现成功分别报告。

## 测试与审查发现

初始既有 setup 基线20项通过。入口缺失的回归先观察到断言失败。

新增来源绑定回归先证明旧流程可能接受其他源码的导入；修复后对模块路径、解释器前缀和安装来源进行程序校验。

独立审查实际复现并修复三个安装问题：

1. 用户 pip 配置和目的地变量可能将包写到专用环境之外。现在清除目的地变量、禁用 pip 配置文件、显式 `--no-user`，保留环境中的镜像/代理设置。真实离线对照确认外部目标目录未被创建。
2. 控制台代码页无法编码解释器路径时，Python 探测会误报未安装。现在使用 ASCII-safe JSON 传递路径；实际包含罕见汉字 `𠮷` 的解释器目录回归通过。
3. Python 3.12 在 CP936 下读取 UTF8 editable `.pth` 中文路径可能无法导入。用户安装改为普通包安装；临时中文源码目录的实际安装、导入、宿主接入和重复安装通过。

早期离线测试的依赖桥接引入另一份 ReproAgent，来源校验正确阻止继续；测试桥接随后剔除了其他 ReproAgent 源码路径。测试末尾也改为 ASCII-safe JSON 读取导入路径，避免测试自身的代码页假设。相关失败没有计作通过。

最终专项测试 **21 passed**，实现者21.93秒，独立审查36.26秒。包括真实 CMD→PowerShell→Python、真实 pip 普通安装和 setup、重复安装宿主文件内容/mtime不变、假凭据不落盘。离线安装测试将模型URL指向临时本地 HTTP observer，安装及检查请求数为0，不使用真实服务或密钥。

独立审查最终没有未关闭的 Critical / Important 问题。

## 完整回归

整仓离线测试完成：**1311 passed / 1 failed / 9 skipped**，1333.54秒，exit 1。最终冻结指纹为 `final-source.json`，输出为 `complete-tests.log`、`complete-tests.xml`、`complete-tests.exit`，都位于上述回执目录。所有新增安装器测试通过；不将专项通过或分开复测拼接为一次整仓全绿。

唯一失败为既有依赖测试 `tests/unit/test_integration_credentials.py::test_dpapi_file_round_trip`。单独复现仍失败；读取假凭据子进程stderr后确认为 `Microsoft.PowerShell.Security` 无法加载。测试子进程继承了当前宿主的 PowerShell 7 `PSModulePath`，使 Windows PowerShell 5.1 命中了不兼容模块。该测试使用假值，未读取真实密钥。

仅将测试进程的 `PSModulePath` 设为 Windows PowerShell 5.1 自带 Modules 目录后，整份凭据测试 **26 passed**，1.46秒，回执为 `credentials-clean-environment.xml`。环境变量在父进程中恢复，不改用户配置、既有凭据代码或既有测试文件。本次没有再跑第二遍整仓，不声称完整套件一次全绿。

## 本机使用与交付

只读检查主目录现有接入状态：模型配置和DPAPI凭据来源存在，但并未做认证；Claude Code 为旧入口冲突，Codex 用户入口缺失。对本机首次更新，可以在 `E:/ReproAgent` 下运行 `install.cmd -Force`，既有安装器会先备份冲突文件再替换。

新增入口、说明和测试已复制到主目录；README仅补入口链接，保留原有未提交文档内容。既有23个依赖文件与本次快照逐一核对未变，复制不覆盖它们。最终交付回执为 `final-validation.json`。

本次未运行付费模型案例、未执行真实用户级安装、未提交、合并或push。
