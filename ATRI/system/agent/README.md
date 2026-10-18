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
│   ├── history.py       # 历史记录管理
│   ├── memory/          # 记忆系统
│   ├── schedule.py      # 任务调度
│   ├── sender.py        # 消息发送器
│   └── ...
├── brain/               # 多模型/多脑协作相关实现
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

模型类型由 `ModelType` 枚举定义，当前包含：

- `CHAT`：通用聊天模型
- `IMAGE`：图像处理模型
- `TOOL`：工具调用/函数调用模型
- `EMBEDDING`：向量嵌入模型
- `TTS`：语音合成模型
- `SYSTEMONE`：SystemOne 相关模型

### 3.2 `LLMManager`

`LLMManager` 是统一的模型调度器，负责：

- 注册模型：`register_model()`
- 获取模型：`get_model()` / `get_models()`
- 按类型调用：`call_model_by_type()`
- 按名称调用：`call_model()`

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

在启动或 reload 时，会从系统配置目录读取 `provider.yml`（由 `PROVIDER_CONFIG_FILE` 指定），并通过 `load_provider_from_config()` 注册所有模型。

### 3.5 模型调用日志

模型输入输出使用独立日志系统，写入 `data/logs/llm`：

- 每次启动创建一轮运行目录，滚动保留最近 3 轮；
- 单个日志文件最大 10MB，溢出后自动轮转；
- 每次调用按文本块记录模型、事件、耗时、Token 用量、完整输入输出与异常；
- 图片/音频只记录类型与长度，不写入 base64；
- 主日志仅保留一行调用摘要，不再输出完整输入输出。

日志由 `LLMManager.call_model()` 统一写入。

## 4. 配置项

`ATRI.system.agent.config` 中提供了 Agent 运行参数：

```python
class AgentConfig(PluginConfig):
    max_history = Field(int, 20, ...)
    max_thinking_times = Field(int, 15, ...)
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

- `max_history`：最大历史消息数，控制上下文窗口大小
- `max_thinking_times`：Agent 的最大思考轮数
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
3. `agent.daily_task.init()` 注册日常任务与运行逻辑
4. 对话链路通过 `ATRIAgent` / `ChatHistoryManager` / `MemoryManager` 获取上下文
5. 工具调用或多模型推理由 `FunctionCallingManager` 与相关模块完成
6. 对处理结果进行返回、记忆落库或进一步调度

## 8. 记忆与历史

`agent` 子模块中包含：

- `ChatHistoryManager`：管理聊天历史
- `MemoryManager`：记忆存储与总结
- `UserProfile`：用户资料与偏好
- `FaceManager`：表情/消息风格管理
- `ATRISchedule`：计划与日程调度

这让 Agent 不只是单轮生成器，而是具备一定长期交互状态的智能体。

## 9. 扩展建议

如果需要扩展 Agent 能力，可以考虑以下几个点：

- 新增 provider：在 `provider.py` 中注册新的 `LLMProvider`
- 新增模型：在 `provider.yml` 中配置 `models`
- 追加工具调用：扩展 `agent/function_calling.py`
- 增加记忆策略：调整 `agent/memory/` 模块逻辑
- 增加会话/调度器：在 `agent/schedule.py` 或 `chat_chain.py` 中接入

## 10. 常见异常

- `ModelRequestError`：模型调用失败，通常是网络、鉴权或 API 返回异常
- `ValueError`：模型未注册或错误参数
- `NO_MODEL_ERR`：没有可用模型

当使用 `llm_manager.call_model_by_type()` 或 `call_model()` 时，建议在业务层进行异常捕获与重试。

## 11. 说明

该模块适合在 ATRI-LK 中作为系统级 Agent 能力底座使用。它的设计目标是：

- 统一抽象不同厂商的 LLM 接口
- 兼容多种模型类型和调用场景
- 简化 Agent 的消息、记忆和工具调用开发过程
- 让业务层更容易使用可靠的智能体能力

如果你正在开发 ATRI-LK 的聊天机器人、自动任务处理或多模型协作流程，这个模块是核心入口之一。
