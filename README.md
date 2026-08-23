![](res/img/atri.png)

# ATRI-LK

### 👋 ATRI-LK：厨力项目的改进版本

アトリは、高性能ですから！

ATRI-LK 致力于在 QQ/OneBot 即时聊天平台中复现一只功能丰富、表现稳定的机器人。项目基于原 ATRI 开发，并加入更多扩展功能。

> 本项目新版部分功能依赖 [napcatAPI](https://napcat.apifox.cn/)。在非 [NapCatQQ](https://napneko.github.io/) 机器人环境下可能会出现兼容性问题。

## 📌 声明 | Declarations

**本项目仅供学习与研究使用，请勿用于非法用途。**

- 原项目地址：[Kyomotoi/ATRI](https://github.com/Kyomotoi/ATRI)
- 项目名称与灵感来源于 [ANIPLEX](https://aniplex-exe.com/) 发行的 [ATRI-My Dear Moments-](https://atri-mdm.com/)
- 本项目中涉及的 ATRI 相关图标、LOGO 等版权归 [ANIPLEX](https://aniplex-exe.com/) 所有
- 相关使用规范请查阅：[ANIPLEX 使用指南](https://aniplex-exe.com/guidelines/)

## ✨ 特性概览 | Features

- 使用 [NoneBot 2](https://v2.nonebot.dev/) 作为底层框架
- 在原 ATRI 基础上增加更多功能和插件支持
- 遵循 [OneBot v11](https://onebot.dev/) 标准
- 支持可扩展插件系统，便于二次开发与个性化配置

## 📱 功能概览 | Services Overview

<details markdown='1'><summary>系统插件</summary>

- 帮助
- 管理
- 基础
- 状态
- 反馈
- 广播
- 更新
- 插件商店

</details>

<details markdown='1'><summary>LK系列插件</summary>

- [x] 用户系统
- [x] 物品商店系统
- [x] 群聊模式对策
- [x] 附属-图库

> 附属可移除。

</details>

<details markdown='1'><summary>内置 LK 附属插件</summary>

> 已勾选的功能已基本完成，仍可能继续优化。
> 未勾选的内容已在项目中加入但仍在补充中。
> 计划中表示未来将继续开发。
> 划线项目暂时不再更新。

- [x] 签到
- [x] 投喂
- [x] 运势
- [x] 聊天
  - [x] AI聊天包含记忆、词语解释
  - [x] 戳一戳ATRI
- [ ] ~~宠物~~
  - [x] 新宠物
  - [x] AI聊天
- [ ] 农场
  - [x] 新农场
  - [x] 锄地、浇水、种植、收获、移除
  - [x] 品质
  - [x] 肥料
  - [x] 天气
  - [x] 耕种等级
  - [ ] 幸运值(计划中)
- [ ] 钓鱼
  - [x] 钓鱼
  - [x] 鱼竿,鱼饵
  - [x] 鱼具
  - [x] 35种鱼,4种非鱼钓鱼战利品
  - [x] 成就
  - [x] 宝藏
- [ ] ~~探险~~

</details>

请在 [ATRI-LK-plugin](https://github.com/lokyoh/ATRI-LK-plugin) 或插件商店下载更多扩展插件

## 🤖 ATRI 智能体 | ATRI Agent

本项目自研的智能体系统，支持：

- 好感度管理
- 用户记忆与画像
- 自定义词语解释
- 表情发送
- 网络搜索
- 亚托莉日程

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
