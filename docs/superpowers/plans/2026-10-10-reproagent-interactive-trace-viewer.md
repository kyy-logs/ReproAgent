# ReproAgent Interactive Trace Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in the current session. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有trace.html升级为类似参考截图的调用树/瀑布时间线，支持节点详情、搜索筛选和程序提示；生成与浏览均不新增模型调用或模型token。

**Architecture:** Python对已有trace.json建立只读展示模型，保留服务端静态回退；内联CSS与固定JavaScript模块提供浏览器交互。正文在HTML中只保存一次，展示模型只存引用及元数据；浏览器按引用读取已脱敏正文，不访问网络或原始任务文件。

**Tech Stack:** Python≥3.11标准库、HTML/CSS、原生JavaScript ES modules、pytest；前端纯逻辑使用Node内置test runner，不引入产品npm依赖或构建服务。

**Spec:** 本文件“设计合同”落实用户认可的短设计与截图参考；现有采集与权限边界沿用 `docs/observability.md` 和 `docs/superpowers/specs/2026-10-09-reproagent-agentscope-observability-migration-design.md`。

**Baseline:** 2026-10-10，本地main `308ba6a`。当前renderer约400行、模板约75行；现有 `test_viewer_is_offline` 禁止任何script，需有针对性更新为“仅允许打包的固定脚本，不执行trace内容”。所有任务当前未执行。

## 设计合同

### 页面与交互

- 顶部紧凑概览：任务状态、Trace ID、开始时间、主任务/含学习总耗时、Agent reply次数、逻辑模型调用数、HTTP尝试数、工具执行数、已知token与费用。Agent次数只数实际sdk.agent_reply，schema1未记录则显示未知。时间显示本地时区并注明时区；模型计数沿用summary.logical_calls，不把SDK、logical、HTTP嵌套层相加。
- 主区域左侧调用树约38%、右侧共用时间轴约62%。ENTRY绿色、AGENT青色、LLM橙色、TOOL粉色、VERIFY紫色，HTTP/其他步骤使用较浅色；kind映射必须明确，颜色不替代文字。
- 默认展开两层，父节点可折叠；保留实际SDK→logical→HTTP层级，权限点用tool_call_key关联，不虚构工具是某个模型节点的孩子。点事件画刻度，未知耗时写“未知”，不画假时间条。
- 点击节点打开桌面右侧360px详情面板；≤900px时改为主区域下方。详情显示实际名称/ID/来源、display status与原始OTel status、受控属性、输入/输出、工具参数/结果、已返回推理、程序验收依据。
- 页签只做“调用树”“验收”。调用树中已经包含时间线；验收展示现有program_check/provider_claim/verdict，不引入模型自评分。
- 搜索名称、属性与已保留正文；搜索输入延迟150ms。类型筛选与“仅看失败/异常”组合使用。匹配后保留祖先路径并临时展开，清空查询恢复手动折叠状态；无匹配显示空状态。
- 异常筛选依据display status=error/failed、result_code=ERROR/FAIL/FAILED或health=failed/blocked。SDK原始ERROR但程序展示为正常终止的节点不误列为失败；DENIED/RESERVED_FOR_PUBLISHING分开显示和筛选。
- 顶部“运行提示”为确定性程序规则：最慢的3个HTTP/工具/验收/进程节点、同一logical下实际保留的多次HTTP尝试、发布预留数量、已有失败位置。partial时注明“基于已保留数据”，不推断未取得的原因。

### 数据与时间线

- schema1/2继续可读；不改变trace.json格式、采集器或summary。schema1.kind=event与schema2.kind=point都识别为点，修正现有 `_render_contents` 仅匹配event导致schema2权限参数未自动关联工具的展示缺口。
- 使用offset_seconds/duration_seconds；只接受非bool、有限、非负数。未知/负值/NaN/Infinity保留未知标记，不当0。所有树遍历迭代实现，至少验证1024节点和1024深父链不递归溢出。
- 时间轴范围=max(有效total_duration、已知节点结束时间)。没有可用总时长时标“已记录时间范围”；没有有效时间时显示无时间数据。百分比夹到0..100；零耗时和点事件使用最小刻度并显示真实0，不赋予假时长。父子耗时不累加。
- 元数据/正文各保留一次：内联JSON不含contents.text；每个content_id只对应一个服务端正文容器，节点持有content_keys。相同tool_call_key的permission和tool节点共享详情引用，拒绝/预留即使没有执行节点也能独立查看参数。
- 模型名称仅在完整、未截断的wire_request可解析且model为字符串时提取，绑定拥有该记录的HTTP节点及其实际祖先模型节点；缺失则未知，不解析head/tail摘录猜模型。
- usage.complete=false时输入/输出标“已知小计”；仅complete=true且两项都已知时显示总token。费用也依据cost.complete标总额或已知小计；partial时计数注明已保留范围。
- TTFT、缓存命中率不在本次采集范围，显示“未采集”；不为界面改变stream/thinking配置、发追问、追加模型分析或新健康检查。

### 单文件与安全

- 普通run默认生成JSON/HTML、--no-trace关闭、离线trace子命令均不变。只写现有两个观测文件，不追加JS文件到任务输出目录。
- 固定JS源码保存为resources/trace_viewer.mjs，Python读取并内联到type=module脚本；无import、CDN、字体、fetch/XHR/WebSocket、远程分析或服务器依赖。CSS在模板内联。
- 展示元数据嵌入type=application/json数据岛，JSON序列化后转义<、>、&及U+2028/U+2029；字段含</script>、注释边界或引号仍不能结束数据岛。脚本内容不得来自记录。
- 固定脚本只使用DOM创建、textContent和数值样式；不用eval、Function、document.write、innerHTML解析采集数据，不把正文变成HTML/Markdown/可请求的媒体或链接。
- CSP：default-src 'none'；script-src仅允许固定模块源码的SHA-256 hash；style-src 'unsafe-inline'；connect-src/img-src/frame-src/object-src/base-uri/form-action均'none'。hash按实际内联源码UTF-8计算；CSP不能替代转义测试。
- 禁用JS或初始化失败时，保留静态概览/内容/验收可读。增强完成后才隐藏静态区域，不能失败后留下空白页面。搜索与展开键盘可操作，详情关闭后焦点回选中行。
- 保持HTML完整UTF-8≤8MiB、JSON读取≤6MiB；超限按原规则拒绝页面并保留主结果/trace.json，不默默截掉正文或增加额度。

## Global Constraints

- 不新增模型请求、模型token或实际工具/pytest/健康检查调用；所谓零token指运行时页面生成与使用，不承诺开发对话本身没有token。
- 不改Controller、Verifier、预算、权限/最后三步预留、学习时序、OTel provider/采样、模型工厂或收集的内容范围。
- 只读取已有脱敏trace document；不重新读取源码、stdout/stderr或模型原始请求。所有派生值有来源，不反馈给Agent或证据链。
- 保留schema1/2、单文件离线、8MiB HTML/6MiB JSON限额、Windows路径入口/原子写及写失败非致命行为。
- 允许的是固定打包模块，trace数据仍不可执行。更新与旧“禁止任何script”有关的观测文档和测试，不弱化XSS及零外部请求断言。
- 沿用当前会话逐项执行；执行前建立隔离工作区。本次仅写计划，不改产品、不调用真实模型、不push。

## Review Focus

1. </script>/事件属性/恶意URL/Unicode数据岛注入、CSP hash不匹配：页面必须仍安全且交互可用。Task 2/3。
2. schema1 event与schema2 point、共享正文及拒绝无tool节点：参数/结果正确关联，正文只存一次。Task 1/2。
3. partial、未知/非有限时间、父节点缺失与深树：时间条及计数不伪造，路径保留且页面不崩。Task 1/3。
4. 查询+折叠+类型+异常组合：祖先可见、恢复折叠、正常控制性SDK ERROR不误判。Task 3。
5. file://、JS禁用/失败、移动宽度、wheel遗漏模块：正常与回退视图都可读，无网络及额外调用。Task 2/4。

## 文件与公共接口

Create:
- `src/reproagent/trace_view_model.py`：纯只读展示模型、时间几何、关联、规则提示。
- `src/reproagent/resources/trace_viewer.mjs`：可测试的纯筛选函数与浏览器交互入口。
- `tests/unit/test_trace_view_model.py`, `tests/frontend/trace_viewer.test.mjs`。
- `tests/fixtures/traces/viewer-scenarios.json`：脱敏合成schema1/2、partial/权限/共享正文/恶意文本/1024节点案例；大案例可由测试生成，禁止提交真实凭据或用户任务。
- `docs/reviews/2026-10-10-interactive-trace-viewer-acceptance.md`（实施验收时创建）。

Modify:
- `src/reproagent/trace_rendering.py`：组装页面、数据岛、正文注册表、静态回退/CSP hash；现有render_trace/write_trace_html签名不变。
- `src/reproagent/resources/trace.html.template`：紧凑概览、树/时间线、详情、响应式样式及固定脚本槽。
- `pyproject.toml`：package-data加入resources/*.mjs，产品依赖不增加。
- `tests/unit/test_trace_rendering.py`, `tests/integration/test_installed_package.py`, `tests/integration/test_observability_end_to_end.py`：安全/输出/安装与原调用数量回归。
- `docs/observability.md`与本计划状态；历史计划保留其当时约束，说明新查看器允许固定脚本。

接口：
```text
build_trace_view(document: dict) -> dict                         # 先沿用validate_trace
serialize_trace_view(view: dict) -> str                         # trace_rendering.py：无正文安全数据岛
render_content_registry(document: dict) -> str                  # trace_rendering.py：每ID一个正文容器
render_trace(document: dict) -> str                             # 原接口不变
computeVisibleRows(view, state, matchedContentKeys: Set) -> Array # JS纯函数
mountTraceViewer(root: HTMLElement, view: Object) -> void         # 浏览器入口，成功后增强
```

view_version=1，字段header/nodes/roots/contents/hints/time_axis。node包含key（输入顺序生成的安全整数）、span_id、actual_parent_span_id、parent_key、child_keys、depth、label、kind_tag、display_status、otel_status、attributes、content_keys、offset_seconds/duration_seconds、start_percent/width_percent、missing_parent。content descriptor包含安全整数key、content_id、source/availability/original_bytes/captured_bytes/truncated/redacted/excerpt_mode及正文容器ID；绝不包含text。

根/子节点按(未知offset排后、offset、输入序号)稳定排序，缺失父节点独立呈现并标记，不篡改actual_parent_span_id。state含query/typeSet/errorsOnly/collapsedKeys/selectedKey；matchedContentKeys由浏览器逐份正文匹配生成，不为每节点拼接复制长正文。

## Task 1: 只读展示模型与可信时间几何

**Files:** Create trace_view_model.py、test_trace_view_model.py、合成fixtures。
**Interfaces:** Produces build_trace_view、node/content_keys、time_axis、hints；消费现有validate_trace和有限输入document。

- [ ] Step 1: 写 `test_schema1_and_schema2_permissions_join_tools`、`test_denied_and_reserved_calls_keep_their_own_arguments`、`test_shared_content_has_one_descriptor_and_no_embedded_text`。断言双kind关联同key、无tool仍有详情、没有正文拷贝、不修改输入对象。
- [ ] Step 2: 写 `test_time_geometry_handles_unknown_zero_and_nonfinite_values`（offset=2/duration=3/extent=10得到20%/30%，未知为null）、`test_partial_metrics_are_labelled_known_subtotals`、`test_deep_tree_and_missing_parent_do_not_invent_edges`；1024层迭代无溢出，父子耗时不相加。最慢3节点/真实重试/预留提示只从保留数据派生。
- [ ] Step 3: Run `.venv/Scripts/python.exe -m pytest tests/unit/test_trace_view_model.py -q`，Expected: RED指向缺失展示接口/行为。
- [ ] Step 4: 实现build_trace_view与数据合同、node分类、model提取和程序规则。保留真正原parent，输出纯JSON可序列化数值；拒绝bad schema/循环沿用原reader规则。
- [ ] Step 5: 同命令GREEN；每项输入deepcopy前后相等，提交 `feat: add a readonly trace viewer model`。

## Task 2: 单文件页面、安全数据岛和静态回退

**Files:** Modify trace_rendering.py、template、pyproject.toml与test_trace_rendering.py；Create trace_viewer.mjs的纯入口骨架。
**Interfaces:** Consumes Task1；Produces serialize_trace_view/render_content_registry、可信脚本/CSP与各布局区域，render_trace仍按8MiB完整字节判断。

- [ ] Step 1: 写 `test_data_island_cannot_be_closed_by_trace_content`、`test_shared_body_is_stored_once_in_html`、`test_only_packaged_module_is_executable`、`test_static_fallback_survives_missing_javascript`。用</script><script>、引号/事件属性、URL、U+2028/2029，断言解析元数据等于原投影、只有一个正文容器、没有外部资源和动态代码执行入口。
- [ ] Step 2: Run `.venv/Scripts/python.exe -m pytest tests/unit/test_trace_rendering.py -q`，Expected: 新合同RED。将旧test_viewer_is_offline改成核对固定模块内容/hash、无src/CDN/connect资源，而非简单禁止所有<script>；原恶意正文转义断言保留。
- [ ] Step 3: 实现模板布局/颜色/字段、固定模块内联及hash、安全JSON转义、正文单份注册表和回退。未知/截断/脱敏/partial提示保留；不能双存静态正文和JSON正文来凑交互。
- [ ] Step 4: 同命令GREEN，覆盖8MiB超限、JSON成功HTML失败、静态内容/验收可读，提交 `feat: render an offline trace tree and waterfall shell`。

## Task 3: 浏览器搜索、折叠、选择和详情

**Files:** Modify trace_viewer.mjs；Create tests/frontend/trace_viewer.test.mjs；必要CSS调整归本task。
**Interfaces:** Consumes view数据/正文DOM；Produces computeVisibleRows/mountTraceViewer及类型/异常/页签/详情交互。

- [ ] Step 1: 用Node内置runner写 `matching_descendant_preserves_and_opens_ancestors`、`clearing_query_restores_manual_collapses`、`combined_filters_distinguish_expected_otel_error`、`shared_content_matches_without_per_node_body_duplication`。覆盖孤儿、空结果、零时间、同名多调用，用纯数据测试而不是CSS截图镜像断言。
- [ ] Step 2: Run `node --test tests/frontend/trace_viewer.test.mjs`，Expected: RED；实施时先定位现有/捆绑Node，使用实际绝对路径，不为产品添加npm依赖。
- [ ] Step 3: 实现筛选/祖先集/折叠状态、150ms搜索、DOM正文索引、纯文本详情及键盘焦点。module在无document的Node环境仅导出纯函数，在浏览器挂载成功后才切换增强视图；不创建模型“智能分析”按钮。
- [ ] Step 4: Node测试GREEN；使用browser-act技能在独立浏览器页打开file://合成案例，验证点击/折叠/查询/错误筛选/清空恢复/权限详情、1366px与800px布局；脚本错误时回退不空白、控制台无未处理错误。
- [ ] Step 5: 保存交互检查结果与截图到本地验收目录，提交 `feat: add local trace inspection interactions`。浏览器工具不可用则保留案例并将实际浏览器验收not_run，不以静态字符串测试冒充通过。

## Task 4: 安装、端到端与验收回执

**Files:** Modify installed_package/end_to_end测试、docs/observability.md、本计划状态；Create验收回执。
**Interfaces:** Consumes完整查看器；Produces安装与输出兼容证据、程序提示说明、browser验证记录。

- [ ] Step 1: 写 `test_wheel_includes_and_inlines_viewer_module`、`test_interactive_viewer_adds_no_requests_or_task_artifacts`。独立wheel核对mjs/template且可生成页面；真实SDK+pytest原用例中模型/工具次数、token、结果及交付包不变，task-output仍只增加两个观测文件。原schema1离线重建不覆写JSON。
- [ ] Step 2: Run `.venv/Scripts/python.exe -m pytest tests/integration/test_installed_package.py tests/integration/test_observability_end_to_end.py -q`，Expected: 新打包/页面行为RED；只做必要renderer/package-data修复后GREEN。
- [ ] Step 3: 浏览器实际检查恶意案例：window.__trace_xss_marker保持未定义，脚本/图片/iframe/外部地址不触发请求；加载后资源记录不含外部网络资源，搜索点击不发请求。关闭JS后仍有静态输入/输出/验收。截断/未知/partial、键盘操作和1024节点案例有实测。
- [ ] Step 4: 跑本改动直接相关的view_model/rendering、frontend Node、CLI/Windows路径、installed_package/end_to_end回归与pip check。仅当新故障/跨层改动/审查要求出现时扩大到完整suite；不重复旧704pass当新界面验收。独立审查按requesting-code-review技能做一次，重要发现先复现再修复。
- [ ] Step 5: 更新使用文档、固定脚本安全边界和回执；记录版本、命令、实际结果、浏览器/截图、失败及修复位置。提交 `docs: verify the interactive offline trace viewer`；不默认合并/push。

## 验收矩阵与自检

| ID | 必选项 | 当前 | 失败定位 |
| --- | --- | --- | --- |
| V01 | schema1/2、真实parent、permission/tool及共享正文关联 | not_run | view_model/renderer |
| V02 | 真实时间条、未知值/零值/partial计数诚实、程序提示有依据 | not_run | view_model/时间线 |
| V03 | 折叠/查询/组合筛选/祖先恢复/详情/验收页签实际可操作 | not_run | mjs/模板 |
| V04 | 已有推理/参数/结果可见；缺失不补写；正常控制结束不误报错误 | not_run | 分类/详情投影 |
| V05 | 固定脚本+CSP；恶意文本不可执行、外部资源零请求；单份正文 | not_run | 序列化/模板/DOM操作 |
| V06 | 单文件file://、JS回退、桌面/窄屏、键盘和1024节点实测 | not_run | 页面样式/入口/迭代逻辑 |
| V07 | 命令/输出/模型token及调用数不变；8MiB/长路径/wheel/旧文件重建 | not_run | renderer/package-data/集成测试 |
| V08 | 命令/结果/截图/失败修复/审查齐全，未测部分单列 | not_run | 验收回执 |

每项记录expected/status/command/observed/evidence_ref/fix_location/recheck_command。回执和原始日志/截图保留到 `.local/interactive-trace-viewer-verification/`；截图只是视觉证据，不替代交互/安全断言。所有必选项有证据才宣称查看器验收完成。

已自检：Python与JS接口均有产出任务，content字段沿用original_bytes/captured_bytes；只改展示/打包，接口一致，正文单份与8MiB限额一致，旧script禁令明确修订，五个Review Focus各有测试/浏览器步骤；不引用旧测试数量或截图里未采集指标作为完成证据。执行方式沿用当前会话逐项实现。本次仅交付计划，未改产品代码。
