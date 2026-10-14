# YHN-LK0-024 Release:
> !!!破坏性更新!!! 新增QQ适配器,需更新依赖;完全重构`PluginConfig`会导致一批插件挂掉,请及时更新插件;需重新配置`tts`、`embedding`类型模型

- 新增:
  - 新版`宠物`插件Demo
  - `Agent`新增Token统计
  - `WebAPI`新增与更新接口
  - 部分功能适配QQ官方机器人
- 重构:
  - `PluginConfig`重构,实现更好的前端配置体验
  - `Agent`的`tts`、`embedding`模型配置重构,统一使用provider注册使用;重构处理流程;重构功能调用
