# Bug 复现报告：validators-432

此文是根据保存的任务、执行日志和最终评估回执整理的中文阅读报告。本次没有重新调用模型或运行目标测试，也没有修改原始复现包。

## 结论与验证范围

已生成可执行回归测试。问题版本在两个新副本中表现为 **1 failed、1 passed**；同一测试在修复版中 **2 passed**。导出包随后在额外的新原版、新修复版副本独立重跑，结果相同。

| 项目 | 保存的结果 |
| --- | --- |
| 最终任务状态 | DONE |
| 证据等级 | DIFFERENTIAL_VALIDATED |
| 导出状态 | published |
| 任务耗时 | 79.41 秒，不含额外独立导出重跑 |
| Agent 动作 / 模型请求 | 7 / 11 |
| 候选数 / 任务内执行次数 | 1 / 3 |
| 目标模块 | validators.mac_address |
| pytest | 9.1.1 |
| 人类独立审查 | 尚未完成 |

此结论证明本案例的生成测试能区分问题版与所提供的修复版，不证明通用复现成功率或完整根因。

## 问题描述

MAC 地址校验错误地接受混用冒号和连字符的输入：

```python
mac_address("aa:bb-cc-dd:ee:ff")
```

保存的原始描述要求：混合分隔符输入应返回 `validators.ValidationError` 对象，实际返回 `True`；统一使用冒号的 `aa:bb:cc:dd:ee:ff` 应继续返回 `True`。这里应检查返回值，不使用 `pytest.raises`。

输入由评估者根据历史问题整理，来源地址记录为 [python-validators/validators PR 432](https://github.com/python-validators/validators/pull/432)。

| 版本 | 评估锁定的提交 |
| --- | --- |
| 问题版 | 54404420a226c2a3c1603b81cb9958ef197b52a7 |
| 修复版 | 9bc7e82f7bf0fb3990eb287e11323fb6ca1c87cf |

## 生成的回归测试

以下断言摘自 [已导出的候选测试](../../repro-results/expanded-final/validators-432/artifacts/reproduction/candidate/repro_tests/test_repro.py)：

```python
from validators import ValidationError, mac_address


def test_mixed_separators_are_invalid():
    assert isinstance(mac_address("aa:bb-cc-dd:ee:ff"), ValidationError)


def test_uniform_colon_separators_still_accepted():
    assert mac_address("aa:bb:cc:dd:ee:ff") is True
```

第一条检查报告中的错误输入，第二条检查正常输入仍被接受。候选执行时安装到仓库副本的 `repro_tests/test_repro.py`。

## 实际执行证据

| 执行 | 结果 | 退出码 | pytest 耗时 |
| --- | --- | --- | --- |
| 问题版首次 | 混合分隔符失败，正常输入通过 | 1 | 0.66 秒 |
| 问题版新副本确认 | 同一测试、同一关键失败 | 1 | 0.51 秒 |
| 修复版对照 | 两个测试均通过 | 0 | 0.24 秒 |
| 导出包独立重跑：新问题版 | 1 failed、1 passed | 1 | 本文未列 |
| 导出包独立重跑：新修复版 | 2 passed | 0 | 本文未列 |

原版两次关键失败均为：

```text
assert isinstance(mac_address("aa:bb-cc-dd:ee:ff"), ValidationError)
AssertionError: assert False
where False = isinstance(True, ValidationError)
where True = mac_address('aa:bb-cc-dd:ee:ff')
```

对应首次日志：[stdout.log](../../repro-results/expanded-final/validators-432/runs/run-500f30520afe425d8bb9a53f1fe3982c/stdout.log)。

Probe 记录目标模块来自各自运行副本的 `src/validators/mac_address.py`，三次任务内执行均记录 3 次目标调用。独立导出重跑回执也确认节点集合一致、Probe 完整、源码来源属于新副本：问题版一个失败、一个通过；修复版两个通过。

## 输出文件

| 文件 | 用途 |
| --- | --- |
| [task.json](../../repro-results/expanded-final/validators-432/task.json) | 最终 DONE、证据等级、耗时和导出状态 |
| [report.json](../../repro-results/expanded-final/validators-432/artifacts/reproduction/report.json) | 结构化报告、候选/快照哈希、预期来源、运行与证据映射 |
| [manifest.json](../../repro-results/expanded-final/validators-432/artifacts/reproduction/manifest.json) | 导出包文件内容校验 |
| [test_repro.py](../../repro-results/expanded-final/validators-432/artifacts/reproduction/candidate/repro_tests/test_repro.py) | 可安装到兼容目标副本的回归测试 |
| [replay.py](../../repro-results/expanded-final/validators-432/artifacts/reproduction/replay.py) | 无模型参与的独立安装与执行工具 |
| [最终评估回执](../../evals/cases/expanded-final-results.json) | validators-432 的版本、用量、独立重跑结果和本地证据哈希 |

完整包位于 `E:\ReproAgent\repro-results\expanded-final\validators-432\artifacts\reproduction`。复跑方法见 [输出说明](../technical-highlights-and-output.md)。

## 已知问题与限制

原契约的模型文本把正确行为写成了“raises ValidationError”，与保存的原始输入所说的返回错误对象不一致。生成测试正确检查返回值，语义核验也明确指出了措辞冲突；修复版及独立重跑通过。但不能宣称模型契约与原始描述完全一致，后续需将返回值/抛出异常机制结构化校验。本文未改写原契约以掩盖这一问题。

包内报告保存的是导出阶段状态 EXPORTING；最终任务状态在 task.json 中为 DONE。历史证据仍记录迁移前的 C 盘路径，兼容链接使它们继续可访问。

目标环境由评估者提前准备；文件副本不是安全沙箱，xdist 不在当前支持范围。费用记录为 unknown，没有根据 token 数推测实际金额。
