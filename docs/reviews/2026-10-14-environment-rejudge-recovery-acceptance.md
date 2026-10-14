# 8627 重判收尾与 8906 Windows 临时路径修复

日期：2026-10-14。起点 `cf23ae0`，功能分支 `codex/environment-rejudge-recovery`，初始实现提交 `eafc536`，并包含下述独立审查后的清理期限修复。

## 授权范围与通过条件

用户同意先处理 8627 和 8906 的环境问题，沿用已有冻结候选，不重新调用模型。此批是现有运行器及显式修复工具的局部修复。

- 改：Runner 的 Windows 私有临时目录生命周期、重复确认时的自有临时路径归一化、8627 显式重判命令、对应测试和说明。
- 保持：原候选字节、业务源码、Controller 决策、模型/工具权限、预算和 thinking 配置、默认 official-run、既有轮次/分母/成绩。
- 通过条件：长命名 pytester 文件实际可创建；每次执行独享并清理临时目录；清理失败不能进入成功确认；重复确认仅忽略自有路径差异；8627 的全部测试节点、容器退出码、回滚及容器删除都满足条件才确认差分。

全程没有调用真实模型，也没有使用 Astra。既有 gold 仅在独立重判/修复版对照端使用，不进入生成模型。

## 8906：长路径根因确认

原配置解释器为 `.local/swt-bench/envs/py39-pytest-7.0/Scripts/python.exe`，实际 CPython 3.9.13。任务专属目录中，普通 **270 / 276 字符**文件路径都报 FileNotFoundError，同一文件的扩展路径形式可写；短路径也可写。

另独立检查该解释器：`Path(prefixed_path).resolve()` 会去掉扩展前缀。目标 pytest 的 TempPathFactory.getbasetemp 会 resolve 临时根，因此只给 TMP 加前缀不能解决下游 pytester 写入长子路径的问题。

修复仅在 Windows 的实际候选执行启用：创建系统临时根下的独占短目录，实际 TMP/TEMP 写入 spec.json；结束或异常后清理。清理前验证它仍是所选根的直接子目录、名称属于本次分配范围且不是重解析点。不删除调用方传入的任意目录。无法清理会令 raw.cleanup_ok 为 false，并记入 temp_cleanup_ok，不允许当作完整成功执行。

独立审查发现初始版本同步 rmtree 会阻塞事件循环，绕过清理期限。已改为逐条遍历/删除，在每个条目之间让出事件循环，检查取消状态及任务/清理期限的较早值；不启动遗留后台删除。嵌套重解析点不跟随，部分删除或子文件消失但根目录仍在不能报告清理成功。单次系统文件调用本身仍不可抢占，与既有协作式文件操作边界一致，不声称硬实时磁盘 I/O 隔离。

源码/候选仍在原任务 run/code 内，来源绑定和保护检查保留。外置目录是临时测试数据，不是新的源码执行位置。重复失败签名将明确记录的自有 temp_root 归一化为 `<run>/tmp`，不同文件名仍保持不同。原有独立 replay.py 未修改；此次不承诺旧解释器可以使用任意长度的自定义系统临时根或任意超长文件名。

## 8906：同冻结候选的真实执行结果

原始候选 `candidate-0e837038fd6b409fba79f748d5dba442`，`testing/test_repro.py` SHA256 为 `b941426477f7fc7b5960fc17aefed39ef3616df979d4b77ac71e35a64935e4be`，字节未改。
新原版快照与 002 原冻结快照逐文件内容哈希一致。使用同一 3.9.13 解释器，通过产品 Runner 执行原版两次、修复版一次：

| 执行 | call 结果 | 退出码 | 探针 / 绑定 / 保护 / 清理 |
| --- | --- | --- | --- |
| original 1 | 1 failed、1 passed | 1 | 均通过 |
| original 2 | 1 failed、1 passed | 1 | 均通过 |
| fixed | 1 failed、1 passed | 1 | 均通过 |

环境错误已消失，已执行到真正的错误信息断言。但候选要求 `*Using pytest.skip outside of a test is not allowed*allow_module_level*`；修复版已换成 `Using pytest.skip outside of a test will skip the entire module... allow_module_level=True`，因此仍不匹配。

结论是 **环境已修、冻结候选尚未形成差分**，不是新增 resolved。未放宽断言、未修改候选、未启动模型重新生成。该措辞依赖属于后续候选质量问题。

证据：`E:/ReproAgent/.local/environment-recovery-20261014/longpath-exact.*`、`replay_8906-final.py`、`pytest-8906-final/receipt.json` 及其 task/runs 原始记录。审查后最终 Runner 再跑原版两次、修复版一次，结果与上表一致；回执记录的 Runner SHA256 与实际代码一致。较早 `pytest-8906/` 是初始实现的执行记录，保留但不代替最终运行。

## 8627：完整显式重判入口

`PYTHONPATH=src python -m evals.repairs.sphinx_rejudge` 在 Linux/WSL + Docker 中运行，参数为原轮次、原 harness 日志根、本地镜像与新输出目录。见 `evals/repairs/sphinx-legacy/README.md`。

它校验原 round/manifest seal、来源回执身份与冻结预测哈希，检查两臂 spec 与同一候选/base 绑定，然后复制原脚本并调用现有派生器处理回滚。依赖档和脚本只读挂载；tag 先解析为本地不可变镜像 ID，运行不触发拉镜像。每臂独占容器，记录 Docker inspect 的退出码，并确认容器删除。测试原版失败是正常观测，和 Docker 客户端故障分开。

新回执包含原/派生脚本、spec、预测补丁、工具/派生器代码、依赖档、镜像、命令/挂载、日志哈希、完整测试节点、退出码、回滚和清理结果。缺节点、skip/error、超时、回滚失败或清理失败均不能判差分成立。类型为 `reproagent-repaired-environment-diagnostic-v1`，不冒充 import-reports 接受的官方执行回执。

最终提交版本实际运行，绑定 002 冻结预测 SHA256 `949a20f291b3b32e658d819a9169e89a64d241ab57437c70b36537a4ba677d81`：

| 臂 | 测试结果 | inspect 退出码 | 回滚 | 容器清理 |
| --- | --- | --- | --- | --- |
| pre | 两个 Struct 测试失败，两个 Path control 通过 | 1 | true | true |
| post | 4 passed | 0 | true | true |

结果为 candidate_differential_confirmed。最终回执工具/派生器哈希与实际源文件再次核对一致，镜像 ID 为 `sha256:a16a9d45b558c78f9aea34daed515dfefa39e060ee85f57304691d6fb118848f`。

证据：`E:/ReproAgent/.local/environment-recovery-20261014/sphinx-8627-final/receipt.json` 及该目录全部冻结输入、派生脚本、profile 和日志。较早的 `sphinx-8627-rejudge/` 是补齐代码哈希字段前的初步运行，不代替最终回执。

## 回归和独立审查

| 检查 | 实际结果 |
| --- | --- |
| runner / paths / pinned-script 基线 | 21 passed、1 skipped，7.05s |
| 新 pytester/异常清理 RED | 2 failed：长路径写入失败、临时目录未释放 |
| 新 pytester/异常清理 + runner GREEN | 7 passed，9.69s |
| 新显式重判 RED | 5 failed：入口模块不存在 |
| 重判与派生器 GREEN | 12 passed，0.58s |
| 新私有临时路径确认 RED | 1 failed：相同失败因临时根不同而无法确认 |
| 相关五组 GREEN | 29 passed，25.73s |
| 集中验收 | **119 passed、3 skipped，129.62s，exit 0** |
| 清理期限补修后 runner/verifier/Windows/controller/paths 回归 | **90 passed、3 skipped，131.40s，exit 0** |

集中验收覆盖 runner_private_temp、private_temp_confirmation、verifier、sphinx_rejudge、pinned_sphinx_script_repair、windows_long_paths、runner、controller、paths 和 swt_results，basetemp 为仓库外的 `C:/Users/Dell/AppData/Local/Temp/reproagent-environment-recovery-acceptance`。这些组有重叠，不相加为唯一测试数；实现阶段未重新跑全仓测试，合并阶段的全量回归见下文。初次及补修后日志复制保留在 `E:/ReproAgent/.local/environment-recovery-20261014/environment-recovery-acceptance.log`、`environment-recovery-cleanup-final.log`。

独立审查初轮 14 项通过，指出 1 项 Important（清理绕过期限），已修复。新增期限/取消测试先见到 4 项失败，补齐实现后通过；子文件消失误报清理成功也先看到失败后修复。审查人复核最终实现，独立 19 项通过（10.05s），无剩余 Critical/Important。审查建议进一步隔离任务期限与清理期限测试，已分别设为 0.05s/100s，单独 3 项通过（0.22s）。

## 保留的评测边界

原 holdout-diag-002 的官方成绩仍为 **4/10 / 0.4**。8627 新回执是明确带修复环境的额外确认，不修改原成绩；8906 目前只确认环境已解除阻塞。未修改 11445、5221、8474 的生成/验收策略。用户本地评测 HTML 保留。

## 合并阶段全量验证

用户另行授权“合并并 push”后，同步 origin/main，main 从 `cf23ae0` 快进合并到 `194ab3e`，没有合并冲突。在主工作区 `E:/ReproAgent` 实际执行：

```powershell
.venv/Scripts/python.exe -u -m pytest -q --basetemp C:/Users/Dell/AppData/Local/Temp/reproagent-environment-merge-full-20261014
```

结果为 **1055 passed、6 skipped，917.82s，exit 0**。未调用真实模型。完整日志在 `E:/ReproAgent/.local/environment-recovery-merge-full.log`。`git diff --check` 通过；用户未跟踪的 `docs/evaluations/2026-10-13-pitfall-experience-ab.html` SHA256 仍为 `aaa7f31a825f9c89e80bb8249f364d2c2b2be59aceb972d29c76d0b67551c0e2`，不纳入此次提交。
