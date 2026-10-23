# 复现测试 Skill 接入验收

日期：2026-10-23。分支：`codex/reproduce-bug-skill`，基线：`1477ee9`。下述真实冒烟保留当时来源与行为；提交前采用首次请求临时注入，见当前加载链路。

## 实际加载链路

当前链路：`ExplorationMiddleware.on_model` → `reproduction_skill` → 包内 `resources/skills/reproduce-bug/SKILL.md` → 首次模型请求的 SystemMsg。

- 每个任务首次探索请求默认加载，无需新增命令或配置。作为临时 SystemMsg 注入，不持久写入消息历史；后续请求不重复发送完整正文。
- 只读取固定的产品包资源；不扫描目标仓库的 Skill，不依赖启动目录，不读取全局 Codex Skill。
- 只加载正文，不把 YAML 发现元数据发送给模型；正文缺失或格式损坏不静默关闭指导。
- Skill 注入与经验开关独立，关闭经验库也保留；契约分析不额外接收该 Skill。
- 工具、权限、预算与验收流程不变；正文计入现有完整请求容量守卫。没有新增模型调用，但正文占用输入上下文及 token。
- `pyproject.toml` 纳入 wheel 包数据；现有来源指纹递归覆盖 resources，Skill 变化会使评测来源校验失败。

## 离线验证

实际从此 worktree 导入 `reproagent`，使用主环境依赖；设置 `PYTHONPATH` 指向此 worktree 的 src，pytest basetemp 位于仓库外。

- 新单元测试首次运行：4 failed / 1 passed，失败对应缺少资源和加载函数；随后实现加载。
- 新增三组测试 8 项：经验开关独立性、损坏正文、来源指纹、真实 SDK 请求包含完整正文且每轮一份、工具与请求次数不变、契约分析隔离、wheel 脱离源码目录读取。
- 最终相关回归：**68 passed in 49.53s**。范围为 `test_reproduction_skill.py`、`test_agent.py`、`test_experience_catalog_metadata.py`、`test_swt_provenance.py`、`test_model_protocol_regressions.py`、`test_reproduction_skill_delivery.py`、`test_reproduction_skill_package.py`、`test_agentscope_explorer.py`。
- 包校验使用 `pip wheel --no-index --no-deps --no-build-isolation`，再在仓库外通过独立 Python 模式从 wheel 读取正文；确认实际导入来自 wheel。
- Skill Creator quick_validate 通过，git diff --check 通过。独立只读代码审查未发现阻断问题。

## 边界

测试的模型传输使用 MockTransport；上述离线验收阶段没有调用付费模型、没有使用 Astra，也未进行真实公开 Bug 的成功率对照。以上只证明加载、打包和既有链路兼容，不证明模型会遵循每条指导或复现率提高。

主检出 `E:/ReproAgent` 中此前的 `skills/reproduce-bug/SKILL.md` 是起草文件，未在本分支另存副本；本分支实际维护与运行读取的是包内文件。主检出尚未合入本次修改，使用主检出旧启动器不代表运行了此分支。


## 后续真实单例冒烟（同日）

用户明确授权后，使用已有本地凭据、deepseek-flash、thinking=disabled，运行 pytest #5227 一例；经验读写关闭。没有使用 Astra。

- 输出：`E:/ReproAgent/repro-results/skill-live-5227-20261023-223801`。
- 状态：DONE / DIFFERENTIAL_VALIDATED；fix_validation_status=passed，导出 published。
- 最终同一候选：原版独立运行两次均 exit=1，修复版一次 exit=0；三个探针完整。
- 总计4个候选、7次执行、6份Verdict，耗时138.5s；22次HTTP全部200，输入230,973 / 输出10,029 token。
- 3份完整保留的探索wire request均逐字包含Skill正文一次；其后17份wire正文受到原有采集上限截断，不宣称逐轮完整留存。
- 实际从本worktree导入，运行前后源码指纹和Skill正文哈希一致。源码指纹：`8473e1b562e5f98666cddb618e5e158a3ca904daed7d120a6efe515c6a75818a`。
- 机读回执及说明：`E:/ReproAgent/.local/claude-code-tasks/skill-live-5227-20261023-223801/validation.json` 和 `validation.md`。

这次证明接入分支可以完成真实本地差分复现；没有跑官方harness，也没有无Skill对照，不能证明Skill提高成功率。


## 提交前加载策略补验

当前版本只在首次探索请求发送完整Skill。相关单元、Agent与真实SDK请求捕获测试：14 passed in 6.92s；先前最终119项相关回归亦覆盖首次请求发送、第二次不再发送及wheel读取。上述真实单例冒烟属于此前持续系统提示的版本，不作为首次临时注入策略的真实效果证明。
