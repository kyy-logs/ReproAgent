# 验收上下文超限修复记录

## 范围

保留本地已有的超限大小提示和 CLI 默认经验库改动，修复以下行为：

- `review_context.py`：超限时将重复的堆栈、异常帧和 crash 改成对完整失败证据的引用。
- `verifier.py`：区分原始证据超限和模型格式纠错提示超限；记录上下文大小、上限及检查结果。
- `controller.py`：相同候选因原始证据超限被阻塞后，核对文件和引用完整性，避免再次执行；测试、契约或执行设置变化后仍可运行。
- `cli.py`、`experience.py`、`docs/experience.md`：省略经验路径使用 CLI 默认库，显式 `null` 完全关闭；环境变量相对路径在配置加载时固定为绝对路径。
- 对应测试和验收提示词说明。

没有调整模型、thinking、32,768 字节上限、源代码保护或成功复现的验收规则。没有调用真实模型，没有改写历史案例的 trace、HTML 或 verdict。

## 证据去重规则

只有属于同一次 run、同一测试节点及阶段，且待移除详情与已校验 probe 行一致时，才生成 `failure_evidence_ref` 和 `referenced_fields`。

失败证据的完整正文、文件哈希和行范围保持可见。完整候选代码、期望来源和结构化断言比较仍保留。字段不一致时不去重，文件哈希不一致时拒绝验收；去重后仍超限时保留未确认状态。

缓存只使用程序产生的 `next_action=narrow_candidate`。它不把旧 verdict 绑定成新候选的 verdict，不缓存普通语义拒绝或成功结果，也不替代独立重跑。格式纠错提示超限返回 `MODEL_PROTOCOL_ERROR` 不确定性，不进入这个缓存。

## 已保存的真实案例离线检查

使用 `repro-results/diag-8365/cases/pytest-dev__pytest-8365/task` 中已有的八组执行证据：

- 原输入：43,106～43,191 字节，超过 32,768 字节。
- 修复后：30,724～30,785 字节，每组有五个阶段引用完整失败正文。
- 八组均经过程序校验并到达本地模型请求替身，每组一次 `verdict` 请求。
- 网络调用为零；替身返回未确认，不用于判断真实模型的语义准确率。
- 原 `trace.json`、`trace.html` 和八份 verdict 的 SHA-256 检查均未变化。

明细：`.local/review-context-repair-case-check.json`。

## 回归验证

- 初始最小回归：四项按预期失败，覆盖超限、重复执行、显式关闭和相对路径问题。
- 相关回归：91 通过、1 跳过。
- 审查发现纠错提示超限被误缓存：新增回归先失败，修复后通过。
- 前端查看器：8 通过。
- 仓库外独立临时目录的 SWT 控制及来源指纹回归：17 通过。

完整套件首次使用仓库内临时目录：730 通过、8 失败、5 跳过。失败集中于七项 SWT 控制测试和一项来源指纹测试：临时项目继承了上层 pytest 配置及 Git 仓库信息。没有为绕过这些失败修改产品或测试逻辑；改用仓库外全新临时目录后，上述模块全部通过。

首次完整运行失败项（均在仓库外临时目录复验通过）：

- `test_success_requires_buggy_call_failure_and_all_fixed_calls_pass`
- `test_collection_error_is_not_a_discriminating_control`
- `test_skipped_or_setup_failed_nodes_do_not_qualify[setup_failure]`
- `test_skipped_or_setup_failed_nodes_do_not_qualify[skipped]`
- `test_control_never_mutates_generation_repositories`
- `test_control_cli_records_every_selected_case`
- `test_control_subprocess_environment_excludes_secrets`
- `test_capture_covers_code_prompts_resources_evals_and_pyproject`

完整套件最终结果：**738 通过、5 跳过，623.79 秒，退出码 0**。使用仓库外全新专用临时目录运行 `python -m pytest -q --basetemp=<temporary-directory>`。日志：`.local/review-context-repair-full-outside.log`。首次完整结果保留于 `.local/review-context-repair-full-final.log`；因审查修复而提前中断的一轮保留于 `.local/review-context-repair-full-interrupted.log`，不计作完整验证。

本地代码审查发现的纠错提示超限缓存问题已通过失败→修复→通过的回归确认。最终完整套件包含该回归；没有遗留审查发现项。提交和发布版本以 Git 记录为准。
