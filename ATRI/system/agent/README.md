# ATRI Agent

ATRI-LK 项目的智能体（Agent）模块，负责统一管理模型、消息、记忆、工具调用和调度逻辑。它既支持基础文本对话，也可扩展为更复杂的多角色、多模型协作场景。

## 1. 模块定位

`ATRI.system.agent` 是系统级智能体能力层，核心职责包括：

- 统一封装 LLM 与多种模型类型（聊天、工具、图片、嵌入、语音等）
- 维护模型提供商配置和注册流程
- 管理 Agent 历史对话、记忆、用户资料与任务调度
- 提供函数调用与工具链接入能力
- 对外暴露统一的 `llm_manager` 与插件配置入口

## 2. 目录结构

```text
ATRI/system/agent/
├── __init__.py          # 模块初始化与导出
├── config.py            # Agent 配置与 Provider 加载逻辑
├── service.py           # 插件注册与命令入口
├── agent/               # Agent 实际运行逻辑
│   ├── chat_chain.py    # 对话链路
│   ├── function_calling.py
│   ├── function_permission.py  # 三级权限与作用域求值
│   ├── action_executor.py      # 校验 / 分流 / 并发 / 回填
│   ├── outbox.py        # 聊天输出队列（保序发送）
│   ├── reply_pipeline.py       # 清洗 / 分条 / 去重
│   ├── history.py       # 历史记录管理
│   ├── memory/          # 记忆系统
│   ├── life/            # 固定作息 / 日程 / 位置 / 着装 / 家
│   ├── schedule.py      # 旧日程接口 facade
│   ├── sender.py        # 消息发送器
│   └── ...
├── brain/               # 思考与表达
│   ├── thinking.py      # 决策循环
│   ├── chat_model.py    # 意图 → 表达（ATRI_CHAT）
│   ├── action_parser.py # DSL 解析与参数校验
│   └── reply_judgment.py
├── llm/                 # 模型定义、Provider、工厂函数
│   ├── model.py         # ModelType、LLMManager、LLMResponse
│   ├── provider.py      # ProviderManager 与默认 provider
│   ├── contents.py      # 消息内容封装
│   ├── openai.py        # OpenAI/OpenAI-compatible 接口
│   ├── gemini.py        # Gemini 接口
│   ├── system_one.py    # SystemOne 接口
│   ├── embedding.py     # Embedding 模型
│   ├── tts.py           # TTS 模型
│   ├── log.py           # 模型输入输出独立日志
│   └── ...
├── utils.py             # 日志与通用请求封装
└── README.md            # 文档入口
```

## 3. 关键概念

### 3.1 LLM 模型类型

模型类型由 `ModelType` 枚举定义。Agent 只认 `atri-*` 类型，**绝不回退**到 `chat` / `tool` 等通用类型：

| 类型 | 用途 | 必填 | 缺失后果 |
|---|---|---|---|
| `atri-think` | 决策中心：读上下文、选工具、产出意图 | 是 | Agent 无法思考，拒绝聊天并告警 |
| `atri-chat` | 聊天措辞：意图 → 自然文本 | 建议 | 链路继续工作，但 `say` / `md` 降级为 intent 直发、`face` 跳过 |
| `atri-judge` | 主动聊天判断 | 主动聊天 | 不主动插话，只响应 @ / 点名 |
| `atri-memory` | 记忆抽取 / 总结 | 记忆功能 | 记忆总结与写入工具隐藏 |
| `atri-profile` | 用户画像生成与修改 | 画像功能 | `profile` 只能读，不能改 |
| `atri-schedule` | 填入式日程 | 生活系统 | `plan(fill)` 不可用；固定作息仍可读 |
| `atri-outfit` | 白天 / 晚上两套着装 | 生活系统 | 显示「还没决定今天穿什么」 |
| `atri-home` | 家的生成 / 扩写 | 可选 | 使用仓库自带模板 |
| `atri-summary` | 转发 / 长文总结 | 可选 | 总结功能隐藏 |
| `atri-vision` | 图片理解 | 图片功能 | 图片统一记 `[图片]` |
| `atri-embedding` | 记忆检索向量 | 记忆功能 | 检索 / 写入工具隐藏 |
| `atri-tts` | 语音合成 | 可选 | 只发文字 |

通用类型 `chat` / `work` / `tool` / `image` / `embedding` / `tts` / `sys1` 继续供 `lkpet`、`trpg`、`feed` 等非 Agent 服务使用；Agent 不消费它们。

### 3.2 `LLMManager`

`LLMManager` 是统一的模型调度器，负责：

- 注册模型：`register_model()`
- 获取模型：`get_model()` / `get_models()`
- 按类型调用：`call_model_by_type()`
- 按名称调用：`call_model()`
- Agent 专用：`require(type)` 缺类型直接抛 `AgentModelMissingError`，`call_agent(type, ...)` 先校验再调用

如果目标模型不存在，或没有注册该类型的模型，将抛出 `ModelRequestError` / `NO_MODEL_ERR`。在调用失败时通常需要捕获该异常。

### 3.3 `LLMContents` / `LLMMessages`

模块提供了结构化消息内容对象，适合构造更复杂的请求：

- `LLMContents`：单一内容对象
- `LLMMessages`：多轮消息集合
- `LLMMessage`：单条消息

这样可以方便组装多角色对话、工具调用上下文、系统提示等信息。

### 3.4 Provider 与配置

模型提供商由 `LLMProvider` 和 `LLMModel` 定义：

- `LLMProvider`：描述 provider，例如 OpenAI-compatible API、Gemini 等
- `LLMModel`：描述某个具体模型实例及其参数
- `ProviderManager`：统一管理 provider 和 model 注册

一个 Provider 只对应一个 `url`。`api_key` 是一个带名称的密钥列表，模型可以按名称选择自己使用的密钥；同一个 Provider 内的密钥名称唯一，未命名时会自动生成 `key-1`、`key-2` 等名称：

```yaml
provider_name: example
provider_type: openai
url: https://api.example.com/v1
api_key:
  - name: main
    key: sk-main
  - name: backup
    key: sk-backup
models:
  - name: example
    model: example-model
    type: chat
    api_key: backup
  - name: example-all
    model: example-model
    type: chat
    api_key: all
```

Provider 的 `api_key` 列表项必须是 `name` / `key` 对象。模型上的 `api_key` 支持单个名称或名称列表：

- 留空：使用 Provider 的第一个密钥；
- 填写名称：按名称选择密钥，多个名称会按顺序调用，前一个失败自动切换；
- 填写 `all` 或 `*`：使用 Provider 下的全部密钥；
- 名称未匹配时按密钥原文处理，因此也可以直接在该字段填写模型专属密钥。

旧版 `api_key: "sk-xxx"` 仍可直接加载，会自动转换为名为 `key-1` 的密钥。

在启动或 reload 时，会从系统配置目录读取 `provider.yml`（由 `PROVIDER_CONFIG_FILE` 指定），并通过 `load_provider_from_config()` 注册所有模型。

### 3.5 模型调用日志

模型输入输出使用独立日志系统，直接写入 `data/logs/llm`：

- 每次启动新建 `llm-<启动时间>.log`，滚动保留最近 3 次启动的日志；
- 单个日志文件最大 10MB，溢出后自动轮转；
- 每次调用按文本块记录模型、事件、耗时、Token 用量、完整输入输出与异常；
- 图片/音频只记录类型与长度，不写入 base64；
- 主日志仅保留一行调用摘要，不再输出完整输入输出。

日志由 `LLMManager.call_model()` 统一写入。

### 3.6 三级权限与功能调用

权限只有三个账户等级，另有一个只由代码构造的内部伪角色 `system`：

| 等级 | 能力上限 | 能做什么 |
|---|---|---|
| `master` | `bot` | 框架全局：配置、文件、服务查询、跨群 |
| `admin` | `group` | 只限本群；**不能**用任何 `bot` 作用域功能 |
| `user` | `chat` | 正常聊天、她自己的生活/记忆/关系 |
| `system` | `bot` | 主动开口、定时任务等内部链路，不对外开放 |

默认权限由每个功能自己的 `scope` / `scope_by_action` 声明；`AgentConfig.permission` 与 `permission_users` 只记录**覆盖项**，默认表为空。能力上限是代码常量，任何 `allow` 都不能提权。

求值顺序固定、可解释：功能存在 → essential → 全局禁用 → 插件禁用 → 等级（含个人 `downgrade_to`）→ 作用域 → 能力上限 → 个人 deny → 个人 allow → 等级 deny → 等级 allow → 功能默认作用域允许。

个人 `downgrade_to` 对所有「按等级判断」的地方生效：`PermissionResolver.effective_level()` 是唯一口径，定时任务存等级、`plan(force)` 与 `tasks` 的可见范围都用它，不会只在权限求值里降级、在别处仍按原等级放行。

### 3.7 功能调用清单（新名 / 旧名别名）

| 新名 | 旧名 | 说明 |
|---|---|---|
| `say` | `send_message` | 说话（intent → Chat 渲染） |
| `say_exact` | — | 逐字发送，唯一跳过 Chat 渲染 |
| `md` | `send_md_message` | 卡片（intent → Chat 排版） |
| `md_read` | `read_md_message` | 读回自己发过的 Markdown |
| `face` | `send_face` | 表情（intent → Chat 选表情） |
| `love` | `love_change` | 改好感度 |
| `rel_set` | `relation_set` | 设置对某人的态度 |
| `rel_get` | `relation_query` | 查关系 / 名单 |
| `user` | `get_user_info` | 查用户资料 |
| `alias` | `user_alias` | 记 / 删别名 |
| `profile` | `user_profile` | 用户画像 |
| `remind` | `schedule_task` | 定时提醒 |
| `tasks` | `list_tasks` | 任务列表 |
| `untask` | `cancel_task` | 取消任务 |
| `services` | `get_service_list` | 服务列表 |
| `service` | `get_service_info` | 服务详情 |
| `command` | `get_command_info` | 命令详情 |
| `remember` | `memory_change` | 改临时长期记忆 |
| `self_note` | `self_memory` | 记自己的经历 |
| `recall` | `get_memories` | 语义检索记忆 |
| `reinforce` | `access_memory` | 强化一条记忆 |
| `skills` | `list_skills` | 完整技能清单（`prompt_hidden`） |
| `skill` | `get_skill_content` | 读技能正文（章节 / offset） |
| `tree` | `get_dir_tree` | 目录树（`file_ops` rare） |
| `read` | `read_file` | 读文件（`file_ops` rare） |
| `plan` | — | 日程 show / fill / clear |
| `wear` | — | 穿搭 show / generate / set |
| `home` | — | 家 show / edit / add / generate |
| `where` | — | 当前位置 |
| `go` | — | 立即前往 |
| `call` | — | 调用不常用功能 |

> 别名保留一个 minor 版本；提示词、技能文档、WebUI、日志与 telemetry 只用新名。
> **别名只改功能名，不改参数**：`send_message` 仍会归一到 `say`，但它原本的 `msg` 参数已不存在（改用 `intent` / `tone` / `count`），旧调用会被参数校验拒绝并回填原因。

### 3.8 聊天输出流

thinking（`atri-think`）只决定「表达什么」；所有对外发送都进入 `ChatOutbox`：

```text
thinking 产出 say(intent) / md(intent) / face(intent) / say_exact(text)
  → ActionExecutor 校验后投递 ReplyJob
  → ChatOutbox 单 worker：
       ChatModel (atri-chat) 渲染 → ReplyPipeline 清洗/分条/去重
       → sender 发送 → 写历史 → TTS
```

- `say` / `md` / `face` 必须经 `atri-chat`；唯一跳过渲染的是 `say_exact`（代码、命令、引用原文）。
- 缺 `atri-chat` 时：`say` / `md` 用清洗后的 intent 兜底，`face` 丢弃；**不回退**旧 `CHAT`/`TOOL`。
- `chat_parallel_render` 打开后 worker 会一次取多条排队任务**并发渲染**，发送仍严格按序；默认关闭。
- 发送顺序与 thinking 调用顺序一致；`drain()` 后 `sent_count`、历史与主动发言记账落定。
- 历史记录最终发出的内容，不记 intent。

### 3.9 生活数据文件

```text
res/data/agent/life/           # 随仓库发布
  routine.md                  # 固定作息（可手改，非模型生成）
  home.default.md             # 家的预设模板

data/plugins/agent/life/      # 运行时
  routine.md                  # 固定作息的用户覆盖（存在时优先于内置）
  schedule/<date>.json        # 填入层；固定作息不写入
  outfit/<date>.json          # 白天 / 晚上两套
  home.md                     # 家的内容；首次从模板复制
  home_history/               # 每次修改前的备份（最多 20 份）
```

位置不落盘：每次由「固定作息 + 填入层 + 当前时间」实时派生。旧 `ATRI_schedule.json` 用 `scripts/migrate_agent_life.py` 迁移，条目标记 `legacy`。

写入规则：

- `plan(fill)` 只替换它覆盖的时间窗口，**不传 `truncate_after` 不会清掉当天后续安排**；
- `home(edit)` 会拒绝整体替换带 `###` 子章节的父章节（如 `## 房间`），请指定子章节或用 `add`；
- `home.md` 必须保留 `## 位置`、`## 房间`，且 `## 房间` 下至少有一个 `###`，否则写入被拒。

## 4. 配置项

`ATRI.system.agent.config` 中提供了 Agent 运行参数：

```python
class AgentConfig(PluginConfig):
    model_think = Field(str, "", "思考模型（atri-think）", required=True)
    model_chat = Field(str, "", "聊天模型（atri-chat）", required=True)
    ...
    max_history = Field(int, 30, ...)
    max_thinking_times = Field(int, 20, ...)
    search = Field(
        dict,
        "搜索API配置",
        children={
            "enable": Field(bool, False, "启用联网搜索"),
            "provider": Field(str, "tavily", "搜索服务"),
            "api_key": Field(str, "", "搜索 API 密钥"),
        },
    )
```

含义：

- `model_think` / `model_chat`：Agent 必需的两类模型；其余 `atri-*` 按功能可选
- `max_history`：最大历史消息数，控制上下文窗口大小
- `max_thinking_times`：Agent 的最大思考轮数
- `max_side_effects_per_turn`：单回合副作用上限（默认 5）
- `reply_max_chars` / `reply_inter_message_delay`：单条上限与分条间隔
- `chat_parallel_render` / `max_parallel_render`：并行渲染开关与上限（默认关闭 / 2）
- `skill_prompt_budget`：技能目录 + 内嵌正文总预算（默认 10000 字）
- `outfit_night_hour` / `life_morning_plan`：晚间穿搭切换点 / 早间主动计划
- `search.enable` / `search.provider` / `search.api_key`：联网搜索能力配置

## 5. 快速开始

### 5.1 聊天模型调用

```python
from ATRI.system.agent.llm import ModelType, llm_manager

resp = await llm_manager.call_model_by_type(ModelType.CHAT, "你好!")
print(resp.content)
```

### 5.2 复杂消息构造

```python
from ATRI.system.agent.llm import LLMContents

content = LLMContents().text("你好！请用中文回复我。")
```

```python
from ATRI.system.agent.llm import LLMMessage, LLMMessages, LLMContents

messages = LLMMessages().add_message(
    LLMMessage(role="user", content=LLMContents().text("你好!"))
)
```

### 5.3 指定模型调用

```python
from ATRI.system.agent.llm import llm_manager

model = llm_manager.get_model("siliconflow/DeepSeek-V4-Flash")
resp = await llm_manager.call_model(model, "请总结一下机器学习的核心思想。")
print(resp.content)
```

## 6. 默认 Provider 说明

默认 provider 在 `ATRI.system.agent.llm.provider` 中定义，示例为 `siliconflow`，使用 OpenAI 兼容接口：

```python
default_provider = LLMProvider(
    provider_name="siliconflow",
    provider_type="openai",
    url="https://api.siliconflow.cn/v1",
    models=[
        LLMModel(
            name="siliconflow/DeepSeek-V4-Flash",
            model="deepseek-ai/DeepSeek-V4-Flash",
            temperature=0.7,
            type="chat",
        ),
    ],
)
```

`load_models_from_config()` 会读取 provider 配置并把模型注册到 `llm_manager` 中，从而实现统一调用。

## 7. Agent 运行流程

ATRI Agent 的典型生命周期大致如下：

1. 启动插件时执行 `init()`
2. `load_models_from_config()` 自动加载 provider 与 model
3. `agent.daily_task.init()` 注册日常任务与运行逻辑，并确保生活数据就绪
4. 对话链路通过 `ATRIAgent` / `ChatHistoryManager` / `MemoryManager` 获取上下文
5. thinking 通过 `ActionExecutor` 分派功能调用；数据调用可并发，副作用进 `ChatOutbox`
6. 聊天输出由 `ChatModel`（`atri-chat`）渲染后经单 worker 发送
7. 对处理结果进行返回、记忆落库或进一步调度

## 8. 记忆、历史与生活

`agent` 子模块中包含：

- `ChatHistoryManager`：管理聊天历史
- `MemoryManager`：记忆存储与总结
- `UserProfile`：用户资料与偏好
- `FaceManager`：表情/消息风格管理
- `LifeSystem`：固定作息 + 填入式日程、位置、穿搭与家（`agent/life/`）；`agent/schedule.py` 只是兼容 facade
- `ChatOutbox` / `ChatModel` / `ReplyPipeline`：聊天输出的渲染、清洗、保序发送

这让 Agent 不只是单轮生成器，而是具备一定长期交互状态的智能体。

## 9. 扩展建议

如果需要扩展 Agent 能力，可以考虑以下几个点：

- 新增 provider：在 `provider.py` 中注册新的 `LLMProvider`
- 新增模型：在 `provider.yml` 中配置 `models`
- 追加工具调用：扩展 `agent/function_calling.py`，并声明 `scope` / `returns_data` / `parallel_safe`
- 新增聊天输出类型：扩展 `brain/chat_model.py` 与 `reply_pipeline.py`
- 增加记忆策略：调整 `agent/memory/` 模块逻辑
- 增加生活能力：扩展 `agent/life/`，并在 `function_calling.py` 暴露新函数

## 10. 常见异常

- `AgentModelMissingError`：必需的 `atri-*` 类型没有注册
- `ModelRequestError`：模型调用失败，通常是网络、鉴权或 API 返回异常
- `ValueError`：模型未注册或错误参数
- `NO_MODEL_ERR`：没有可用模型
- `FunctionCallingUnavailableError`：功能存在但当前不可用（权限 / 开关 / 环境）

当使用 `llm_manager.call_model_by_type()` / `call_agent()` 时，建议在业务层进行异常捕获与重试。

## 11. 说明

该模块适合在 ATRI-LK 中作为系统级 Agent 能力底座使用。它的设计目标是：

- 统一抽象不同厂商的 LLM 接口
- 兼容多种模型类型和调用场景
- 简化 Agent 的消息、记忆和工具调用开发过程
- 让业务层更容易使用可靠的智能体能力

如果你正在开发 ATRI-LK 的聊天机器人、自动任务处理或多模型协作流程，这个模块是核心入口之一。
