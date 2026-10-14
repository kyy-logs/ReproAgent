# 评测修复合并验证

日期：2026-10-14。用户明确要求合并并 push。两批修复从 `ee1b802` 快进合并，最终产品/测试提交为 `4dcff76a0adad2451a31bfd46f7674f77e750654`。

## 合并后发现并修正的测试误报

首次全量回归中，`tests/integration/test_agentscope_explorer.py::test_the_fixed_version_never_reaches_the_exploration` 失败。它禁止序列化输入出现任何 `fixed` 单词，而新策略明确写了禁止查看 hidden fixed version；因此将策略文字误报为修复版泄漏。该次执行在定位原因后中断，不计为通过；日志保留在 `.local/eval-protocol-merge-interrupted.log`。

`4dcff76` 将断言改为检查隐藏修复版的专属目录名与源码 canary。canary 仅在固定版代码末尾增加注释，不改变函数行为。DONE、DIFFERENTIAL_VALIDATED、仅一次探索请求的断言保留。该文件独立验证 **7 passed，25.89s**，追加只读审查无问题。未修改产品逻辑来规避测试。

## 最终全量回归

在 `E:/ReproAgent` 的 main 上执行：

```powershell
.venv/Scripts/python.exe -u -m pytest -q --basetemp C:/Users/Dell/AppData/Local/Temp/reproagent-main-eval-protocol-merge-final-20261014
```

实际结果：**1036 passed，6 skipped，887.39s，exit 0**。

完整日志：`.local/eval-protocol-merge-final.log`。`git diff --check` 通过。此后本提交仅增加本验收文档，没有新的产品或测试改动。

## 保留的边界

- 本轮测试未调用真实模型，未使用 Astra；之前的 Sphinx 8627 容器诊断记录仍与正式成绩分开。
- 默认评测逐案例 trace、协议诊断、候选独立场景与平台规则均合入 main。
- Legacy Sphinx 的依赖配置与脚本派生器仍为显式诊断工具，未自动接入默认 official-run，原 holdout-diag-001 的 2/10 final 不变。
- 用户未提交的 `docs/evaluations/2026-10-13-pitfall-experience-ab.html` 未纳入提交，SHA256 保持 `aaa7f31a825f9c89e80bb8249f364d2c2b2be59aceb972d29c76d0b67551c0e2`。

两批范围、真实容器对照和独立审查分别见 [协议诊断验收](2026-10-14-evaluation-protocol-diagnostics-acceptance.md) 与 [环境及候选验收](2026-10-14-holdout-environment-candidate-acceptance.md)。其中“尚未合并”的文字是对应实现阶段的历史状态，本记录补充本次合并事实。
