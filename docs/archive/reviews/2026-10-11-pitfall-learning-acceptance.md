# 踩坑学习材料验收

> **历史归档（2026-10-24）**：保存当时的设计、运行结果或验收依据，不能据此判断当前默认行为、最新测试数量或总体复现率。
> 当前入口见[文档导航](../../README.md)，旧路径对照见[归档索引](../README.md)。

状态：离线验收完成。最终完整 pytest：757 passed、5 skipped、exit 0；Node：8 passed、exit 0。

实现位于隔离工作区 `experience-episodes`，分支 `codex/experience-episodes`，基线 `1c7a604`。该验收记录生成时尚未合并或推送；未运行付费模型，验收与独立审查未使用 Astra。

## 实现范围

- 独立 `learning_material.py` 整理事实，不再借用 Verifier 投影。
- 相同模型配置、独立上下文/客户端/预算；主任务资源关闭且结果封存后才提炼。
- 三类 EID 索引、实际尝试/运行 ID、原版验收分类和重跑确认状态；最多两次代表性尝试。
- 保留 SDK 返回的少量工具错误及实际 Read/Grep 引用，trace 关闭时也可用；不存完整聊天副本。
- 用户 JSON 总计最多 8192 字节；候选代码优先于次要失败细节，大材料逐项省略并记录原因。
- 精确原版哈希/行号，原子冻结 `learning/evidence.jsonl`；后来追加事件不能改变卡片引用。

未改探索策略、权限集、主预算、Verifier、thinking 配置或主交付包。仍然一次逻辑学习请求，卡片最多 2048 字节，无新增依赖或服务。

## 检查表

| 检查 | 证据/结果 |
| --- | --- |
| 两次尝试、不同代码、原版重跑确认 | 真实 pytest + 脚本模型控制 Controller 的单元测试 |
| 相同代码反复发布不掩盖较早不同尝试 | 回归先失败、修复后通过 |
| 大堆栈/大候选不丢弃其他可用类别 | 受控超限材料与 UTF-8 总大小断言 |
| trace 关闭仍能保留工具错误和原版引用 | 真实 AgentScope + HTTP MockTransport + pytest |
| 无候选的 NEEDS_INFORMATION 仍可学习已有事实 | 诊断包封存后一次独立学习请求 |
| 取消、只读、主预算耗尽、模型/库故障 | 生命周期回归；主结果与包保持原值 |
| 已注册的修复版本快照禁止进入材料 | 真实 Workspace.freeze 固定版本负向回归 |
| Unicode 行界与改动后的来源拒绝 | 小候选与原版片段边界回归 |
| 采集/retirement 出错仍清理客户端 | SDK capture 故障与 factory 清理回归 |
| 经验材料与事件不重复计 token/费用 | 既有 SDK learning/usage 回归与完整验收通过 |
| HTML 查看器 | Node 8 项通过，exit 0 |

## 独立审查与修复

GPT-6.1-sol 的一次新上下文只读审查发现三项 P2；全部实际复现后修复：

1. 重复失败细节挤掉有效候选片段：先保留尝试概况及候选，再补失败细节。
2. 后续固定版本 snapshot.created 被误认成原版：仅注册任务首次原版冻结；original 执行绑定必须一致。旧记录没有冻结事件时，只接受唯一 original 执行快照。
3. 小候选提前返回绕过 Unicode 行界校验：大小分支之前统一验证。

修复后的相关测试：53 passed，exit 0，日志 `.local/review-green.log`。无延后的小问题；审查未作判断项为空。

初次完整验收：753 passed、5 skipped、1 failed；唯一失败为新增 MockTransport 检查把 diagnostic 包当作 reproduction 包。修正测试后整个生命周期 25 项通过；最终完整验收重新运行并通过：757 passed、5 skipped、exit 0，605.22 秒，日志 `.local/full-final.log`。五项跳过涉及 Windows 链接环境：四项缺少链接创建权限，一项要求解释器经链接启动；其他已支持的路径/遍历边界仍运行。

## 保存的真实案例

仅离线投影 pytest #8365 已保存结果，未重新调用模型或重跑目标项目。对副本重写存储根，不改历史 trace、verdict 或学习输出。

| 指标 | 最终修复后 |
| --- | --- |
| 用户 JSON 字节数 | 8022 / 8192 |
| 代表性尝试 | 2 |
| 保留候选代码片段 | 2 |
| framework/model/workflow 索引 | 7 / 8 / 5 个 EID（同一 EID 可以在多个类中） |
| 原始 trace/verdict SHA256 | 保持一致 |
| 付费模型调用 | 0 |

省略的是候选其他上下文、部分额外失败细节和长假设，均明确标注。程序不把片段当成完整程序、不把时间顺序或候选假设当作原因证明。离线检查文件在 `.local/saved-diag-8365-v3/projection-result.json`。

完整验收命令：`.venv/Scripts/python.exe -m pytest -q -rs --basetemp C:/Users/Dell/AppData/Local/Temp/pitfall-full-final-fb8e14d7`；查看器：`E:/node/nodejs/node.exe --test tests/frontend/trace_viewer.test.mjs`。未调用付费提供方。

## 验证边界

离线验收验证事实选择、大小、引用、生命周期和调用隔离。未证明经验语义正确或提升复现率；后者需要冻结经验库并分离学习/评测集，进行真实模型 A/B。当前材料是代表性经过，不是完整探索上下文。

## 主分支集成

用户随后明确授权合并并 push。2026-10-11 将 `codex/experience-episodes` 快进合并至 E 盘 `main`；代码提交为 `160f7d5`，没有冲突或源码改动。主工作区补测 53 passed、exit 0；Node 查看器 8 passed、exit 0。详细记录见 [集成补测踩坑说明](../../reports/问题与修复索引.md#主工作区验证受限环境中没执行完不算产品失败或通过)。原隔离工作区的测试日志与离线案例胶囊保存在 `.local/experience-episodes/`，此前相对日志路径指该目录内同名文件。
