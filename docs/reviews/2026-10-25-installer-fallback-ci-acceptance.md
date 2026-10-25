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

尚未执行托管CI；未调用模型、未执行真实Python安装、未提交、合并或push。
