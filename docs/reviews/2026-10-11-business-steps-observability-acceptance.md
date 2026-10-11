# ReproAgent 七步业务观测验收

日期：2026-10-11。基线：972930b。工作区：C:/Users/Dell/.codex/worktrees/observability-diagnostics/ReproAgent。
本次是用户确认的有界展示改进，沿用codex/observability-diagnostics隔离分支；没有自动commit、push、merge。
状态：本地实现与验收完成。最终完整pytest退出码0：770通过、5环境skip、0失败，631.25秒。前端9项、新业务浏览器9项与原查看器浏览器11项通过。本文记录提交前的验收快照；用户随后授权提交并push当前分支，未授权合并。

## 范围与实现

默认业务步骤页签，保留调用树与验收页签。七步顶部卡片显示最近一次已记录的状态、耗时、次数，可点击定位实际节点；执行列表保留重复尝试的真实顺序。
准备、分析、探索、验证、导出复用原span；增加run_candidate、replay、revise_contract实际业务边界。初始契约验证/保存与观察仍在原执行顺序，归入analyze跨度。
契约版本取自实际保存事件；请求修改不等于修改成功。重跑中的第二次Verifier、修复版准备和执行都位于第六步内。
任务purpose=business_steps_v1与结构完整、终态共同确定缺少步骤能否显示“未执行”。旧记录、未结束任务和缺少父节点时只显示“未记录”。不按历史runner.execute数量补写重跑。
“已执行”不等同复现通过，Verifier状态来自verdict，重跑确认来自程序submit_candidate结果。每项耗时只取对应span；卡片表示最近一次，不将子span或多次尝试相加。

修改：Controller/Verifier仅观察边界与受控身份，trace_business_steps.py、trace_view_model.py、trace_rendering.py、trace_viewer.mjs、trace.html.template，以及相关测试和docs/observability.md。
未改：模型请求/提示词、thinking配置、预算值/计步、工具权限、源码保护、验收决策、经验读取/写入规则、schema或依赖版本。

## 检查表

证据：.local/business-steps-verification/；日志：.local/business-steps-*.log。

| ID | expected | status | command | observed | evidence_ref | fix_location | recheck_command |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B01 | 真实顺序、重复轮次、仅最近一次状态/耗时 | 通过 | C1/C2 | 真实成功例1→2→3→2→3→4→5→6→7 | business-steps-review-green.log / success.json | projection/renderer | C1/C2 |
| B02 | 缺少步骤区分未执行/未记录，旧执行不猜重跑 | 通过 | C1/C3 | 新耗尽例Step4–6未执行；legacy Step6未记录 | exhausted.json / business-browser-results.json | projection | C1/C3 |
| B03 | 程序验收与系统执行状态分开；修订按实际版本 | 通过 | C1/C2/C3 | NOT_REPRODUCED与REPRODUCED分开；实际v1→v2；重跑已确认 | success.html / business-browser-results.json | projection/Controller/Verifier | C1/C2/C3 |
| B04 | 卡片/列表点击原节点，筛选恢复、键盘和窄屏 | 通过 | C3 | 七张卡片，真实节点/祖先、Enter/Escape、800px无横向溢出 | business-browser-results.json / business-steps.png | JS/CSS | C3 |
| B05 | 正文只存一次、恶意内容无执行、CSP、零外部请求 | 通过 | C1/C3/C4 | 正文出现一次；浏览器无pageerror或external请求 | business-browser-results.json / browser-results.json | renderer/JS | C1/C3/C4 |
| B06 | 无JS和初始化异常仍保留静态七步与原节点链接 | 通过 | C3 | fallback恢复，无重复section | business-browser-results.json | JS/static renderer | C3 |
| B07 | trace/no-trace业务结论、证据、步数、请求数一致 | 通过 | C2 | SDK success与no-trace同DONE/证据等级/3步/7请求 | business-steps-green.log / success.json | observation-only scope | C2 |
| B08 | 原诊断、正文保留、1024节点、长路径/安装/执行兼容 | 通过；5项环境skip单列 | C4/C5 | 原浏览器11项通过；完整pytest770通过/5skip/0失败 | browser-results.json / business-steps-full-suite-final.log | per failed node | C4/C5 |

命令均在上述工作区执行，移除全局PYTHONPATH，PYTHONIOENCODING=utf-8、PYTHONUTF8=1。
C1：`.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_trace_business_steps.py`。
C2：`.\.venv\Scripts\python.exe -m pytest -q tests/integration/test_observability_diagnostic_chain.py`。
C3：`& E:\node\nodejs\node.exe .local/business-steps-verification/browser-business-check.cjs`。
C4：`& E:\node\nodejs\node.exe .local/business-steps-verification/browser-regression-check.cjs`。
C5：`.\.venv\Scripts\python.exe -m pytest -v`。
前端：`& E:\node\nodejs\node.exe --test tests/frontend/trace_viewer.test.mjs`，9通过；pip check无依赖冲突。

## TDD与阶段证据

新增初版5单元+1集成用例RED，修复后与原耗尽例合计7通过。缺父节点fixture最初未标partial，先修正fixture并确认5个RED均为business_steps字段缺失。
契约版本单独RED→GREEN（6单元通过）；卡片可点击与最近状态单独RED→GREEN（6单元通过）。
首次浏览器RED缺少业务页签；修复后9项GREEN。随后卡片/版本修订重跑9项GREEN，原浏览器11项GREEN。
中间一次QA脚本重建缺少__file__，导致旧legacy文件未刷新；修正脚本环境、重新生成所有fixture，最终浏览器两组均通过。此脚本错误没有修改产品语义。
相关107项通过发生在最后卡片/版本修订之前，不用其冒充最终全量结果。
最终SDK fixture为success/exhausted的final-cases-v2目录；success7次HTTP、3步，exhausted4次HTTP、3步。全部MockTransport，thinking disabled，真实pytest，无付费模型。

## 取舍与限制

1. 复用已附加的隔离分支，不新建重复worktree；E盘main未改，部署使用仍需集成此分支。
2. 新增少量业务span，而非按历史执行次数猜重跑；现有1024节点上限不变，更多观察可能更早达到上限，届时缺失显示未知。
3. 七步卡片显示最近一次，下面保留每次尝试；避免汇总掩盖之前失败，不相加嵌套时长。
4. 旧trace只投影能确定的准备/执行/验收信息，不恢复未采集的正文、内部思考或重跑事实。
5. 每个后代只归入最近实际业务边界，保证重跑和修复版准备不冒充主任务首次准备/验收。
6. 不自动push本次新变更；先完成审查和实际全量验收，用户明确请求后发布。

## 独立审查与修复

requesting-code-review要求的一次只读审查由GPT-6.1完成，无Astra、无第二次审查。无Critical；1项Important：合法trace结构中的verdict.result_code若为list/dict，会在状态字典查询时报TypeError，导致整页无法生成。
收到报告后验证根因并补回归：list/dict verdict两例，以及同类风险的list/dict task status两例。4例先RED，类型守卫修复后10项GREEN。异常类型只使业务结果为未确认/未记录，不修改或删除原始属性。
相同类型守卫覆盖读取业务结果码的各个分支；程序明示拒绝仍显示拒绝，无法确定的完成结果不冒充通过。
Declined to judge仅付费模型能力：本次是展示与采集兼容性验收，不据Mock脚本声称模型能力；保留此范围，风险是不能外推生产Bug成功率。无延期Minor。
首次完整回归为修复停止，最后日志到约34%，不是绿色结果；日志business-steps-full-suite.log保留。新轮最终日志为business-steps-full-suite-final.log，源码与测试在该轮期间保持不变。
## 最终完整回归与跳过项

最终C5运行完整775个已收集用例，770 passed / 5 skipped，631.25秒（10分31秒），退出码0。命令输出完整保存在business-steps-full-suite-final.log。产品源码与测试自该轮开始后没有修改。
前端最终9项通过，business-steps-node-final.log；新业务浏览器最终9项、原查看器最终11项通过，page errors和external requests均为空。pip check无依赖冲突，git diff --check通过。
按精确node id加-rs单独复查5个skip，实际原因见business-steps-skip-reasons.log：

| 跳过项 | 实际原因 |
| --- | --- |
| test_a_link_that_escapes_a_deep_workspace_is_still_refused | Windows符号链接权限不可用，普通路径越界另有检查 |
| test_a_venv_interpreter_runs_its_own_pytest_through_its_link | 当前解释器没有通过符号链接启动 |
| test_library_symlink_cannot_escape_into_fixed_repo | 当前环境无法创建符号链接 |
| test_the_standalone_replay_still_refuses_a_link_out_of_the_package | Windows创建符号链接需要权限 |
| test_rejects_external_symlink_and_install_escape | Windows符号链接权限不可用，普通路径越界另有检查 |

上述5个具体分支未验证，不能算作通过；未改Windows权限配置绕过它们。未做付费模型能力评测。
E盘样例与日志快照在E:/ReproAgent/.local/business-steps-verification/，产品源码仍在上述隔离工作区；main源码未变。
