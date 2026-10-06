# ReproAgent 技术亮点与输出说明

本文依据当前实现和已保存的真实案例编写。当前工作目录为 `E:\ReproAgent`。

ReproAgent 将 Bug 描述变成可执行的 pytest 回归测试，并交付测试、核验记录和独立重跑工具。最有价值的部分是把“失败的测试”与“复现了描述中的问题”区分开，并保存可审查的依据。

## 已实现的技术亮点

| 能力 | 当前实现 | 解决的问题 |
| --- | --- | --- |
| 规则核验与语义核验 | Verifier 先检查候选、契约、快照、Probe、源码保护和清理状态，再核对触发条件、正确行为来源及失败证据；规则失败不能被模型覆盖 | pytest 报错可能来自语法错误、fixture 缺失或无关断言，不能单凭退出码判为复现 |
| 实际执行来源采集 | pytest Probe 记录收集和 setup/call/teardown、目标模块来源和目标调用；Runner 核对来源属于运行副本 | 识别测试是否跑到了本次源码，而不是环境里旧的安装包；模型审查继续核对是否触发目标问题 |
| 冻结与重复验证 | 冻结源码和候选，绑定哈希与契约版本；在两个新副本中执行同一候选，比较测试节点、失败位置及关键现象 | 降低工作目录污染、候选变化和偶然失败导致的误报；两次一致不等于证明所有非确定性问题稳定 |
| 可选修复版对照 | 同一候选在独立修复版副本执行；修复材料不进入生成上下文 | 区分重复观察与原版失败、修复版通过的差异验证，降低从修复材料直接照抄测试的风险 |
| 独立复现包 | 导出候选原始字节、证据、判定、来源、环境和文件哈希；提供只依赖标准库的 replay.py | 接收者使用兼容源码与已有 pytest 环境即可重跑，不需要 ReproAgent 或模型接口 |
| 有边界的 Agent 循环 | 模型使用结构化动作；Controller 管理状态和最终验收；时间、步数、输出量和有限协议纠正统一受控 | 避免无限尝试和模型自报成功；缺环境或缺预期时保留明确诊断 |

代码入口：[Verifier](../src/reproagent/core/verifier.py)、[Controller](../src/reproagent/core/controller.py)、[Runner](../src/reproagent/runner.py)、[pytest Probe](../src/reproagent/adapters/languages/python_pytest/probe/reproagent_pytest_probe.py)、[Exporter](../src/reproagent/exporter.py)、[独立重跑工具](../src/reproagent/resources/replay.py)。

模型接口、语言适配器和执行后端通过 Protocol 分开，便于后续扩展。不过当前只实现 Python/pytest 和本地执行，一些配置仍包含 Python 专用字段；不能据此承诺接入任意语言都无需改核心。

## 输出文档与文件

**新导出包自动生成中文 `report.md`，用户首先阅读它；`report.json` 保存完整机器记录。** 报告根据保存的结构化证据填充 [固定模板](../src/reproagent/resources/report.md.template)，不额外调用模型。历史复现包保持原样，已有中文案例文档是之前整理的阅读版本。

模板使用 Python 标准库 `string.Template`。可修改标题、章节顺序和静态说明，保留以下占位符；运行时填入的文本已处理 Markdown 转义，缺失字段不会由模型补造：

| 占位符 | 填入内容 |
| --- | --- |
| `$title` | 成功报告或未确认复现的诊断标题 |
| `$conclusion` | 证据结论、导出时状态、停止原因 |
| `$review` | 用户审查清单和候选测试链接 |
| `$problem` | 模型理解的触发/预期/实际现象，以及原始来源节选 |
| `$evidence` | 运行结果、日志链接及模型核验意见节选 |
| `$replay` | 环境和独立重跑方法；诊断包不提供成功重跑命令 |
| `$limitations` | 不确定项、契约假设、缺失信息和环境限制 |
| `$details` | 完整 JSON 报告及文件哈希清单链接 |

源码安装中直接修改模板文件，后续导出使用修改后的模板；已安装 wheel 的环境需重新构建和安装更新包。模板里的字面美元符号写作 `$$`。每个报告最多 64 KiB，章节和列表有长度限制，超过部分明确标为节选并指向完整 JSON。模板渲染受导出收尾期限限制。

原始 `input/issue.md` 独立于模型契约引用保留，避免契约修订后遗漏最初要求。报告集中阅读入口；候选测试及支持数据保持既有路径，没有为了简化界面另复制一份测试，也不清理历史记录。

成功任务通常有以下布局：

```text
<任务目录>/
├── task.json                    最终任务状态、证据等级、耗时和导出状态
├── input/issue.md               保存的输入描述
├── contracts/                  预期行为、来源和契约版本
├── candidates/                 已冻结候选及清单
├── runs/<run-id>/               每次执行的命令、环境关联、日志和 Probe
├── verdicts/                   语义核验结果及证据引用
├── events.jsonl                状态转换、动作和模型用量等事件
└── artifacts/reproduction/
    ├── report.md               用户首先审查的模板报告
    ├── report.json             结构化报告及证据映射
    ├── manifest.json           复现包文件列表及 SHA256
    ├── candidate/              可安装到目标仓库的回归测试及支持文件
    ├── sources/                正确行为的引用来源
    ├── verdicts/               与已接受候选绑定的核验记录
    ├── evidence/               导出的运行证据
    ├── probe/                  pytest 采集插件
    └── replay.py               包完整性校验、候选安装和独立执行
```

`report.json` 包含包类型、是否已验证、证据等级、候选和快照清单、契约、选择器、原版/修复版运行摘要、日志/来源/判定映射、Python/pytest 环境及限制。原始失败位置和完整日志通过映射查找。

包在 EXPORTING 阶段形成，因此当前包内 `report.json.status` 可能为 EXPORTING；Markdown 也明确标为“导出时状态”。最终状态以任务根目录的 `task.json` 为准，不应误读为任务仍在运行。

未验证任务产生 `artifacts/diagnostic/`，同样包含 `report.md`，标题为“未确认复现”，保留终止原因、已有原始描述和可用诊断，不能当作已确认的成功复现包。

## 如何理解结论

| 字段/等级 | 含义 |
| --- | --- |
| SINGLE_OBSERVATION | 一次观察得到支持，尚未完成重复确认 |
| REPEATED_OBSERVATION | 两次新副本运行支持相同问题 |
| DIFFERENTIAL_VALIDATED | 在重复确认基础上，兼容修复版的同一测试通过 |
| DONE | 当前成功流程和导出完成；仍需结合 evidence_level 判断证明范围 |
| BLOCKED / NEEDS_INFORMATION / EXHAUSTED / FAILED | 环境阻塞、需要信息、预算耗尽或执行失败；检查 stop_reason 和诊断 |

差异验证不等于完整根因证明。语义判断仍可能误判，文件副本也不是安全沙箱。环境跑不起来时，当前版本先报告阻塞，自动修依赖、启动服务或容器隔离属于后续方向。

当前费用在缺少计价信息时保持 unknown；现有模型适配器不能保证调用前费用上界，因此不支持硬费用限制。

## 查看真实案例

- [validators-432 中文复现报告](reports/validators-432-reproduction.md)
- [案例最终任务状态](../repro-results/expanded-final/validators-432/task.json)
- [案例原始结构化报告](../repro-results/expanded-final/validators-432/artifacts/reproduction/report.json)
- [案例生成测试](../repro-results/expanded-final/validators-432/artifacts/reproduction/candidate/repro_tests/test_repro.py)
- [最终整轮评估回执](../evals/cases/expanded-final-results.json)

已有最终评估记录覆盖提前准备环境的 7 个仓库、20 个历史 Bug，20 个均完成差异验证和导出包独立重跑。它是针对性诊断样本，不能推算一般复现率；独立人类评审仍待完成。

只读查看任务：

```powershell
cd E:\ReproAgent
.\.venv\Scripts\reproagent.exe inspect repro-results/expanded-final/validators-432
```

使用导出包时，将以下占位路径换为实际路径。仓库必须是兼容版本的全新副本，目标解释器已准备依赖和 pytest，输出目录必须不存在：

```powershell
python E:\ReproAgent\repro-results\expanded-final\validators-432\artifacts\reproduction\replay.py `
  --repo '<全新的目标仓库副本>' `
  --python '<目标环境的 python.exe>' `
  --output '<尚不存在的结果目录>' `
  --install
```

`--install` 会把候选测试放入该仓库副本，已有同名文件会被拒绝覆盖。问题版本返回非零 pytest 退出码是预期现象，需要核对具体失败；修复版应通过。独立重跑工具校验包内文件内容，不负责自动安装依赖或自动判定是否与历史失败语义相同。
