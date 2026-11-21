# YHN-LK0-025 Patch1:

- 移除:
  - `Agent`移除旧的整日日程接口,日程统一由生活系统提供
- 修复:
  - `插件商店`修复 Python 3.13 下插件列表报错bug[#16](https://github.com/lokyoh/ATRI-LK/issues/16)
  - `ATRI`修复定时任务错过执行窗口、重启丢参数与重复补发
  - `QQ`断线日志改为一行摘要,不再打印整条调用栈
  - `Agent`修复回复长度上限对直发路径无效
