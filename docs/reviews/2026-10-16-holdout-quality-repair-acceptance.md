# 五例修复离线验收与真实运行入口

## 已验证

- 隔离分支 codex/holdout-execution-reliability；基线 aac87160e22f7c44cf9d0bd0b6d7f5d2d029e807；主工作区其他代理改动未覆盖。
- 全量离线：1324 passed, 10 skipped，exit 0，1219.70 秒。日志：E:/ReproAgent/.local/holdout-five-20261015/phase2-full.log；基准临时目录在工作区外。
- 收集上下文修正、真实 SDK 交付约束、包装器、oracle 相关 66 项通过。独立审查随后发现 timeout 的 CLI float 与 receipt int 契约不一致，新增三项失败回归再修复；包装器 11 项通过，全量亦覆盖修正后版本。
- 依赖档案/日志/脚本/轮次绑定与环境结果挂接相关回归 94 passed, 4 skipped；真实 symlink 测试受 Windows 权限限制而跳过，模拟 reparse 拒绝分支有覆盖。
- 第一阶段保存的 8906 重跑对：机械签名从 0/15 到 15/15；这不是新语义批准或官方成功。
- 独立审查使用非 Astra 模型。验证过原版收集钩子清标记的反例后，Controller 静态平台判断收紧为 target_unverified。wrapper 的 timeout 必须在外部执行前是正整数。
- 38 个历史原版审查上下文加 oracle 后均不超过 32768 字节，最大 31324。没有 tokenizer 证据证明输出 token 预算足够；真实运行仍是必要验证。

## 真实轮次（结果另记）

E:/ReproAgent/repro-results/holdout-reliability-20261016

五条冻结绑定均通过预检。运行代码实际导入自本工作树，源指纹：2bbd54e326c23cbe26d27f94e298aeeabbc8748c5ffab1998895260e744d6c67。模型 deepseek-flash、thinking disabled、20 步、900 秒、命令 60 秒、32768 字节；没有金标输入或手写替换候选。源指纹运行中再次核对一致。

已从 8365 真实 wire_request 确认 Linux delivery 及固定 harness 来源进入探索请求。单次本地结果不证明跨平台交付成功。

WSL 实际导入亦来自本隔离工作树；官方 harness 330a649a764fab2fadaea632776eeae87272f74b，验前源 hash a27011af436dad5552a72e1f4cf5ced9a8c7edf1c0e178cceec1ac0e9cb1f007。Docker 使用已有安装，未改全局 harness 输入、未删共享 locks、未杀其他会话。旧同步脚本会覆盖为主工作区版本，故未使用。

## 尚不能据离线测试下的结论

- 不能宣布五例都修复成功，也不能宣布 8365 Linux 差分成立。
- 原子 oracle 的支持性仍由独立语义上下文判断，程序保证覆盖和证据引用，不保证模型每次判对。
- 8474 如仍缺真实构建输入，应返回缺信息，不能伪造触发条件。
- 8627 新候选必须独立核对；旧环境诊断不得移植为新候选成功。环境诊断不改官方计分。
- 新轮次与 003 的工具版本不同；单次方向变化不能等同稳定能力提升。

实现方案：../superpowers/plans/2026-10-16-holdout-quality-and-delivery.md。
