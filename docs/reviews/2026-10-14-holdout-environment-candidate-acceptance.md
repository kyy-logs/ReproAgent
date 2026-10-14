# Holdout 第二批修复：环境执行与候选质量

日期：2026-10-14。隔离分支 `codex/evaluation-protocol-diagnostics`，本批起点 `869001e`。
候选规则/依赖配置提交 `284162e`；显式诊断脚本修复提交 `b9f7dcb`。

## 范围与结果

| 问题 | 根因 | 本次改动 | 已验证到哪里 |
| --- | --- | --- | --- |
| Sphinx 8627 插件无法导入 | 主环境有 pkg_resources，tox 子环境却从 virtualenv 自带包安装了不含它的 setuptools 82.0.1；项目无版本上界，未触发降级 | 显式 legacy Sphinx tox 配置，把测试环境安装约束为记录中的 setuptools 80.9.0 | 同冻结候选在两个新容器中原版 2 failed、补丁版 2 passed |
| 评测脚本最终退出 128 | `git diff HEAD <base>` 没记录工作区改动；空文件交给 git apply 报错；`git checkout <base>` 也不撤销工作区补丁 | 离线派生新诊断脚本，记录初始 tracked diff、保留测试退出码、恢复并比对 diff，空 diff 不 apply | 原版 exit 1、补丁版 exit 0，均 rollback=ok；额外验证测试失败 7 保留、回滚失败另报 125 |
| 15678 独立场景互相遮挡 | 同一个测试先跑 Eq 抛错，后续 f(x) 未执行；探索提示还写着 one focused regression test | 分析保留可区分场景，探索用独立函数或参数节点，审阅区分实际执行与不可达代码 | 新规则确实进入真实 SDK 的分析/探索/两次审阅请求；未新跑模型生成候选 |
| 8365 平台假设不成立 | 将某平台的非法字符等同于所有平台都必须清洗，候选触发与补丁恢复条件不一致 | 三处提示明确核实实际平台触发条件，只断言来源支持的恢复承诺 | 同上；未声称新生成候选已通过官方判分 |

用户授权继续修复现有缺口。本次没有修改核心状态机、预算、权限、验收枚举、源码保护、经验库、thinking 配置或 trace viewer。没有使用 Astra，也没有调用任何真实模型。

## 依赖问题的隔离复现

使用原先已有镜像：

`sha256:a16a9d45b558c78f9aea34daed515dfefa39e060ee85f57304691d6fb118848f`

断网且不挂载主机目录的临时容器中，主解释器报告 setuptools 80.9.0、tox 4.16.0、virtualenv 21.14.5，且能找到 pkg_resources。调用 virtualenv `--no-download` 新建环境后，自带种子为 setuptools 82.0.1，不能找到 pkg_resources。问题并非父环境没安装 setuptools。

`evals/repairs/sphinx-legacy/tox` 只在显式挂载时替换该容器的 tox 入口。它用 `exec` 和 `"$@"` 保留参数/退出码，通过 tox 4.16 的 `-x testenv.install_command=...` 把约束作用到真实测试环境。独立 `.pkg` 构建环境未改，其余依赖未完全锁定，不宣称任意时间的整环境复现性。

首次只加依赖约束：测试已经形成原版失败/补丁版通过，但原冻结脚本两边都在最后回滚时退出 128，不能用容器退出码当作测试结果。

## 回滚派生器的边界

`python -m evals.repairs.pinned_sphinx_script` 只生成新文件，不执行 Docker，不改原脚本，不改冻结 harness。它验证完整 base hash 和已知脚本形状，输出独占创建；未知形状或既有输出拒绝。原补丁 heredoc 内容保持不变。

派生脚本要求 `/testbed` 属于新建临时容器的可写层，不可将主机源码绑定在那里；恢复使用 git reset。恢复范围为初始 tracked 文件字节，新增 untracked 候选由容器销毁清理。不是通用宿主机/可复用仓库清理器。测试失败单独保留，回滚失败不能伪装成测试失败或成功。

第二轮显式派生脚本真实执行结果：

| 状态 | pytest 实际结果 | 容器退出码 | tracked 恢复 |
| --- | --- | --- | --- |
| 原版 pre | 两个目标断言失败，2 failed，4 warnings | 1 | rollback=ok，恢复 diff 与初始逐字节相等 |
| 补丁版 post | 两个目标节点通过，2 passed，4 warnings | 0 | rollback=ok，恢复 diff 与初始逐字节相等 |

另外在断网、无主机挂载的临时容器做了两种边界：初始 diff 为空且测试返回 7，最终仍返回 7、tracked 源码干净；git reset 被受控替身模拟为失败 93，最终返回 125 并输出 ROLLBACK_FAILED。替身仅用于该边界检查，不进入产品或真实 pre/post 对照。

## 自动回归与独立审查

| 检查 | 实际结果 |
| --- | --- |
| 开始前相关基线：model_protocol_regressions + eval_observability | 36 passed，28.73s |
| 新整链路提示交付 RED | 1 failed，缺 independent scenarios 指令 |
| 提示交付 + model_protocol_regressions + verifier GREEN | 44 passed，48.08s |
| 新脚本派生器 RED | 7 failed，缺模块 |
| 脚本派生器 GREEN | 7 passed，0.14s |
| 脚本派生器 + 提示交付 + SWT 成绩、来源、预测导出 | 38 passed，34.29s |

最后一组命令（basetemp 在仓库外）：

```powershell
.venv/Scripts/python.exe -m pytest -q tests/unit/test_pinned_sphinx_script_repair.py tests/integration/test_candidate_quality_guidance.py tests/unit/test_swt_results.py tests/unit/test_swt_provenance.py tests/integration/test_swt_predictions.py --basetemp C:/Users/Dell/AppData/Local/Temp/reproagent-holdout-repair-acceptance
```

这些组有重叠，不相加为唯一测试数；未做全仓测试。脚本回复证明规则交付和既有编排，不能证明真实模型会遵循规则或成功率提升。

独立审查第一段 `869001e..284162e` 无 Critical/Important/Minor；审查者另验真实 SDK 测试 1 passed、8.12s，确认 wrapper 的参数/退出码、Git executable/LF、prompt 的真实 blocker 与全部断言验收边界。

追加审查 `284162e..b9f7dcb` 同样无 Critical/Important/Minor，独立重跑 7 项单元测试通过（0.06s），并核对真实两臂及合成边界的原始日志。确认测试退出码、还原链失败传播、独占输出和原补丁保留；结论限本文件明确的一次性容器 tracked 还原，不扩展为官方认证或可复用仓库清理保证。两段 `git diff --check` 均通过。

## 原始证据与成绩边界

证据位于 `E:/ReproAgent/.local/holdout-env-audit-20261014/`：首次 pre/post 日志、原冻结脚本、依赖配置、replay.py；`cleanup/` 另存派生脚本、两臂日志和 receipt.json；`cleanup-boundaries.*` 为额外边界检查。新回执保留完整镜像 ID、原/派生脚本 SHA256、配置哈希、预测哈希、结果与回滚标记。

冻结预测 SHA256 保持 `baa3f84e97a22773d773d534bbc2d0e79d492439790ac9e3ee9a217f9583ad0a`，未向生成模型提供 gold 内容。

本批修复配置和派生器都是显式诊断工具，**尚未接入默认 official-run**，也不生成可导入为官方成绩的执行回执。原 holdout-diag-001 仍为原环境下的 **2/10 final**。后续正式重判应给环境/harness 修复另做版本与来源记录；本地诊断差分不能直接改成官方 resolved。未更新用户评测 HTML，未合并 main、未 push。
