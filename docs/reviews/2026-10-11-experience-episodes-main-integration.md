# 踩坑学习功能主分支集成

日期：2026-10-11。用户明确授权“push+合并”。

## 合并与验证

- 同步远端后，本地 main 与 origin/main 均为 `1c7a604`；工作区干净。
- 快进合并 `codex/experience-episodes`，代码提交 `160f7d52555f0023b0242c15c16202051936ebc9`。没有冲突，合并后源码和测试树与已验收的分支一致。
- 该代码此前完整 pytest：757 passed、5 个 Windows 链接环境跳过、exit 0；独立 GPT-6.1-sol 审查的三项问题均完成 RED→GREEN 修复。
- E 盘主工作区补测学习材料、SDK 采集、卡片校验和生命周期：53 passed、exit 0，98.66 秒。
- 主工作区 Node 查看器：8 passed、exit 0。
- 本次集成没有新增源码改动；复用完整验收结果，并验证主工作区运行入口。此集成记录仅增加文档。
- 未使用付费模型或 Astra，不把离线验收当作真实模型复现率提升的证明。

主工作区命令：

```text
.venv/Scripts/python.exe -m pytest tests/unit/test_learning_material.py tests/unit/test_learning_capture.py tests/unit/test_experience_learning.py tests/integration/test_experience_lifecycle.py -q --basetemp C:/Users/Dell/AppData/Local/Temp/pitfall-main-integration-retry-c2d4e5a1
E:/node/nodejs/node.exe --test tests/frontend/trace_viewer.test.mjs
```

首次补测在受限执行环境中停在第一例，未形成测试结果，已停止本次测试进程。保持同一代码和解释器，切换到此前完整验收使用的权限方式，第一例 1 passed（4.04 秒），随后全部 53 项通过；无需修改产品代码。

## 本地证据

- `.local/pitfall-main-integration-final.log`：主工作区 53 项。
- `.local/pitfall-main-node.log`：主工作区 Node 8 项。
- `.local/experience-episodes/full-final.log`：此前完整验收 757/5。
- `.local/experience-episodes/review-green.log`：审查修复回归。
- `.local/experience-episodes/diag-8365/projection-result.json`：8022 字节、两个候选片段、原历史记录未改变、模型调用为零。
- `.local/experience-episodes/diag-8365/task/learning/evidence.jsonl`：离线材料胶囊。

这些文件在 Git 忽略目录中，留在 E 盘供本地检查。只归档本次已合并的 managed worktree；其他工作区保持原状。
