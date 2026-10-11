# ReproAgent 本地观测诊断修复验收

日期：2026-10-11。基线：15338f5。分支：codex/observability-diagnostics。
工作区：C:/Users/Dell/.codex/worktrees/observability-diagnostics/ReproAgent。
状态：本地实现与验收完成。最终全量回归760通过、5环境跳过、0失败；Node9通过；浏览器11项通过。本文记录提交前的验收快照；用户随后授权提交并push当前分支，未授权合并。

## 最终行为与边界

保持原AgentScope/OpenTelemetry底座，不新增服务、依赖或模型请求。运行命令不变，正常run默认采集。
缺失原因按来源、span、限制明确记录；正常未返回推理属于信息提示，未知长度为null。
结构、统计覆盖、正文和token/费用各自判断；OTel冗余event掉点不污染本地已保留的统计。
契约建立与实际修订、权限三态、发布/引用拒绝、计步与最终停止组成可定位的业务诊断链。
显示实际v1→v2和调用/phase标识；从同调用权限点借用的预算明确标为“许可时”。
SDK模型输入从公开hook直接复制，wire_request仍代表实际HTTP请求；256KiB单条/4MiB集合限额包括序列化开销。
保持thinking配置不变。验收脚本明确设置disabled，全部出站模型接口被MockTransport截获；没有付费真实模型调用。
本验收证明采集/诊断实现与原有复现流程的兼容性，不证明真实模型的总体Bug复现能力。

## 检查与证据

证据根：`.local/observability-diagnostics-repair-verification/`；任务日志：
`.superpowers/sdd/2026-10-10-reproagent-observability-diagnostics-repair/`。

| ID | expected | status | command | observed | evidence_ref | fix_location | recheck_command |
| --- | --- | --- | --- | --- | --- | --- | --- |
| D01 | 正常缺失不误报；损失原因/来源/限额明确，无悬空引用 | 通过 | C01 | capture-health与facade回归通过 | final-full-suite-complete.log | ContentStore / TaskTraceSink | C01 |
| D02 | 正文与计数/token/费用独立；本地已保留点不受冗余OTel掉点影响 | 通过 | C02 | 33点+完整10/5用量回归通过 | final-full-suite-complete.log | projection / summary | C02 |
| D03 | 仅保存新版后记录实际变化；显示旧/新版与真实差异 | 通过 | C03 | Controller/SDK修订例通过，v1→v2显示回归通过 | final-full-suite-complete.log | Controller / diff / renderer | C03 |
| D04 | 权限允许不等于业务接受；hash/显示范围拒绝准确 | 通过 | C03 | 发布拒绝与两次引用拒绝例通过 | final-full-suite-complete.log | middleware / domain检查 | C03 |
| D05 | 一探索调用计一步，重试不多扣；最终原因/已知取消维度明确 | 通过 | C04 | 3次HTTP/1步、cap后最终摘要、cancel回归通过 | final-full-suite-complete.log | Budget / runtime / CLI | C04 |
| D06 | 诊断跳转真实节点；未知历史不补写、不构造候选/验收 | 通过 | C05/C06 | 浏览器/legacy/静态链接通过 | final-full-suite-complete.log / browser-results.json / final-node.log | projection / renderer / JS | C05/C06 |
| D07 | 长输入限额内完整；每条与集合含JSON开销且超限明确 | 通过 | C07 | >65536字符SDK、80KiB wire、21轮/42正文、精确集合边界通过 | final-full-suite-complete.log | public hook / ContentStore | C07 |
| D08 | 观测不改请求/执行结论；预算快照/封存故障不改CLI输出/退出码 | 通过 | C08 | SDK等价检查与两项CLI故障注入通过 | final-full-suite-complete.log | middleware / CLI边界 | C08 |
| D09 | 离线安全、旧schema、无JS、长路径、安装包、6/8MiB | 通过；5项环境skip单列 | C00/C05/C06/C09 | 全量760通过；前端/浏览器通过；5环境跳过项见下文 | final-full-suite-complete.log / browser-results.json / final-node.log | rendering / paths / package | C00/C05/C06/C09 |
| D10 | 新回执、失败及修复证据，未测与跳过项独立 | 通过 | C00/C10 | 此回执/ledger与最终完整日志，首轮失败和5个跳过项独立保留 | final-full-suite-complete.log / progress.md / skip-reasons.log | 验收记录 | C00/C10 |

阶段测试：Task1 46通过；Task2 48；Task3 45；查看器Python54/Node9；长正文59；端到端7。
审查新增7个回归先全部RED，修复后相关57通过。以上集合有重叠，不能相加当总测试数。
Node最终9通过；pip check无依赖冲突；git diff --check通过。
浏览器：隔离headless Chrome 155.0.8059.39，最终11项通过，pageerror=[]、external=[]。
检查涵盖耗尽/成功、诊断点击与键盘、800px窄屏、长正文/限额/legacy、1024节点、恶意正文、禁JS回退。
脚本/截图/结果：generate.py、browser-check.cjs、diagnostics.png、browser-results.json。


最终全量命令C00：`.\.venv\Scripts\python.exe -m pytest -v`，退出码0，760 passed / 5 skipped，984.59秒；完整输出为final-full-suite-complete.log。
命令均在上述工作区运行；先移除全局PYTHONPATH，并设置PYTHONIOENCODING=utf-8、PYTHONUTF8=1。
下面C01–C08为按验收项复查的子集；C00已覆盖全部Python用例。不是把重叠集合相加。

| 命令 | 可执行复查 |
| --- | --- |
| C01 | `.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_capture_health_repair.py tests/unit/test_observability_content.py tests/unit/test_observability.py` |
| C02 | `.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_otel_projection.py tests/unit/test_observability_review_repairs.py` |
| C03 | `.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_observability_decision_events.py tests/integration/test_observability_diagnostic_chain.py` |
| C04 | `.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_budget_diagnostics.py tests/unit/test_budget.py tests/integration/test_agentscope_observability.py` |
| C05 | `& E:\node\nodejs\node.exe --test tests/frontend/trace_viewer.test.mjs` |
| C06 | `& E:\node\nodejs\node.exe .local/observability-diagnostics-repair-verification/browser-check.cjs`（使用已保存的最终HTML） |
| C07 | `.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_observability_content.py tests/integration/test_agentscope_observability.py` |
| C08 | `.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_observability_review_repairs.py tests/integration/test_observability_diagnostic_chain.py` |
| C09 | `.\.venv\Scripts\python.exe -m pip check`；`git diff --check` |
| C10 | 对照本回执、progress.md、final-full-suite-complete.log与skip-reasons.log逐项核验 |

这次完整回归确实执行了Windows长路径、安装包、启动器等测试；以下5项没有执行其目标分支，不能算作通过。
用精确node id重跑这5项并加`-rs`，得到同样5 skip；实际原因保存在skip-reasons.log：

| 跳过项 | 实际原因 |
| --- | --- |
| test_a_link_that_escapes_a_deep_workspace_is_still_refused | Windows创建符号链接需要权限；路径越界的普通分支另有检查 |
| test_a_venv_interpreter_runs_its_own_pytest_through_its_link | 当前解释器没有通过符号链接启动 |
| test_library_symlink_cannot_escape_into_fixed_repo | 当前环境无法创建链接 |
| test_the_standalone_replay_still_refuses_a_link_out_of_the_package | Windows创建符号链接需要权限 |
| test_rejects_external_symlink_and_install_escape | Windows符号链接权限不可用；普通路径越界另有检查 |

## 端到端结果

真实SDK、HTTP边界脚本、真实pytest与原Controller/Workspace/Verifier，不用真实模型。

- exhausted：4次HTTP请求（1次契约+3次探索），3步；未完成契约导致发布拒绝，随后2次未读引用拒绝，steps耗尽。无candidate/run/verifier。trace的partial=False、content/metrics/usage.complete=True。
- success：7次HTTP请求，3步；Read→请求修订→实际保存v2→发布→真实pytest/Verifier确认。DONE。与关闭trace的业务结论、证据等级、步数、请求次数一致。

产物：exhausted.json/html、success.json/html；实际执行目录保留在final-cases/下。
旧pytest #5227产物没有覆盖，也不声称补回其中曾丢失的输入或推理。

## 一次独立审查与修复

执行技能要求的一次全分支只读审查使用GPT-6.1，未使用Astra。审查不修改任何文件或git状态；无第二次审查。
审查4项Important及2项Minor；后两项因明确取消维度/可读版本变化属于验收要求，执行者重分级为Important并一并修复。

| 问题 | 修复 | 回归 |
| --- | --- | --- |
| 最终预算快照/封存异常逃逸CLI | 失败隔离、固定警告、保留任务结果/退出码 | test_final_snapshot_failure_preserves_cli_result / test_trace_finalization_failure_preserves_cli_result |
| 冗余OTel event上限误伤完整统计 | 独立counts_partial，记录结构损失而不抹掉已保留统计 | test_retained_points_do_not_make_duplicate_otel_event_drops_statistical |
| facade丢reason/limit/原始长度参数 | 同步透传metadata | test_module_capture_forwards_all_missing_metadata |
| 总正文限额漏算集合括号/分隔符 | 集合开销纳入准入计数 | test_aggregate_bound_includes_array_overhead |
| 明确内部取消仍为未知维度 | 实际取消分支增加dimension，外部无信息仍未知 | test_known_cancel_event_has_explicit_dimension |
| 诊断关系、版本与泛化ERROR难读 | 中文标签、调用/阶段短ID、v1→v2、许可时预算来源 | test_diagnostic_rows_show_revision_transition_and_call_group |

无Critical，未延期Minor，Declined-to-judge为空。每项覆盖RED→GREEN；最终全量结果单独记录。

## 首轮失败与实施调整

首轮全量：744通过、9失败、5跳过，618.92秒。日志full-suite.log保留，不能当最终绿色结果。
6个启动器失败因工作区缺少本地.venv；3个安装包失败因我全局设置PYTHONPATH，使安装/目标解释器错误发现源仓库。
修正为本地.venv、本地editable安装、复用已有依赖的本地pth，移除全局PYTHONPATH；Python/SDK版本不变。
安装包3+启动器4先通过；临时强制前置源码的pth导致2个fake_cli检查未使用其有意设置的PYTHONPATH。
删除该前置pth，保持正常editable路径顺序；该2项重新通过。没有为环境失败修改启动器或安装包的产品代码。

完整实施调整（包括取舍与风险）：

1. 原复用E盘venv+PYTHONPATH方案改为工作区venv；错误路径会使检查无效，已核对实际导入工作区模块与安装包隔离。
2. Windows使用Python维护同格式ledger/brief信息；未用bash-only助手；遗漏进度可能重做，因此保留计划身份/基线/命令和证据。
3. 按计划保留未提交改动，不自动commit/push/merge；审查看working-tree diff和新文件，不能用空BASE..HEAD忽略新实现。
4. 增加decision_metadata/可选context关键词，保持关闭采集时不读预算时钟；故障隔离回归约束不影响业务。
5. Tasks4/5/6浏览器验收合并到最终产物复验，避免重复开浏览器；最终11项有新证据，不引用旧截图充当通过。
6. 只读审查与稳定版本回归并行，完成仍以两者实际结果为门槛；首轮红色和中断轮次不隐藏。
7. 取消维度修复扩展到现有明确取消分支，仅增加异常元数据；不改条件/消息/状态，模型无取消信号的interrupted保持未知；全量回归检查兼容。
8. 取消与诊断展示重分级为Important，避免已知原因仍未知、v1→v2不可读；一次修复与回归覆盖，没有重派审查。

## 尚未验证/已知限制

- 付费真实模型接口、生产Bug能力评测未执行；无新的API密钥需求或远程数据上传。
- 4MiB容量上限内保留更长正文，不承诺无限会话完整；超过上限有定位原因。
- 旧文件的历史缺失不可恢复；接口不返回公开reasoning时也不生成思考文本。
- 验收时分支/工作区保留且未提交；用户随后授权提交并push当前分支。E盘main产品源码未修改；最终提交及远端状态以Git记录为准。
- 5个环境skip的具体分支未验证，原因如上；没有提升Windows权限或改变系统配置来绕过它们。
