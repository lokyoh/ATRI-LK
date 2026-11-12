![](res/img/atri.png)

# ATRI-LK

### 👋 ATRI-LK：厨力项目的改进版本

アトリは、高性能ですから！

ATRI-LK 是一个基于 [NoneBot 2](https://v2.nonebot.dev/) 的 QQ 聊天机器人项目，在原 ATRI 的基础上补齐了完整的内置服务、智能体系统、Web 管理界面与插件生态。支持 OneBot v11（NapCatQQ、SnowLuma 等）与 QQ 官方机器人两种接入方式。
 
> QQ 官方机器人适配器已覆盖部分功能，两套适配器可在同一实例中同时运行。

## 📌 声明 | Declarations

**本项目仅供学习与研究使用，请勿用于非法用途。**

- 原项目地址：[Kyomotoi/ATRI](https://github.com/Kyomotoi/ATRI)
- 项目名称与灵感来源于 [ANIPLEX](https://aniplex-exe.com/) 发行的 [ATRI-My Dear Moments-](https://atri-mdm.com/)
- 本项目中涉及的 ATRI 相关图标、LOGO 等版权归 [ANIPLEX](https://aniplex-exe.com/) 所有
- 相关使用规范请查阅：[ANIPLEX 使用指南](https://aniplex-exe.com/guidelines/)

## ✨ 特性概览 | Features

- 使用 [NoneBot 2](https://v2.nonebot.dev/) 作为底层框架，遵循 [OneBot v11](https://onebot.dev/) 标准
- 同时支持 OneBot v11 与 QQ 官方机器人，同一套内置服务尽量保持两端一致
- 自研智能体：长期记忆、用户画像、技能、功能调用与主动开口
- 独立的 Web 管理界面：配置编辑、服务与插件管理、Agent 调试、实时日志
- 完整的内置服务与插件商店，支持扩展插件的安装、更新与卸载
- 分群、分人、全局的服务控制，支持禁用、解禁、封禁与白名单

## 📱 功能概览 | Services Overview

<details markdown='1'><summary>系统服务</summary>

- 帮助、管理、状态、反馈、广播
- 更新、重启、插件商店
- 备份、调度管理、图库、年终总结

</details>

<details markdown='1'><summary>LK 系列服务</summary>

- 用户：账号绑定、背包、物品、商店、排行榜
- 群管 / 主人：群聊管理与主人专用指令
- 聊天：@ 触发、主动聊天，接入智能体
- 智能体：长期记忆、用户画像、技能、功能调用、主动开口
- 农场：种植、品质、肥料、天气、耕种等级、幸运值
- 钓鱼：鱼竿鱼饵、35 种鱼、成就、宝藏
- 宠物：养成、技能、好感度、轮回、肉鸽爬塔（Demo）
- 签到、投喂、运势、RSS 订阅

</details>

除内置服务外，可在 [ATRI-LK-plugin](https://github.com/lokyoh/ATRI-LK-plugin) 或插件商店安装更多扩展插件。

## 🤖 ATRI 智能体 | ATRI Agent

本项目自研的智能体系统，为聊天服务提供底层能力：

- 带遗忘的长期记忆与自我记忆
- 用户画像、别名与关系查询
- 自定义词语解释
- 技能系统：按需查阅玩法与操作说明
- 功能调用：网络搜索、图片识别、日程管理、文件操作等
- 主动开口与内心状态
- 好感度、表情发送与回复风格约束

## 🖥️ Web 管理界面 | WebUI

配套的 [ATRI-LK-WebUI](https://github.com/lokyoh/ATRI-LK-WebUI) 会在启动时自动下载并保持更新，浏览器访问机器人的 `host:port` 即可打开：

- 仪表盘与运行状态总览
- 服务、插件与配置的可视化编辑
- Agent 模型、功能调用、技能与 Token 用量管理
- 实时日志查看与版本更新

登录账号与密码保存在 `config.yml` 的 `WebUIConfig` 中。

## 🚀 开始部署 | Getting Started

请参考官方文档：

- [部署项目](https://lokyoh.github.io/ATRI-LK-docs/quick_start/introduction.html)

## 📖 文档 | Documentation

更多使用说明与开发文档请访问：

- [ATRI-LK 文档站点](https://lokyoh.github.io/ATRI-LK-docs/)
- [文档仓库](https://github.com/lokyoh/ATRI-LK-docs)

本站点基于 [VitePress](https://vitepress.dev/) 构建。

## 🔊 更新日志 | Changelog

版本更新请查看：

- [changelog.md](changelog.md)
- [Release 页面](https://github.com/lokyoh/ATRI-LK/releases)
- [提交记录](https://github.com/lokyoh/ATRI-LK/commits/main/)

## 🐧 QQ 群

- [ATRI-LK 交流群](https://qm.qq.com/q/8Gx7UxXnA4)：日常交流、提问与新手指导
- [ATRI-LK 技术群](https://qm.qq.com/q/cE5ceehNHq)：获取开发资讯、提交建议与插件交流

## ❤️ 特别感谢 | Acknowledgments

- [Kyomotoi](https://github.com/Kyomotoi)：原项目 [ATRI](https://github.com/Kyomotoi/ATRI) 及其贡献者
- [Bot Universe](https://github.com/botuniverse)：OneBot 标准
- [NoneBot](https://github.com/nonebot)：NoneBot2 框架
- [JetBrains](https://www.jetbrains.com)：为本项目提供 PyCharm 等 IDE 授权

## 📄 许可 | License

本项目采用 [GPLv3](https://www.gnu.org/licenses/gpl-3.0.html) 许可。

这意味着你可以运行本项目并对外提供服务，但如果你修改了源码，需要公开修改后的源码。
