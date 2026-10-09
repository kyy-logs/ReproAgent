# AgentScope 原生观测迁移验收回执

计划日期：2026-10-09；验收日期：2026-10-10。当前状态：本地已执行范围验收通过；5项Windows链接条件跳过，具体缺口另列。

基线 `c818deb`；产品代码版本 `e852740`；分支 `codex/agentscope-observability`。
环境：Windows，Python 3.12，AgentScope 2.0.9，OpenTelemetry API/SDK 1.45.0，pytest 9.1.1。

## 改动与结果

真实AgentScope原生hook生成Agent/model/tool span；OTel负责唯一trace/span上下文与ID。
ReproTraceMiddleware补reasoning周期和既有业务字段；模型工厂保留四purpose、logical/HTTP尝试与原记账。
本地投影负责白名单、脱敏、完整UTF-8序列化限额和schema2，查看器保留schema1读取/重建。
普通run默认输出trace.json/trace.html，仅--no-trace可关闭；没有远程exporter或后台服务。

未改模型请求协议、prompt、输出限额、实际业务工具、权限/最后三步预留、Controller/Verifier/Runner业务推进、验收规则、EvidenceLedger、manifest与学习来源。
具体变更文件由 `git diff --name-only c818deb e852740` 核对；旧观测验收文件没有改写成新结果。

## 验证记录

证据均位于 `.local/agentscope-observability-migration-verification/`，原始失败保留，未提交原始本地日志。

| 检查 | 结果 | 证据/失败定位 |
| --- | --- | --- |
| M01 原生底座/统一ID | pass | 原生真实SDK集成及backend测试；runtime.py、otel_backend.py |
| M02 上下文/关闭隔离 | pass | foreign provider、并发、迟到span、显式foreign context、嵌套关闭；final-review-red.log→final-fixes-focused.log |
| M03 内容安全/有界 | pass | SDK嵌套ID/未知名称、事件秘密、metadata信封、media blob/uri；final-review-red.log、native-media-red.log→对应GREEN；otel_projection.py |
| M04 reasoning与决定 | pass | 真实SDK调用树、既有程序check与provider_claim、wire视图和工具关联；task-2-3-final.log、task-5-focused.log |
| M05 异常/关闭/不重跑 | pass | handler之前/之后的native故障、序列化、异常字符串、公开hook关闭、ctor失败；final-fixes-focused.log；native middleware/facade |
| M06 一次记账/权限 | pass | HTTP叶子、重试/unknown、ALLOWED/DENIED/RESERVED、真实六/经验七工具；task-2-3-final.log |
| M07 验收/学习隔离 | pass | 真实SDK+pytest与关闭观测等价、短路、独立学习预算；task-5-focused.log及生命周期测试 |
| M08 输出/旧格式/安装 | pass with platform gaps | schema1/2、只增加双文件、长路径/失败降级；58pass2skip，独立wheel/target replay在final-fixes-focused.log通过；Windows链接缺口见下 |
| M09 当前完整回归 | pass | full-suite-accepted.log：704 passed、5 skipped、0 failed、exit 0；671.16秒；pip-check-final.log无破损依赖 |
| M10 过程/审查依据 | pass | 本回执、progress.md、独立review-report.md、先失败/修复与最终全套结果均保留 |

专项记录：底座8pass；门面+业务59pass；页面/CLI/Windows58pass2skip；最终修复+安装包24pass；实际media类型7pass；pip check无破损依赖。
完整基线668pass5skip。首次迁移全套686pass8fail5skip：六个launcher失败是工作区缺少.venv入口（补齐后6pass），两个wheel测试是环境PYTHONPATH污染（隔离环境后通过）。这些初次结果不是最终验收通过证据。

最终全套：`704 passed, 5 skipped in 671.16s`，进程退出0。专项24pass之后的media排除追加验证7pass；最终全套已包含全部修复。

最终全套命令：
```powershell
# 使用工作区自己的解释器；不设置全局PYTHONPATH
$env:REPROAGENT_RG_PATH=(Get-Command rg).Source
.venv/Scripts/python.exe -m pytest tests/unit tests/integration -q -rs
.venv/Scripts/python.exe -m pip check
```

## 独立审查与修复

一位全新上下文gpt-6-astra/high审查 `c818deb..0fb02ec`，只读，不修改代码。
三项Important：公开stream关闭未同步关闭委托链；native消息内部工具ID/未知名称泄漏；事件名称未脱敏。
三项均有新增复现测试RED→GREEN；父代理发现的foreign context、嵌套关闭、整体metadata限额、初始化降级、无观测依赖与media边界同样由实测固定。
没有审查遗留的Minor。审查本身没有认证测试全套通过，最终结论依实际运行输出。

## 实现取舍与代价

- Bash技能脚本在本Windows环境用等价PowerShell台账/日志步骤替代；若记账有误会影响后续恢复，因此保留原始命令和提交。
- Task 2/3关联接口一起验证，错误分类随门面切换落地；若耦合判断有误可能漏跨层回归，因此跑真实SDK和全套。
- SafeTracingMiddleware继承并调用原生公开super hook，用执行/结果跟踪隔离观测故障，不复制SDK或重跑handler；若故障归因错误可能影响结果，因此覆盖调用前后、流关闭和异常记录失败。
- schema2增加真实根与point，旧的无根/兄弟节点断言改为实际OTel结构；若迁移阅读错误会影响排障，因此双版本和实际父链有断言。
- 文件显式UTF-8，Unicode内容字节限额与SDK字符限额分别测试；编码处理错误会损坏页面/记录，因此有中文及真实序列化限额验证。
- 最终使用独立工作区venv，通过本地.pth复用已安装依赖/Windows扩展，不设置环境PYTHONPATH、不改共享环境；隔离不当会假通过安装测试，因此验证独立wheel和没有产品/SDK的target replay。
- 初始化观测失败、foreign provider和显式关闭不接管宿主，复现继续；代价是该任务无trace，固定警告说明，不能无声伪造完整观测。
- 不扩展远程OTLP、跨服务、硬杀恢复、峰值内存优化；这些不验证/不声称已覆盖。

## 验证缺口

Windows无符号链接创建权限时，原有链接实测会skip；不把这些具体OS路径标成已经实测通过。读取限额、路径遍历/重定向守卫、长路径与模板/安装有其他实际回归覆盖。
未运行其他OS/远程CI，也未使用真实模型接口；真实模型性能/费用比较与native瞬时序列化峰值内存未评估。
原生serializer在本地projection之前运行；存储限额不等于该步骤的瞬时内存上限。

## 交付状态

产品代码在独立分支本地提交。未合并main、未push、未调用真实模型。完整回归结果已更新；后续合并/推送由用户选择。
