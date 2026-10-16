# 可操作执行反馈修复验收

## 已确认范围

用户在2026-10-16认可先修两项：超限材料的具体定位、真实调用异常的反馈。沿用隔离工作树 `codex/holdout-execution-reliability`；基线 `dd03556`。这是既有反馈链的局部变更，不新增Agent/工具/模型调用，不增加20步预算，不改变验收判定，不处理第三项契约语义争议。

## 实现

- `ReviewContextTooLarge` 附带最多四项 `material_sizes` 和四项 `failure_sizes`。前者是各payload字段独立JSON编码的UTF8字节数，排序取最大项；后者按原始tests列表的 `test_index` 对应错误消息、longrepr及断言比较值的UTF8大小。标点及对象包裹有额外开销，分项不能直接相加当成总值；原始证据仍完整，32KiB上限不变。
- Verifier把统计写入既有 `REVIEW_CONTEXT_TOO_LARGE` measurements，仍不调用语义模型评判装不下的材料。其他门禁与分类不变。
- Controller只从已存储、绑定匹配的original执行中生成 `execution_observation`，运行必须完整退出、日志/探针完整、源码保护通过；与verdict的run/candidate/manifest/contract/version逐项核对。没有完整、可绑定事实时不补造异常。
- `execution_observation.origin=program_observation`，保留独立Verifier意见的 `provider_claim`，两者分别展示。最多四条失败call，包含原始test_index、nodeid、异常类型、最多512字符消息摘录、实际异常路径与行号。超限统计指向的测试优先保留；完整内容留在原始执行/探针记录。
- 先脱敏（含Python两种引号、JSON转义），规范化运行根路径，再截取。没有通过TypeError错误文本自动认定候选错，也没有把参数名写成Sphinx特例。固定提示指导必要时Read原版签名/示例，模型自己修正候选。
- 小容量先减少异常条目并增加 `omitted_failed_calls`；一条也放不下时整体移除可选观察并增加省略计数，保留主判决代码、未过检查与绑定。JSON不按字节截断，原始存储不变。

## 回归与审查

- 首次测试有收集文件重名，已更名，未计为产品RED。有效RED **4 failed / 3 passed**：缺超限分项、缺单独执行观察。
- 补充多失败优先级/小容量逐条保留 RED **2 failed**，随后GREEN。
- 相关Verifier投影、setup脱敏与反馈容量 **56 passed，exit0，26.48秒**。
- 最终新增单元+真实SDK/本地pytest集成 **11 passed，exit0，8.83秒**。HTTP模型回答脚本化；完整下一轮请求确实有实际TypeError/path/line，超限路径也有分项；语义判决不被覆盖，超限不额外调用Verifier模型。
- fresh-context只读审查未发现可操作回归。审查者独立纯测试 **21 passed / 2 fixture-dependent deselected**；其临时目录ACL阻止了执行型测试，未将其计为独立通过。

## 真实记录零调用回放

`E:/ReproAgent/repro-results/actionable-feedback-replay-8474-20261016/replay.json` 保存输入hash、当前代码来源和输出；三个 `run-*-feedback.json` 可查看实际新反馈。

- 原超限仍为 **67,552 / 32,768 B**。分项 failure_evidence=48,608 B、expectation_sources=7,200 B、contract=4,094 B、response_schema=2,317 B；失败 test_index=1，longrepr=21,415 B、message=17,771 B、comparison=93 B，异常指向候选19行。新反馈2,859 B。
- 原API错误反馈明确出现 `SphinxTestApp.__init__() got an unexpected keyword argument 'builddir'`，实际异常位置为 `sphinx/testing/fixtures.py:195`（这是异常位置，不冒充候选调用行）；新反馈6,534 B，原 CANDIDATE_ORACLES_UNCONFIRMED 保留。
- 原最后候选仍失败在 `assert numbered`，候选18行、空列表；其程序事实与模型对警告预期的反对意见分开。新反馈4,903 B，原 CANDIDATE_ASSERTIONS_UNCONFIRMED 保留。
- **模型调用0、命令/测试执行0、原始输入hash不变**。回放只验证诊断投影；不构成新一轮模型能力评测，也不宣称8474已通过。

## 完整离线验收（通过）

最终代码提交 `838698c`，收集1436个用例，按文件分为三个互斥组，独立仓库外basetemp。最终 **1426 passed / 10 skipped / 0 failures / 0 errors，exit0**。三组分别486/1、450/1、490/8（passed/skipped），各exit0，总1436与收集数一致；墙钟525.016秒。日志/命令/分组在 `E:/ReproAgent/.local/holdout-five-20261015/actionable-final-validation/`。

## 限制

本次让反馈可操作，不自动替模型修测试。没有新一轮付费模型运行，因此尚未验证真实模型是否采纳并最终复现8474。没有放宽未获来源支持的预期/内部节点假设，亦未改变原实跑的EXHAUSTED/NONE结论。首次执行和纠错的6/2步预留策略保持上一版。

最终来源 hash `e190a204408e5f2e0554c38968e7ed42d0aa6c9270fad1f33f0668c2b76ce1e3`；验证后生产目录无未提交改动。完整退出码、分组命令、XML核对与来源回执：`E:/ReproAgent/repro-results/actionable-feedback-validation-20261016/verification.json`。后续提交仅补此验收文档。
