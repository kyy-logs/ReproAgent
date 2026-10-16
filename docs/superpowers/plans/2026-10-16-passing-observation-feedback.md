# 原版通过时的有界输出反馈

事实：真实轮次 holdout-reliability-20261016 的 8906 在原版通过后请求 pytester 子进程 stdout；既有 probe 的 passing call 没保留 captured stdout，NOT_REPRODUCED 反馈只给结论和静态风险。

1. probe 对 passed call 保存 stdout/stderr，各最多4096 UTF-8字节，保留首尾，显式总大小/摘录大小/是否截断。其他阶段不重复，旧记录兼容。
2. collector 为这条通过输出附绑定完整 probe hash 的单行引用；输出是执行观察，不是预期来源。
3. NOT_REPRODUCED 的确定性反馈最多携带两个有内容的原版通过记录，stdout/stderr分别再摘录1024字节；保留run_id、nodeid、来源引用、截断标记及省略数量。重新校验hash/原始行，不传修复版日志。先红后绿验证假引用、输出过大、脱敏、旧数据无输出。
4. 复用8KiB反馈投影和既有省略标记；不增加模型调用或改变验收。完整保存输出仍只到probe自身上限，不能声称无限完整。
5. 对应回归、独立审查、全量测试；8906用新轮次重跑，旧五例与官方重试仍绑定c6690cd原预测，不混算。
