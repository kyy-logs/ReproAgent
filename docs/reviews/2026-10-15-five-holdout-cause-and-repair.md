# 五条历史未通过案例：原因与确定性修复

日期：2026-10-15。分析对象为 `repro-results/holdout-diag-003`；修复基线 main `aac87160e22f7c44cf9d0bd0b6d7f5d2d029e807`。方法是只读检查任务、原始 Issue、原版源码引用、候选、执行、Verdict、事件和留存的 SDK 请求。没有查看参考修复源码/金标测试，没有运行新模型或替换评测候选，没有改变旧轮记录。

## 结论

不能将五条统称为数据集错误，或用“20 步没收敛”解释。`evidence_level=NONE` 不等于从未发布候选。不同版本的五条成功实例，也不能组合成同一版本最终 5/10。

| 案例 | 003 实际事实 | 原因性质 | 本次处理与剩余边界 |
| --- | --- | --- | --- |
| 8365 | Windows 本地原版失败、修复版通过；官方 Linux added_f2p=0 | 本地平台与交付平台要求脱节 | 确认产品缺少交付环境输入及执行闭环；未把提示词改写冒充跨平台修复 |
| 8627 | 旧正式环境不能导入 pkg_resources，之前显式修复环境下的冻结候选有差分 | 环境/评测执行 | 维持此前官方环境决定；不把旁路差分算官方成功，也不替换 003 的新候选 |
| 5221 | 2 份候选、2 次执行；NOT_REPRODUCED 和 INVALID_CANDIDATE | 契约过严、工具摩擦、候选断言错误 | 修相对 Read 与已展示子区间引用；候选自满足和输出解析仍需真实生成重跑验证 |
| 8474 | 0 已发布候选、0 次执行；两版契约仍有必要缺口 | 复现输入和正确行为依据不足，探索未找到有效场景 | 修公共工具摩擦，不删除真实缺口，不伪造可执行触发条件 |
| 8906 | 16 候选、31 次原版执行；15 对重放单次语义审核均通过但确认失败 | 确定性重放误拒 + 过约束候选被语义审核放过 | 修 pytester 摘要耗时比较和重放诊断；不将机械重放通过等同于候选或官方通过 |

## 预算收敛不是缺失功能

003 三例都记录了 budget.convergence：5221 seq58、8474 seq57、8906 seq317。结构化 budget 为 used_steps=15、remaining_steps=5、next_call_index=16、mode=converging。5221/8474 各有一次预留区拒读，8906 没有。故“尚未实现阈值提醒”不能解释这三例，也不能把提高总步骤当作根因修复。

## 8365：不能怪模型不遵守未收到的 Linux 要求

003 候选 `candidate-b29e3d05542e4722b48f0b5419e33501` 的三项测试带 Windows 条件，源码在 `cases/pytest-dev__pytest-8365/task/artifacts/reproduction/candidate/testing/test_repro.py`。原始 Issue 明确描述 Windows。首份完整探索请求 content `c6ea09537a1c0968`（HTTP span `18b380981a557580`）要求的是 current platform，没有传入 Linux 交付要求。

原版 `run-cbef2fd173424504b533e8f84017d9ce`、`run-5bc9b9261d5c4f46b6ae1486c7f810c4` 为完整执行并失败，修复版 `run-6ec2e3ceece949578403ba3b81405f1a` 三项通过，故本地平台的差分确实存在。官方 metadata 表示补丁已应用、resolved=false、added_f2p=0；本次没有独立读取官方原始 skip 日志，不能把推断当作逐项核验的跳过数量。

架构缺口路径：TaskRequest/AgentContext 无交付平台字段 → evals/run.py 只构造本地执行请求 → SDK 实际请求没有 Linux 条件 → SWT 导出补丁 → official.py 才要求 Linux。已有 Verifier 会拒绝本地全跳过，但 Windows 本地执行不是跳过，因此这条规则不可能发现 Linux 的空运行。

正确后续方案是把有来源的交付要求与 IssueContract 分开：默认普通 CLI 仍为本地；SWT 明确声明目标 Linux，Controller 传到实际模型请求并存档。若要保证可交付，必须在真实目标执行环境验证有非跳过的目标测试；没有该执行时只能标明未验证。不可无条件删除 skipif、伪造 sys.platform、读取 gold 或认定所有 Windows 报告都能直接在 Linux 复现。本次没有新增目标运行时，故此架构缺口仍未修完。

## 8627：环境诊断与官方判分分开

既有来源见 `docs/reviews/2026-10-14-holdout-environment-candidate-acceptance.md`。virtualenv 的 setuptools 种子与主环境不一致，子环境缺 pkg_resources；之前显式环境修复下同一冻结预测已有 pre 失败/post 通过，但并非默认官方流程的成绩。

这里的冻结候选和 003 新生成候选不能直接互换。用户此前决定维持官方环境；本次不改变它，不改官方回执，不干扰另一会话正在实现的 repair attachment。

## 5221：发布过候选，但检查器测错了东西

初始契约把确切显示格式未知当成阻塞，后续 v2 已清空缺口。首次候选在 fixture docstring 写入 scope 单词，又在输出块寻找相同单词，因测试自己满足断言而在原版通过，拒绝为 NOT_REPRODUCED 正确。第二候选还假设 verbose fixture 名独占一行；实际为 `name -- location`，定位器自身失败。不能用增加步数或放松验收保证解决。

原始请求 `fed002432e8b3ea4` 已含 neutral fixture names/docstrings 指导。再次加入同义提示不能声称是新机制。审核还把普通 `result.ret == 0` 控制一概当成无依据，这属于可单独评估的审核过严，不能因此放过真正错误的定位断言。

公共工具摩擦是可确定的：Read 描述允许快照相对路径，原生 SDK 却提前要求绝对路径；引用 1303–1355 等真实子区间虽完整落在已展示 1237–1357 内，也被 exact-tuple 策略拒绝。本次修正这两点，并保留权限/哈希边界。

## 8474：警告分支不等于错误规范

原始六行 Issue 只说 Sphinx 3.3 后 singlehtml/latex 出现 `no number is assigned for table`，没有文档/标签/配置，也没明确应改变哪个有效场景。源码能说明警告如何发生，不能单凭该分支证明警告一定错误。singlehtml 只组装 master 和 toctree，不能假定孤立文档会被构建。

最后尝试的候选（SDK input `e5b2b7f32ee7e658`）仅构建已有 testroot=root，没有设置假设中的孤立文档/表格，并用任意内部链接代表目标 numref 正确。绕过契约仍没有有效证据。当前不足以断言数据集本身有错或不可复现；应继续从允许的原版资料寻找有依据的合成输入，确实缺必要事实时返回明确缺口。

## 8906：确认器被日志中的秒数绊住

首对原版执行 `run-51389f5244d0425685f8d8b57715d424` 与 `run-17033788e0744edd943cb215772abd96`，以及其余 14 对，规范化 run/temp 路径后，唯一差别是 LineMatcher 失败文本里嵌套 pytest 摘要的 `0.67s` 与 `0.65s` 等耗时。

两次 REPRODUCED、测试清单、完成清单、前置条件及保护/清理/探针均一致。但确认器逐字比较 crash.message，返回 REPLAY_UNCONFIRMED，没有明确指出差异。模型遂重复提交相同文件：16 个候选文件哈希一致，仅 hypothesis 变化。已有执行指纹只复用已证明的证据容量阻塞，不能错误扩展成所有语义失败的缓存。

修复只在具备 pytester/outcomes 异常帧、LineMatcher 诊断、session 与 short-summary 分隔符时处理实际输出的最终摘要行；期望模式含数字或时间描述时保留全文。业务输出、较早的摘要形状行、错误数量、具体目标诊断、位置、类型、未证实来源与不支持的 regex 变体均保持保守比较。

确认失败现返回受控的首个未通过程序检查，如 `failure_signature_matches`、`test_inventory_matches`。`replay_unmet_checks` 随反馈进入下一阶段并保存到 verification.feedback 事件，原先的单次语义 Verdict 不会被改写。

重要残留：8906 候选要求旧错误句与新选项同一行，而 Issue 只要求说明 allow_module_level=True。已有提示已禁止此过约束（wire `4bdc30a5970bcdb9`），30 次语义审核仍放过它。机械重放确认不是语义正确性证明，本次没有伪造新语义判断。

## 验收记录

- 相关基线：28 passed。
- RED：相对 Read、已展示子区间、摘要耗时重放和缺失的重放检查字段均被真实失败测试捕获。
- 首次关联回归：127 passed，96.39s。
- 独立非 Astra 审查发现数值期望 `*0.10*` 被误规范化；已新增并复现反例，收紧为任何含数字期望都不规范化，并增加摘要形状业务输出与缺来源反例。
- 最终已有数据离线重查：15 对，修复前 0 确认，修复后 15 确认。文件 `E:/ReproAgent/.local/holdout-five-20261015/replay-receipt.json`。沿用旧语义 verdict，仅核验程序重放机制，没有生成新候选、运行模型或计算官方成绩。
- 收紧后的重放/事件边界：10 passed，27.63s。
- 最终全仓首轮：1163 passed、1 failed、6 skipped，1055.67s。唯一失败为 test_agent.py 对提示词固定英文短语 displayed whole 的断言；语义约束仍由真实工具/SDK 测试覆盖。移除该脆弱短语检查后，test_agent + domain tools + next-wire 回归 37 passed，22.45s。没有把这个首轮写成零失败，也没有再次重复整套测试。
- 完整运行结束后的唯一失败项复验：pytest --lf --lfnf=none，1 passed，0.36s；没有残留已知失败项。
- 独立非 Astra 审查最终无可操作遗留发现：实际 LineMatcher 反例确认数值期望、较早业务摘要行、缺少 session 来源都会保持差异。审查未运行 provider 或全套测试；全套由本会话运行。
- 中途因审查修正停止的一次全仓运行不计通过；最终 git diff --check 用于检查差异格式。

本次实现位于隔离分支，不包含主工作区另一会话的环境回执/成绩修改。后续重跑须新 run_id、冻结源码/经验/环境，优先检验 8906 的新候选语义与差分，再观察 5221；8365 需要另行补齐交付平台执行链，8474 不能强行造通过。
