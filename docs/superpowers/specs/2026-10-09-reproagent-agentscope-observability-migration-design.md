# ReproAgent AgentScope 原生观测迁移设计

日期：2026-10-09。状态：迁移实现中，验收结果另见回执。
基线：main `cd309c685a1591820330fef78893eb4641a68bd9`。
已读本地 AgentScope 2.0.9 与 OpenTelemetry API/SDK 1.45.0 源码。

## 目标与约定

将现有观测迁移到 AgentScope TracingMiddleware + OpenTelemetry，以同一 trace_id 关联一次任务的主流程、Agent 推理周期、模型/工具调用、验收、运行环境和封存后的学习。保留默认采集、已有本地查看页面、实际工具结果与解释材料，不增加模型调用、token、健康检查或用户操作。

用户提供的四条参考意见用于确定链路完整性和异常分类，不作为必须引入远程后端的要求。trace展示可取得的上下文与决定，不能声称还原模型内部完整思维或证明其真实决策原因。

正常命令不变：`reproagent run --config task.json --model-config model.json`。仅 `--no-trace` 关闭。结束后自动产生 observability/trace.json 与 trace.html，不要求额外命令或启动服务。

## 方案比较与选择

1. **采用原生 middleware、统一 OTel 上下文、保留本地投影层（选择）。** 原生负责通用 Agent/模型/工具 span；程序补业务 span/event；本地收集层负责脱敏、有界正文、现有页面结构。既复用基础设施，又保留产品验收语义。
2. 同时运行旧 recorder 与原生 OTel：短期容易，但两套时钟、父子树、计数和ID长期冲突。只允许测试对照，不作为生产双轨。
3. 完全采用远程平台默认视图：通用观察方便，但需服务配置、数据出机，并不能直接呈现现有契约/程序验收。第一版不选择。

## 当前实现与替换范围

当前 `observability.py` 自己生成ID、维护 _current_span 和父子树；`adapters/agentscope/observability.py:TraceMiddleware` 自己创建模型/工具节点。原生 `TracingMiddleware` 在2.0.9提供 on_reply/on_model_call/on_acting，包含消息、token、工具参数结果；并不自动提供单独 on_reasoning span。

移除自建通用 span 引擎；保留 observability.py 作为稳定门面，保留 ContentStore、白名单、容量与安全路径。业务层现有 span/mark/capture/record_check 调用不大规模重写。原生 SDK 文件不可修改、私有函数不可 monkey-patch。

## 统一链路

```text
reproagent.task（唯一根，包含主任务+独立学习）
├─ contract / controller已有phase
├─ explore
│  └─ AgentScope 原生 Agent reply
│     ├─ agent.reasoning（补充，每次原生on_reasoning）
│     │  └─ AgentScope 原生 model call
│     │     └─ model.logical
│     │        └─ model.http_attempt（每次真实请求）
│     └─ AgentScope 原生 tool execution
│        └─ 权限/终态关联信息、SDK最终结果
├─ verify（program.check / provider_claim / verdict关联）
├─ probe / process / cleanup
├─ seal / export
└─ learn（封存后，预算独立）
```

结构表示嵌套关系，不承诺每个任务都会执行全部分支。phase/任务逻辑决定实际次序；reasoning与acting可能是reply下相邻节点，不伪造“工具一定是模型span的孩子”。权限点发生在执行前，用 tool_call_key 关联；拒绝/发布预留没有执行span。

一任务一个OTel trace_id（32位小写hex），操作span_id（16位hex）。任务根以空OTel Context启动，避免意外继承宿主已有trace；合法跨服务传递留作后续。学习共享该trace但保留main_duration/total_duration及独立预算。ContextVar只保存任务sink与业务关联，父子上下文由OTel管理。

## Provider、上下文与生命周期

CLI按需初始化一次本项目拥有的 SDK TracerProvider。原生SDK检查全局provider是否为SDK实例，因此不能仅创建一个未注册的私有provider。使用同步 TaskSpanProcessor 与会话采样器；没有活动会话时DROP，不启动BatchSpanProcessor、OTLP队列或后台线程。

若宿主已经安装其他provider（包括非本项目注册的SDK provider），本版不覆盖、不添加processor、不调用其shutdown；观测降级为不可用并固定警告，任务继续。在此模式不注册本项目的原生tracing middleware。未来嵌入场景另行设计显式provider注入。

--no-trace不初始化provider、不创建会话、不注册观测middleware；在本进程此前已初始化本项目provider的情况下，采样器也拒绝该关闭任务，不能串入其他会话。不得重置OTel全局私有变量来切换任务。

每个已采样span在on_start通过活动会话建立trace_id→sink路由；on_end按trace_id路由，不能依赖结束时当前ContextVar。任务并发隔离；未结束/已注销会话的迟到span不重新打开sink，不写入下次任务。每次迭代附加/恢复OTel context，异步生成器yield前detach，避免跨任务close导致上下文串扰。

close先结束根span，在该on_end完成投影后注销路由；finish再冻结投影；正常CLI必须等主资源关闭及学习结束后调用。外部取消/异常通过会话finally关闭并注销；不吞异常、不为生成trace重跑主任务。全局provider不在每次任务退出时shutdown；进程拥有者统一结束。普通本地同步processor无需排队flush。

## Native与业务补充的边界

注册顺序：`[SafeTracingMiddleware(), ReproTraceMiddleware(...), ExplorationMiddleware(...)]`，仅在本项目会话可用时注册前两项。

SafeTracingMiddleware继承原生TracingMiddleware，调用其公开super hooks创建span；在外层记录handler是否已经执行及已产生结果，观测序列化失败只降级，不重跑handler，也不改SDK私有实现。

原生负责reply/model/tool span；ReproTraceMiddleware只添加reasoning周期span，以及当前native span的受控业务字段、工具关联和终态。不再创建第二个tool span或sdk.model_round通用span。外层原生model middleware不可放在直接调用模型的ExplorationMiddleware之后。

_GuardedModel仍负责一次logical和每次HTTP尝试的业务span、真实记账、协议拒绝前的wire采集。contract/verdict/learning直接模型调用不经过Agent middleware，保留该层覆盖，不能以“原生自动覆盖”删掉。

provider返回推理优先；没有时才显示SDK公开ThinkingBlock的文本并注明sdk来源。系统提示词从实际请求/原生消息中显示，不追加formatter调用。reasoning周期通过内容引用、模型输出、tool_calls、工具结果和已有修订说明展示；未返回解释明确标记，不另让模型写计划或评分。

## 脱敏、有界与原生内容

原生span具有输入/输出、工具参数结果和异常文本。全部经过本地受控projection进入ContentStore，不能将原生attributes/events/resource全部原样落盘。只收已知SDK字段和reproagent命名空间白名单；其他键丢弃。名字、status.description、异常message/stacktrace也要脱敏，不能只处理attributes。

原生工具call ID和未知工具名不原样存储；工具关联沿用trace_id+最近explore+steps_used+SDK ID的哈希。原生与permission相同key，但权限点不计执行。正文来源新增sdk_agent_input/sdk_agent_output/sdk_model_input/sdk_model_output；wire保持独立来源，不能混称精确HTTP请求。

优先保留wire消息与provider响应；SDK视图仅作为另一种实际视图。相同来源/owner/字段只存一次，不把每个SDK嵌套节点的一份usage都相加。SDK最后结果已捕获时无需再存相同副本；来源不同不强行声称相等。

原生实现会在processor之前序列化SDK消息和异常。迁移不承诺其瞬时序列化内存也受ContentStore限额；本地保留数据仍有界。使用SDK SpanLimits：每span最多64 attributes、32 events、0 links、属性值最多65536字符；不将大正文塞入自定义OTel属性，capture直接进入有界ContentStore。SDK dropped计数或属性达到长度上限时保守标partial，正文标截断/omitted，不能当完整JSON解析。不得通过改SDK私有serializer消除这一限制。

现有限额保持：1024个展示span/点、每条metadata JSON≤2048字节、metadata合计≤1MiB；512条正文、每条完整JSON≤32768字节、正文合计≤4MiB；trace.json≤6MiB、HTML≤8MiB。OTel SDK运行中的对象不算这些落盘限额；文档须分开说明。对极端SDK正文的内存峰值留待专门性能验证。

全部数据只在本地；不添加自动远程exporter。后续可在同一个已脱敏、有界的投影出口实现OTLP，但不能直接让其他exporter读取原始native spans。跨服务传播、实时后台、硬杀恢复本次不做。

## 异常与业务结果

两个状态并列：otel_status保留SDK原值；display status由已有业务事实投影。PhaseEnded/BudgetStopped、发布完成后GeneratorExit按既有程序终态展示结束/预算耗尽；不修改原生span或隐去原始ERROR。TaskResult仍决定根任务status，learning失败不改变它。

error_category可取input/authentication/rate_limit/provider/transport/protocol/runtime/cancelled/unknown；classification_source固定http_status/program_code/exception_type。400/422=input，401/403=authentication，429=rate_limit，5xx=provider，网络异常=transport，既有PhaseProtocolError=protocol，受控超时/进程错误=runtime。信息不足保持unknown，不把全部4xx说成用户责任，也不推断认证失败是本地还是服务配置问题。

初版范围是进入task会话后的已有执行与异常；CLI解析、凭据缺失等preflight发生在会话之前，继续用既有错误输出，不为其创建失败任务目录。文档不得声称所有客户端错误已有trace。

## 文件兼容与过程层

新写schema_version=2，原生ID、root_span_id、instrumentation_scope、otel_status和错误来源可见。原schema1文件继续可读可离线重建，不迁移/覆盖历史JSON。trace.json是本地产品格式，不是假装OTLP报文。

真实span保留实际父ID；缺失父节点标partial并呈现“未保留父节点”，不伪造OTel父子关系。程序mark作为当前span的OTel event投影为kind=point：展示ID为`point:<host_span_id>:<ordinal>`，otel_span_id=null，parent_span_id为宿主span；不能把点ID称为OTel span ID。root作为span列表成员计入1024上限。

时间线沿用单调时钟：processor在on_start/on_end记录本地monotonic时间供offset/duration；OTel原timestamp单列作原始时间数据，不用跨时钟相减。根started_at用wall clock。父子耗时不能相加成任务总耗时。

summary仍只汇总model.http_attempt的usage/费用、model.logical的逻辑次数；原生模型token可展示但不二次计费。工具次数来自native tool执行节点，不来自permission/补充event。保持按调用分组的已有页面。

双层观测保留：运行时是trace与现有日志/健康；过程层是本设计、实施计划、测试验收矩阵与失败定位回执。不新建评分Agent，不把trace送回Agent/Verifier/学习/EvidenceLedger/manifest。

## 通过标准

原生middleware确实创建Agent/model/tool span；一条统一trace包含程序和SDK节点；正常/拒绝/预留/重试/取消/学习均可解释。默认与--no-trace业务请求字节、次数、工具输出、预算、结论保持等价。schema1/2、页面、脱敏、限额、Windows长路径和安装包验证通过。

实现步骤及逐项验收见 `../plans/2026-10-09-reproagent-agentscope-observability-migration.md`。本设计不承载测试结果；不得引用旧验收数字作为本次迁移通过证据。
