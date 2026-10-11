# 权限筛选修复与简化验收

日期：2026-10-11。基线b1bdcfc，分支codex/observability-diagnostics；本文为提交前验收快照，用户随后授权提交并push该分支，未授权合并。
用户确认的问题：选权限后对应工具与结果消失；随后要求筛选尽量简化。

## 最终行为

筛选栏只保留搜索、权限结果和清空。移除类型/失败控件及其隐藏状态；选项中文显示全部权限结果、允许、拒绝、发布预留。搜索可与权限组合，清空同时重置两个条件，保留手动折叠状态。
permission与实际工具执行可能是兄弟节点；按tool_call_key与真实Agent/探索上下文关联唯一权限点，工具保留其原始ERROR/SUCCESS等执行结果，并显示权限依据。跨阶段重复ID、缺父节点或多个权限点不猜测状态。
筛选隐藏选中节点时关闭旧详情、归还正文。运行提示或业务诊断跳转清空筛选并展开真实祖先。三种权限保持独立，拒绝不制造工具执行，发布预留统计不翻倍。

范围：trace_view_model.py、trace_viewer.mjs、相关Python/Node测试、说明文档。未修改Agent权限、模型请求、预算或执行流程；无新增模型调用/token。

## 根因与TDD记录

第一次修复：Python4个RED（同调用三态与跨阶段正文）；Node2个RED（权限依据与拒绝上下文）；真实SDK浏览器ALLOWED执行0/应为3；另单独复现详情残留true/应为false。逐项修复后GREEN。第一次相关Python80、Node11通过属于此前阶段，不作为最终简化版计数。
简化：Node新增验证旧typeSet/errorsOnly不能继续隐藏调用，先RED；浏览器验证复选框应为0，先观察到9/应为0。删除控件和状态后GREEN。
提示跳转：筛选DENIED后点实际失败提示，先复现筛选仍为DENIED/应为空，目标详情被隐藏；导航明确清空并展开后GREEN。

## 最终验证

- Python：68 passed，27.76秒，退出码0；`.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_trace_view_model.py tests/unit/test_trace_rendering.py tests/unit/test_trace_business_steps.py tests/unit/test_trace_diagnostics_view.py`。
- Node：12 passed，退出码0；`& E:\node\nodejs\node.exe --test tests/frontend/trace_viewer.test.mjs`。
- 简化与权限浏览器：7项通过，`.local/permission-filter-verification/browser-simple-check.cjs`；含真实旧报告、三态、搜索/清空、详情关闭、提示跳转、800px布局，pageerror/external均为空。
- 七步浏览器9项、原查看器浏览器11项复验通过；无JS/初始化失败回退、恶意正文、长正文、1024节点与CSP保持通过。
- git diff --check通过。范围是本地投影与前端，未重复完整仓库测试；不引用此前770作为本次全量通过。

证据：.local/simple-filter-*.log、.local/permission-filter-*.log及.local/permission-filter-verification/的JSON/HTML、simple-browser-results.json、simple-filters.png。
隔离headless Chrome；无Astra、无真实模型请求或新Token费用。既有SDK案例由MockTransport截获，真实旧报告只重建HTML。

## 用户旧报告

已重建E:/ReproAgent/repro-results/traced-5227-viewer/cases/pytest-dev__pytest-5227/task/observability/trace.html，trace.json与执行结果未改、任务未重跑。
实际记录：18次允许、1次发布预留、0次普通拒绝。ALLOWED展示18个工具执行；RESERVED只显示1个权限点；DENIED为空；清空恢复正确。

旧HTML内嵌脚本，需要打开已重建文件或刷新，不能仅更新源码。权限允许不等于业务接受，父节点/权限依据作为上下文可保留在筛选结果中。
代码保留在隔离工作区，E盘main源码未变；提交与push由用户后续明确授权，最终远端状态以Git记录为准。
