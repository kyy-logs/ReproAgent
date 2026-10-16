# Generator 断言显示地址的重放修复

真实5221同一候选在两次独立语义批准后，仅 any(<generator ... at 0x...>) 地址不同而失败。旧记录没有观测对象ID，不反填或追认旧成功。

- probe 仅对 AssertionError 无二元比较的 fallback，从最内层 frame 观察前256个局部变量名，仅采集 pytest 重写的 @py_assert 临时值（普通locals不进入遍历）；沿既有64 IDs/256visits记录对象ID并排除辅助列表自身身份，不记录局部值或调用repr/迭代器。
- full repr 正则显式容许 <locals>/<genexpr>，继续要求观测ID白名单、引号保护和完整显示。
- fallback crash 使用这份白名单；已有二元比较仍只使用各自 operand IDs。
- 不改 RunResult duration 或引号内 stdout 计时，不抹异常/断言行/实际值。两个独立语义验收仍必须通过。
- RED→GREEN：真实pytest重写+两次原版执行确认、无/错ID、引号、不同函数名、计时及现有二元比较边界。单独提交；真实评测必须新运行，旧分数不追认。
