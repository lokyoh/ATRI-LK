---
description: 文件操作：查目录树、读文件。两个功能都不常用，只能通过 call 调用
---

# 文件操作

这是给智能体自己查代码、查配置用的能力，不对用户开放。**两个功能都不常用**，不会出现在常规功能列表里，必须通过 `call` 调用。

## 可用功能

| 功能 | 作用 | 参数 |
|---|---|---|
| `get_dir_tree` | 查目录树 | `dir`：目录代号 |
| `read_file` | 读文件内容 | `path`、`start`、`end` |

## get_dir_tree

`dir` 只接受下面六个代号，写别的一律返回"请提供正确的目录名"：

| 代号 | 指向 |
|---|---|
| `root` | 根目录（不含 `ATRI` / `data` / `plugins` 的深层内容） |
| `ATRI` | 系统代码目录（不含 `system`） |
| `system_plugins` | 系统插件目录 |
| `plugins` | 用户插件目录 |
| `config` | 配置目录 |
| `errors` | 最近 7 天的错误报告 |

用法：

```text
call(function="get_dir_tree", args={"dir": "plugins"})
```

## read_file

- `path`：相对机器人根目录的路径，例如 `ATRI/system/agent/config.py`；
- `start`、`end`：可选的行号范围，`start` 默认 1、`end` 默认 0（读到末尾）。大文件**务必**用行号分段读，否则一次返回的内容会撑爆上下文；
- 路径不合法或文件不存在时返回错误说明，不会抛异常。

用法：

```text
call(function="read_file", args={"path": "ATRI/system/agent/config.py", "start": 1, "end": 40})
```

## 使用建议

- **先 `get_dir_tree` 再 `read_file`**：不确定路径时先看目录，别猜。
- 一次只读需要的行段；确认无关后不要反复读同一段。
- 这两个功能只读不写，读完即可，不需要回报给用户。
- 用户问"你能看文件吗"这类问题时，说明这是你查阅自身代码与配置的能力，不涉及用户设备。
