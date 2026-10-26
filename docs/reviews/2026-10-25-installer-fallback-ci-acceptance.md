# 官网兜底安装与 CI 覆盖修复

基线：`main@f40cc98`。修复分支：`codex/installer-fallback-ci`。

## 问题与修改

原兜底地址 `python-3.12.14-amd64.exe` 实测 HTTP HEAD 404。新的
Python 3.13.17 Windows x64 地址实测 HEAD 200，Content-Length 29941280。
官方版本说明及安装包列表：<https://www.python.org/downloads/release/python-31317/>。

新增 `scripts/python-runtime.json`，统一记录版本、官网地址、winget ID 与安装目录。
安装器读取此配置；winget 和安装后解释器发现同步为 Python 3.13 / Python313。
仍保留官方签名验证，未签名载荷不执行。

CI 增加五个安装/项目预检测试文件，现有测试步骤共14个文件；增加 Windows Python 3.13
矩阵任务，保留 Windows / Ubuntu Python 3.11。Windows 3.13 任务执行读取同一 JSON
的 HEAD 检查，以便未来错误版本或地址产生404时直接失败。HEAD检查不下载或执行文件。

## 验证

新增回归先在旧实现上失败，受控下载记录显示请求的仍是3.12.14地址。修复后，
真实兜底分支读取配置中的3.13.17地址，并在签名无效时拒绝执行。测试下载和
进程启动由驱动替代，TEMP为私有临时目录，不下载或运行真实安装器。

- 兜底与真实Windows入口：实现者 **10 passed / 15.72秒**。
- 独立只读复核：**10 passed / 17.13秒**，无新增可操作问题。
- Workflow YAML、3个矩阵组合、14个测试文件路径已核对。
- 从Workflow提取并实际执行官网HEAD步骤：成功，HTTP 200。

整仓离线回归：**1419 passed / 3 failed / 9 skipped**，1452.12秒，exit 1。
三个失败均为既有 `tests/unit/test_paths.py::at_length` 构造器在余量1时提前结束，
要求300字符、实际299字符，尚未执行产品路径逻辑。具体失败：

- `test_a_path_past_the_limit_takes_the_long_form`
- `test_shared_path_form_promotes_a_pair_together`
- `test_the_standalone_replay_applies_the_same_rule`

参考仓库既有 `74412fb` 的集成fixture处理，仅修单测构造器：余量1时延长末级组件。
新增7个边界数据，先观察到1/102/203余量的3项失败，再修复。保留全部原有产品断言，
不修改 `src/reproagent/paths.py`。独立复核该文件 **16 passed / 1 skipped**。

整仓运行后唯一源文件差异为上述测试fixture，产品源码指纹不变。最终按Workflow实际
14文件清单，加整份路径单测复测：**189 passed / 4 skipped / 0 failed**，111.06秒，
exit 0。回执为 `ci-and-path-tests.log/xml/exit`。没有再跑第二遍整仓，不将分开结果
拼接为一次完整套件全绿。

该轮使用独立测试解释器，所有子进程导入修复分支源码，不导入 E 盘另一任务的
未提交产品代码。测试进程隔离已知 PowerShell 7 / Windows PowerShell 模块路径
冲突，不改用户配置。

回执目录：`E:/ReproAgent/repro-results/installer-fallback-ci-20261025/`，
包含 `source.json`、`final-source.json`、`check-url.log`、`complete-tests.log/xml/exit`、
`ci-and-path-tests.log/xml/exit` 和 `final-validation.json`。
指纹排除测试生成的 `*.egg-info` 元数据和字节码。

以上为首轮验证记录；该阶段尚未执行托管CI、提交、合并或push。未调用模型，未执行真实Python安装。
## 2026-10-26 合并 main 后的完整验证

合入 `origin/main@bb7177a`，保留其 CI 权限、并发与超时设置，以及环境测试修复。
最终代码快照为 `d226a61`。README 标题在 main 中变更，原打包测试因此误报；
改为解析 wheel 的 METADATA 正文，与实际 README 按行完整比较，兼容换行符差异。
该打包测试单例验证通过，随后纳入下述整仓回归。

早期合并复测遇到共享环境的 AgentScope 依赖缺失，已停止，结果不计入验收。
最终使用新建的独立虚拟环境，完整安装项目 `[dev]` 依赖，不引用共享环境的依赖目录。
导入来源已核验：产品来自本修复 worktree，AgentScope 2.0.9 来自独立环境。
测试 TEMP、TMP 和 basetemp 位于 E 盘仓库外；子进程使用同一依赖环境。

最终命令：

```powershell
& 'E:\ReproAgent\.local\installer-merge-validation-20261025\Scripts\python.exe' -m pytest -q `
  --basetemp 'E:\ReproAgent-test-temp\installer-merge-20261025\final-full' `
  --junitxml 'E:\ReproAgent\repro-results\installer-fallback-ci-merge-20261025\final-full\tests.xml'
```

测试进程设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`，隔离已知 PowerShell 模块路径冲突，
并清除模型 API key 环境变量。未调用真实模型、未执行真实 Python 安装器。

结果：**1429 passed / 9 skipped / 0 failed / 0 errors**，1205.48秒，**exit 0**。
9项跳过的原因均为 Windows 下的符号链接权限或 POSIX shell 可用性。
396份已跟踪文件在执行前后逐一核对 SHA-256，无变化。

回执：`E:/ReproAgent/repro-results/installer-fallback-ci-merge-20261025/final-full/`，
包含 `tests.log/xml/exit`、`source-commit.txt`、`source-status.txt`、`dependencies.json`、
`provenance.json` 和 `tracked-hashes.json`。早期中断记录分目录保留，不与完整成功运行混算。
托管 CI 由推送触发，其结果应另行核对。
