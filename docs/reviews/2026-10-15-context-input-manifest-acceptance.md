# 每轮输入清单验收记录

## 范围

在现有离线 trace 中增加每次 HTTP 模型请求的材料清单，帮助区分“材料未进入请求”、
“已进入请求但观测正文被截断”和“材料已进入请求但模型没有按预期使用”。
最后一项只支持人工对照输入与输出，程序不推断模型是否理解或采纳。

本次不修改探索策略、权限、候选验收、经验检索规则、thinking 配置或实际请求内容，
不增加模型调用。主工作区的其他会话改动不在本分支中。

## 实现

- `context_manifest.py`：任务内有界来源登记、角色与文本摘要共同匹配、结构化 HTTP 输入清单。
- `runtime.py`：登记程序组装的阶段输入及因容量移除的阶段记录，保留当前契约版本。
- `middleware.py`：登记临时预算和浏览提醒，将来源作用域绑定到本轮模型调用。
- `model_factory.py`：在 HTTP 发送前、正文采集截断前生成清单；采集失败仍继续普通请求采集。
- `observability.py`：允许受控的 `context_manifest_state` 属性。
- `trace_view_model.py`：仅把实际 HTTP 分支的清单引用关联到模型祖先，不复制正文或改变 span 拓扑。
- `trace_rendering.py`、`trace_viewer.mjs`、模板：安全表格、旧 trace 提示和窄屏换行。
- `docs/observability.md`：诊断入口、字段含义、来源覆盖与容量边界。

清单保留 Issue、当前/历史契约、反馈、阶段记录、预算、工具定义、工具调用 ID、
工具报告的源码行范围，以及经验库提示/目录/正文的差异。未登记来源保持未知。
只匹配已登记文本和正确消息角色，不把模型或工具对任务 JSON 的回显当作当前程序状态。

每条清单最多 12000 UTF-8 字节，并受既有 512 正文条目、4 MiB 正文总量及文件限额约束。
限额按整项省略并记数量，不截断 JSON。字段字节数不是 token 数；源码旁注不是新增验收。
完整请求体的 SHA-256 只用于关联本地发送前请求，不证明提供方已收到请求。

## 验证证据

1. 基线：已有实际 HTTP 请求采集、预算提示关联测试，2 passed。
2. 首个新集成测试先以“0 个清单，预期 2 个”失败，接入后通过。
3. 查看器的分支关联、可读表格、截断提示先失败，接入后通过。
4. 最终相关回归：97 passed；覆盖旧 trace、源码引用、实际请求、经验目录与正文、重试去重、
   组装删减、脱敏、观测错误和安全渲染。
5. 浏览器：独立临时 Edge 上下文，桌面 1440px、窄屏 390px、节点切换、清单恢复；
   无横向页面溢出、无页面脚本错误、无 HTTP/HTTPS 请求。已有前端测试 8 passed。
6. 独立审查使用 gpt-6.1-sol；未调用 Astra 或真实模型接口。

审查发现并修复两项 Important：

- 仅按文本摘要匹配会把 assistant/tool 回显误标成当前契约；新增角色与回显回归，先复现后修复。
- 逐项丢弃时反复序列化整个清单会产生平方级工作量；改为每项编码一次并累减字节数。
  1000 条消息的确定性回归先测得 497725 行序列化工作量并失败，修复后通过。
  审查者对 5000 条小消息的离线测量从 24.891s 降到 0.109s，输出 11994 字节，
  明示省略 4924 项。该测量是观测构建性能，不是模型性能。

修复后的清单单元与集成回归 14 passed；审查者单独运行清单单元测试 7 passed，
两项路径无剩余发现。完整项目回归日志在
`E:/ReproAgent/.local/context-input-manifest-outside-full.log`。

隔离环境另经历两次路径校正：普通追加源码路径会落回主工作区；直接插到最前又会覆盖
启动器测试通过 `PYTHONPATH` 注入的假 CLI，导致两个启动器用例读取不存在的 task.json。
最终仅把工作树源码排在主工作区源码之前，保留显式 `PYTHONPATH` 优先级；已检查新进程
实际导入本工作树模块，启动器整组 6 passed。没有修改启动器或降低断言；此前中断的完整
回归不计入最终结果。

另外，仓库内 `--basetemp` 使控制用例的嵌套 pytest 继承外层项目配置，
实际节点变成 `.local/.../tests/test_reference.py::...`，无法匹配预期的仓库相对节点。
这导致 `test_swt_controls.py` 七项失败。改用仓库外临时目录后，控制组整组
10 passed，39.11s；控制实现和断言未改动。最终完整回归同样使用仓库外临时目录。

## 最终完整回归

工作区：`C:/Users/Dell/.codex/worktrees/context-input-manifest/ReproAgent`，
分支：`codex/context-input-manifest`，基线：`c7c6a09`。

```powershell
.venv/Scripts/python.exe -u -m pytest -q -p no:cacheprovider --basetemp C:/Users/Dell/Documents/Codex/tmp-context-input-manifest-acceptance
```

最终结果：**1157 passed，6 skipped，1127.54s，exit 0**。
日志：`E:/ReproAgent/.local/context-input-manifest-outside-full.log`。
`git diff --check` 通过。浏览器验收和 8 条前端测试另行通过。

离线示例：`E:/ReproAgent/.local/context-input-manifest-demo/task/observability/trace.html`。
它由真实 SDK 与 MockTransport 生成，用于展示输入清单，不是真实 Bug 成功率实验。

## 限制与结果边界

- 此次使用真实 AgentScope SDK、离线 HTTP 夹具和临时浏览器；未花费真实提供方 token，
  未跑真实 Bug 的新一轮生成，也不声称复现率或官方分数提升。
- 非探索请求也有清单，但缺少组装来源登记时会显示未知，不能声称覆盖全部来源。
- 内容采集总量耗尽或采集故障仍可能导致清单缺失，旧 trace 不补造清单。
- 清单记录“哪些内容进入了请求”；不生成摘要，也不提供未由接口返回的内部思考。
