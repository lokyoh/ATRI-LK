import time
from typing import ClassVar

import anyio
from pydantic import BaseModel

from ATRI.exceptions import str_traceback
from ATRI.message import QQMessage, img_msg, rec_msg_from_path
from ATRI.system.htmlrender import md_to_pic
from ATRI.utils.datetime import now

from ..config import config
from ..event import AgentEventBus
from ..llm import ModelType, llm_manager
from ..utils import log, request
from .history import History
from .memes import FaceManager
from .memory.manage import memory_manager
from .sender import ChatSender, OneBotChatSender, QQChatSender
from .state import feel
from .telemetry import telemetry
from .user import add_self_memory, get_user_info, save_user_info
from .user_profile import UserProfile

#: 单个对话人「临时长期记忆」的条数上限（`memory_change` 写入时强制，先进先出）。
#: 这份列表每轮整段进提示词，所以上限是成本约束，不只是整洁问题。
USER_MEMORY_LIMIT = 11

#: 亚托莉自我记忆的条数上限。
SELF_MEMORY_LIMIT = 8


class FunctionCallingArg:
    def __init__(self, name: str, _type: str, description: str):
        self.name = name
        self.type = _type
        self.description = description


class FunctionCallingData:
    def __init__(
        self,
        sender_id: str,
        chat_id: str,
        sender: ChatSender,
        data: dict,
        current_history: History | None = None,
    ):
        self.sender_id = sender_id
        self.chat_id = chat_id
        self.sender = sender
        self.data = data
        self.current_history = current_history


class EnableCheck(BaseModel):
    """技能/功能的环境可用性判定结果"""

    available: bool = True
    reason: str = ""
    """不可用原因，供 UI 灰显 tooltip 与排障使用"""


class FunctionCalling:
    function_name = ""
    description = ""
    args: ClassVar[list[FunctionCallingArg]] = []
    essential: ClassVar[bool] = False
    """核心功能：始终启用，不受手动开关影响（见 FunctionCallingManager）"""

    @classmethod
    def can_enable(cls) -> EnableCheck:
        """① 当前环境能否开启该功能（与对话人无关）。"""
        return EnableCheck()

    @classmethod
    def is_available_for(cls, sender: ChatSender | None, user_id: str) -> bool:
        """② 对当前对话人是否可用。sender 可能为 None（后台/测试调用），必须容忍。"""
        return True

    @staticmethod
    async def call(data: FunctionCallingData):
        pass


class FunctionCallingUnavailableError(Exception):
    """FC 存在但当前不可用（手动关闭 / 环境不支持 / 当前对话人不可用）"""


class FunctionCallingEssentialError(Exception):
    """核心功能不可关闭（stop_thinking / send_message 等）"""


def _plugin_enabled(function_name: str) -> bool:
    """功能所属插件的整体开关；不属于任何插件时恒为 True。"""
    from .function_plugin.manage import function_plugin_manager

    return function_plugin_manager.is_enabled(
        function_plugin_manager.owner_of_function(function_name)
    )


def _plugin_rare(function_name: str) -> bool:
    """该功能是否被功能插件声明为不常用（只能经 call 调用）。"""
    from .function_plugin.manage import function_plugin_manager

    return function_plugin_manager.is_rare_function(function_name)


class FunctionCallingManager:
    function_calling: ClassVar[dict[str, type[FunctionCalling]]] = {}

    @classmethod
    def register(cls, func: type[FunctionCalling]):
        cls.function_calling[func.function_name] = func
        if getattr(func, "essential", False):
            cls.purge_essential_disables()

    @classmethod
    def clear_all(cls):
        cls.function_calling.clear()

    # ---- 手动开关（存 AgentConfig 的禁用列表：默认启用，列表只记例外）----

    @classmethod
    def is_essential(cls, name: str) -> bool:
        """核心功能：维持对话闭环所必需，永远启用，不能被手动关闭。"""
        func = cls.function_calling.get(name)
        return bool(getattr(func, "essential", False))

    @classmethod
    def is_rare(cls, name: str) -> bool:
        """不常用功能：不进入常规功能提示词，只能由 call 调用。"""
        return _plugin_rare(name)

    @classmethod
    def is_manually_enabled(cls, name: str) -> bool:
        if cls.is_essential(name):
            return True
        return name not in (config.get("function_calling_disable") or [])

    @classmethod
    def set_manually_enabled(cls, name: str, enabled: bool):
        if not enabled and cls.is_essential(name):
            raise FunctionCallingEssentialError(f"功能 {name} 是核心功能，不能关闭")
        # config.get() 返回内部引用，必须构造新列表后 config.set 才会落盘
        names = list(config.get("function_calling_disable") or [])
        if enabled:
            if name in names:
                names.remove(name)
        elif name not in names:
            names.append(name)
        config.set("function_calling_disable", names)

    @classmethod
    def purge_essential_disables(cls) -> list[str]:
        """把核心功能名从禁用列表中剔除（手动改配置 / 旧版本残留都会在这里被纠正）。

        返回被修复后的列表；没有变化时不写盘。
        """
        names = list(config.get("function_calling_disable") or [])
        kept = [name for name in names if not cls.is_essential(name)]
        if kept != names:
            config.set("function_calling_disable", kept)
        return kept

    # ---- 可用性（手动开关 ∧ 环境 ∧ 对话人）----

    @classmethod
    def get_can_enable(cls, name: str) -> EnableCheck:
        func = cls.function_calling.get(name)
        if func is None:
            return EnableCheck(available=False, reason="功能未注册")
        try:
            return func.can_enable()
        except Exception as e:
            log.warning(f"功能 {name} 环境可用性检查失败:{str_traceback(e)}")
            return EnableCheck(available=False, reason="环境可用性检查失败")

    @classmethod
    def is_available(
        cls, name: str, sender: ChatSender | None = None, user_id: str = ""
    ) -> bool:
        func = cls.function_calling.get(name)
        if func is None:
            return False
        if not cls.is_manually_enabled(name):
            return False
        if not _plugin_enabled(name):
            return False
        if not cls.get_can_enable(name).available:
            return False
        try:
            return bool(func.is_available_for(sender, user_id))
        except Exception as e:
            log.warning(f"功能 {name} 对话人可用性检查失败:{str_traceback(e)}")
            return False

    @classmethod
    def get_available(
        cls, sender: ChatSender | None = None, user_id: str = ""
    ) -> dict[str, type[FunctionCalling]]:
        """保持注册顺序（= 提示词顺序）的可用功能子集"""
        return {
            name: func
            for name, func in cls.function_calling.items()
            if not _plugin_rare(name) and cls.is_available(name, sender, user_id)
        }

    @classmethod
    def get_rare_available(
        cls, sender: ChatSender | None = None, user_id: str = ""
    ) -> dict[str, type[FunctionCalling]]:
        """可由 call 调用的不常用功能子集（不进常规提示词）。"""
        return {
            name: func
            for name, func in cls.function_calling.items()
            if name != "call"
            and _plugin_rare(name)
            and cls.is_available(name, sender, user_id)
        }

    @classmethod
    async def call(cls, func_name: str, data: FunctionCallingData):
        if func_name not in cls.function_calling:
            raise KeyError(func_name)
        if not cls.is_available(func_name, data.sender, data.sender_id):
            raise FunctionCallingUnavailableError(f"功能 {func_name} 当前不可用")
        return await cls.function_calling[func_name].call(data)


# 生成表情的例子字符串
meme_examples = []
for meme in FaceManager.get_all_memes():
    desc = FaceManager.get_meme_description(meme)
    if desc:
        # 只取描述的前一部分，避免过长
        short_desc = desc.split("（")[0] if "（" in desc else desc[:20]
        meme_examples.append(f"{meme}:{short_desc}")
meme_example_str = ";".join(meme_examples)


def register_function_calling():
    FunctionCallingManager.clear_all()

    class StopThinkingFunctionCalling(FunctionCalling):
        function_name = "stop_thinking"
        description = "在不需要接收调用反馈时调用,调用后结束本次思考"
        essential = True
        args: ClassVar[list[FunctionCallingArg]] = []

        @staticmethod
        async def call(data: FunctionCallingData):
            from ..brain.thinking import StopThinkingError

            raise StopThinkingError("结束思考")

    FunctionCallingManager.register(StopThinkingFunctionCalling)

    class SendMessageFunctionCalling(FunctionCalling):
        function_name = "send_message"
        description = "发送消息,消息符合及时聊天软件的简短聊天风格"
        essential = True
        args: ClassVar = [
            FunctionCallingArg(name="msg", _type="str", description="要发送的消息内容")
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            msg = data.data.get("msg", "")
            current_history = data.current_history
            if current_history is None:
                raise RuntimeError("发送消息时缺少关联的聊天历史")
            try:
                await data.sender.send(msg)
                if llm_manager.has_type(ModelType.TTS) and isinstance(
                    data.sender, OneBotChatSender
                ):
                    file = await llm_manager.call_model_by_type(
                        ModelType.TTS, msg, "Agent-语音"
                    )
                    await data.sender.send(rec_msg_from_path(file.content))
            except Exception:
                return "发送失败,请勿再次尝试"
            current_history.add_response(msg)
            return "发送成功"

    FunctionCallingManager.register(SendMessageFunctionCalling)

    class CallFunctionCalling(FunctionCalling):
        function_name = "call"
        description = (
            "调用不常用功能。不常用功能不会出现在上面的功能列表里，"
            "它们的具体用法通常写在技能正文中，请按技能给出的功能名与参数调用"
        )
        args: ClassVar[list[FunctionCallingArg]] = [
            FunctionCallingArg(
                name="function", _type="str", description="要调用的功能名"
            ),
            FunctionCallingArg(
                name="args",
                _type="dict",
                description='目标功能的参数，写成 {"参数名": 值} 的字典，无参数时省略',
            ),
        ]

        @classmethod
        def can_enable(cls) -> EnableCheck:
            if not FunctionCallingManager.get_rare_available():
                return EnableCheck(available=False, reason="没有可用的不常用功能")
            return EnableCheck()

        @classmethod
        def describe_rare(cls) -> str:
            """当前会话可调用的不常用功能清单，附在 call 的说明后面。"""
            rare = FunctionCallingManager.get_rare_available()
            if not rare:
                return ""
            from .function_plugin.manage import function_plugin_manager

            lines: list[str] = []
            for name, func in rare.items():
                owner = function_plugin_manager.owner_of_function(name)
                label = f"（属于功能插件 {owner}）" if owner else ""
                lines.append(f"- {name}{label}: {func.description}")
            return "\n可调用的不常用功能:\n" + "\n".join(lines)

        @staticmethod
        async def call(data: FunctionCallingData):
            from ..brain.action_parser import normalize_args

            func_name = str(data.data.get("function") or "").strip()
            if not func_name:
                return "请提供要调用的功能名"
            if func_name == "call":
                return "call 不能调用自己"
            required = data.data.get("args")
            if required is None:
                required = {}
            if not isinstance(required, dict):
                return 'args 必须是 {"参数名": 值} 形式的字典'
            func_cls = FunctionCallingManager.function_calling.get(func_name)
            if func_cls is None:
                known = "、".join(FunctionCallingManager.get_rare_available()) or "无"
                return f"未知的功能:{func_name}，当前可调用的不常用功能:{known}"
            if not FunctionCallingManager.is_rare(func_name):
                return f"功能 {func_name} 不是不常用功能，请直接调用它"
            if not FunctionCallingManager.is_available(
                func_name, data.sender, data.sender_id
            ):
                check = FunctionCallingManager.get_can_enable(func_name)
                return (
                    f"功能 {func_name} 当前不可用：{check.reason or '该功能已被关闭'}"
                )
            args, issues = normalize_args(func_cls, required)
            if issues:
                return f"功能 {func_name} 的参数不合规：{'；'.join(issues)}"
            return await FunctionCallingManager.call(
                func_name,
                FunctionCallingData(
                    data.sender_id,
                    data.chat_id,
                    data.sender,
                    args,
                    data.current_history,
                ),
            )

    FunctionCallingManager.register(CallFunctionCalling)

    class SendMdMessageFunctionCalling(FunctionCalling):
        function_name = "send_md_message"
        description = "发送md格式消息,用于专业性回答"
        args: ClassVar = [
            FunctionCallingArg(name="msg", _type="str", description="要发送的消息内容")
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            msg = data.data.get("msg", "")
            current_history = data.current_history
            if current_history is None:
                raise RuntimeError("发送消息时缺少关联的聊天历史")
            try:
                from ..service import plugin

                md_file = f"{time.time()}.md"
                md_path = (
                    plugin.get_path() / "chat" / str(data.chat_id) / "md" / md_file
                )
                md_path.parent.mkdir(parents=True, exist_ok=True)
                async with await anyio.open_file(md_path, "w", encoding="utf-8") as f:
                    await f.write(msg)
                if isinstance(data.sender, OneBotChatSender):
                    await data.sender.send(img_msg(await md_to_pic(msg)))
                elif isinstance(data.sender, QQChatSender):
                    await data.sender.send(QQMessage.md_msg(msg))
                else:
                    await data.sender.send(msg)
            except Exception as e:
                log.warning(f"MD发送发生错误:{str_traceback(e)}")
                return "发送失败,请勿再次尝试"
            current_history.add_response(f"[md:{md_file}]")
            return "发送成功"

    FunctionCallingManager.register(SendMdMessageFunctionCalling)

    class ReadMdMessageFunctionCalling(FunctionCalling):
        function_name = "read_md_message"
        description = "获取你发送的md格式消息"
        args: ClassVar = [
            FunctionCallingArg(name="file", _type="str", description="文件名")
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            md_file = data.data.get("file", "")
            try:
                from ..service import plugin

                md_path = (
                    plugin.get_path() / "chat" / str(data.chat_id) / "md" / md_file
                )
                async with await anyio.open_file(md_path, "r", encoding="utf-8") as f:
                    content = await f.read()
                return content
            except Exception:
                return "获取失败"

    FunctionCallingManager.register(ReadMdMessageFunctionCalling)

    class LoveChangeFunctionCalling(FunctionCalling):
        function_name = "love_change"
        description = (
            "更改你对用户的好感度（长期关系评价）。"
            "注意：好感度**不会**改变你当下的心情——心情用 feel 记录。"
        )
        args: ClassVar = [
            FunctionCallingArg(
                name="num", _type="int", description="与对话人好感度增减数值"
            )
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            # 处理好感度变化
            num = data.data.get("num", 0)
            if num:
                user_info = get_user_info(data.sender_id)
                user_info.love = user_info.love + num
                save_user_info(data.sender_id, user_info)
                return (
                    f"用户 {data.sender_id} 好感度变化：{num}, 当前值：{user_info.love}"
                )

    FunctionCallingManager.register(LoveChangeFunctionCalling)

    class FeelFunctionCalling(FunctionCalling):
        function_name = "feel"
        description = (
            "表达你此刻的情绪，会持续影响接下来一段时间的语气。"
            "与 love_change 不同：这个只关当下感受，不改关系评价。"
            "如被冒犯、被感动、被逗笑、觉得无聊。"
        )
        args: ClassVar = [
            FunctionCallingArg(
                name="mood",
                _type="float",
                description="心情变化，-1.0 到 1.0，负数变差、正数变好；不需要就省略",
            ),
            FunctionCallingArg(
                name="irritation",
                _type="float",
                description="烦躁增加量，0.0 到 1.0；被冒犯/被反复打扰时用，不需要就省略",
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            mood = data.data.get("mood")
            irritation = data.data.get("irritation")
            if mood is None and irritation is None:
                return "没有变化"
            try:
                state = feel(
                    mood=float(mood or 0.0),
                    irritation=float(irritation or 0.0),
                )
            except (TypeError, ValueError):
                return "情绪参数无效"
            log.debug(
                f"情绪变化 mood={state.mood:.2f} irritation={state.irritation:.2f}"
            )
            telemetry.feel(
                mood=state.mood, irritation=state.irritation, source="feel_tool"
            )
            return f"你现在的感受：{state.describe() or '很平静'}"

    FunctionCallingManager.register(FeelFunctionCalling)

    class SendFaceFunctionCalling(FunctionCalling):
        function_name = "send_face"
        description = "仅在表达自己的强烈情绪时发送表情"
        args: ClassVar = [
            FunctionCallingArg(
                name="meme", _type="str", description=f"表情词,定义:{meme_example_str}"
            )
        ]

        @classmethod
        def is_available_for(cls, sender: ChatSender | None, user_id: str) -> bool:
            return isinstance(sender, OneBotChatSender)

        @staticmethod
        async def call(data: FunctionCallingData):
            # 兜底判断：防止绕过 FCM 直接调用
            if isinstance(data.sender, OneBotChatSender):
                meme = data.data.get("meme", "")
                try:
                    face = FaceManager.get_face(meme)
                    if not face:
                        return "表情不存在"
                    await data.sender.send(face)
                except Exception:
                    return "发送失败,请勿再次尝试"
                return "发送成功"
            else:
                return "发送失败,不支持发送表情"

    FunctionCallingManager.register(SendFaceFunctionCalling)

    class UserProfileGetFunctionCalling(FunctionCalling):
        function_name = "user_profile"
        description = "获取或修改指定用户的用户画像。用户画像是用于存储用户的个性化信息和偏好设置的文本数据，它可以包含用户的兴趣、行为模式、偏好设置等信息，用于在与用户交互时提供更个性化的体验。同时也可以用于存储你对用户的看法和评价，可以多次调用"
        args: ClassVar = [
            FunctionCallingArg(
                name="opt",
                _type="str",
                description="操作类型，'get'表示获取，'change'表示修改",
            ),
            FunctionCallingArg(name="user_id", _type="str", description="目标用户ID"),
            FunctionCallingArg(
                name="profile_change",
                _type="str",
                description="如何更改用户画像，除自己以外的人物全都使用用户ID，仅在change操作时需要",
            ),
        ]

        @classmethod
        def can_enable(cls) -> EnableCheck:
            if not llm_manager.has_type(ModelType.TOOL):
                return EnableCheck(available=False, reason="未配置 TOOL 类型模型")
            return EnableCheck()

        @staticmethod
        async def call(data: FunctionCallingData):
            user_id = data.data.get("user_id")
            opt = data.data.get("opt")
            profile_change = data.data.get("profile_change")
            if opt == "get":
                return UserProfile.get_profile(user_id)
            elif opt == "change":
                log.info(f"用户 {user_id} 更改画像：{profile_change}")
                now_user_profile = await UserProfile.change_profile(
                    user_id, profile_change
                )
                return f"用户 {user_id} 画像更改成功: {now_user_profile}"

    FunctionCallingManager.register(UserProfileGetFunctionCalling)

    class GetServiceListFunctionCalling(FunctionCalling):
        function_name = "get_service_list"
        description = (
            "查询当前可用服务列表及服务分类。需要了解自己支持哪些功能服务时调用。"
        )
        args: ClassVar = []

        @staticmethod
        async def call(data: FunctionCallingData):
            from ATRI.system.help.data_source import Helper

            return Helper.get_text_list()

    FunctionCallingManager.register(GetServiceListFunctionCalling)

    class GetServiceInfoFunctionCalling(FunctionCalling):
        function_name = "get_service_info"
        description = "查询指定服务的说明、可用命令和启用状态。"
        args: ClassVar = [
            FunctionCallingArg(
                name="service", _type="str", description="要查询的服务名"
            )
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            from ATRI.system.help.data_source import Helper

            service = data.data.get("service", "")
            if not service:
                return "请提供服务名。"
            return Helper.service_info(service)

    FunctionCallingManager.register(GetServiceInfoFunctionCalling)

    class GetCommandInfoFunctionCalling(FunctionCalling):
        function_name = "get_command_info"
        description = "查询指定服务中某条命令的类型、说明和别名。"
        args: ClassVar = [
            FunctionCallingArg(
                name="service", _type="str", description="命令所属的服务名"
            ),
            FunctionCallingArg(
                name="command", _type="str", description="要查询的命令名"
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            from ATRI.system.help.data_source import Helper

            service = data.data.get("service", "")
            command = data.data.get("command", "")
            if not service or not command:
                return "请提供服务名和命令名。"
            return Helper.cmd_info(service, command)

    FunctionCallingManager.register(GetCommandInfoFunctionCalling)

    class SearchFunctionCalling(FunctionCalling):
        function_name = "search"
        description = "搜索网络上的信息，并到一个总结性回答"
        args: ClassVar = [
            FunctionCallingArg(name="query", _type="str", description="搜索关键词")
        ]

        @classmethod
        def can_enable(cls) -> EnableCheck:
            if not config.get("search.enable"):
                return EnableCheck(available=False, reason="未启用联网搜索")
            if not config.get("search.api_key"):
                return EnableCheck(available=False, reason="未配置搜索 API 密钥")
            return EnableCheck()

        @staticmethod
        async def call(data: FunctionCallingData):
            """
            搜索网络信息
            支持多种搜索引擎和网站
            """
            query = data.data.get("query", "")
            if not query:
                return None
            try:
                # 构建搜索 URL
                url = "https://api.tavily.com/search"
                log.debug(f"搜索：{url}")
                # 发送请求
                response = await request.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {config.get('search.api_key')}",
                    },
                    json={
                        "query": query,
                        "include_answer": "advanced",
                        "search_depth": "basic",
                        "chunks_per_source": 1,
                        "country": "china",
                    },
                )
                if response.status_code == 200:
                    resp = response.json()
                    log.info(f"{query} 搜索结果：\n{resp['answer']}")
                    return resp["answer"]
                else:
                    error_msg = f"搜索失败，状态码：{response.status_code}"
                    log.warning(error_msg)
                    return error_msg
            except Exception as e:
                error_msg = f"搜索出错：{e}"
                log.error(error_msg)
                return error_msg

    FunctionCallingManager.register(SearchFunctionCalling)

    class MemoryChangeFunctionCalling(FunctionCalling):
        function_name = "memory_change"
        description = (
            f"更改你对用户的临时长期记忆，该记忆存在数量上限{USER_MEMORY_LIMIT}"
        )
        args: ClassVar = [
            FunctionCallingArg(
                name="mem",
                _type="str",
                description="新增记忆临时长期记忆,超出上限则先入先出,不要添加时间戳,直接输出记忆内容,可选",
            ),
            FunctionCallingArg(
                name="del",
                _type="list[int]",
                description="需要删除的记忆列表索引,请积极删除无用与过期的记忆,可选",
            ),
        ]

        @classmethod
        def can_enable(cls) -> EnableCheck:
            if not llm_manager.has_type(ModelType.EMBEDDING):
                return EnableCheck(available=False, reason="未配置 EMBEDDING 类型模型")
            return EnableCheck()

        @staticmethod
        async def call(data: FunctionCallingData):
            # 处理记忆变更
            mem = data.data.get("mem")
            del_index = data.data.get("del")
            # 新增记忆
            user_info = get_user_info(data.sender_id)
            msg = []
            if mem:
                user_info.memory.append(
                    f"{now().strftime('%Y年%m月%d日%a-%H时%M分')} {mem}"
                )
                overflow = len(user_info.memory) - USER_MEMORY_LIMIT
                if overflow > 0:
                    del user_info.memory[:overflow]
                    msg.append(f"新增记忆成功（超出上限，已遗忘最早 {overflow} 条）")
                else:
                    msg.append("新增记忆成功")
                log.debug(
                    f"用户 {data.sender_id} 新增记忆：{now().strftime('%Y年%m月%d日%a-%H时%M分')} {mem}"
                )
            if del_index:
                if type(del_index) is int:
                    del_index = [del_index]
                del_index.sort(reverse=True)
                for index in del_index:
                    try:
                        index: int
                        user_info.memory.pop(index)
                        log.debug(f"用户 {data.sender_id} 删除记忆：{index}")
                        msg.append(f"删除记忆：{index}")
                    except IndexError:
                        log.warning(f"用户 {data.sender_id} 删除记忆失败：{index}")
                        msg.append(f"删除记忆失败：{index}")
            save_user_info(data.sender_id, user_info)
            return msg

    FunctionCallingManager.register(MemoryChangeFunctionCalling)

    class SelfMemoryFunctionCalling(FunctionCalling):
        function_name = "self_memory"
        description = (
            "记下你自己（亚托莉）的经历与感受，不是关于用户的资料。"
            f"上限 {SELF_MEMORY_LIMIT} 条，超出先入先出。"
            "用于保持你作为独立个体的连续性：今天发生了什么、你对谁有什么感觉、"
            "你答应了什么。只在确有值得留下的经历时调用，日常寒暄不要记。"
        )
        args: ClassVar = [
            FunctionCallingArg(
                name="mem",
                _type="str",
                description=(
                    "你自己的经历或感受，用第一人称简短短语，"
                    "如「今天被说成机器人，有点不服气」。不要写时间戳"
                ),
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            mem = data.data.get("mem")
            if not mem:
                return "没有内容，未记录"
            ok = add_self_memory(str(mem))
            if not ok:
                return "记录失败"
            log.debug(f"亚托莉自我记忆：{mem}")
            return f"已记下（共上限 {SELF_MEMORY_LIMIT} 条）"

    FunctionCallingManager.register(SelfMemoryFunctionCalling)

    class GetMemoriesFunctionCalling(FunctionCalling):
        function_name = "get_memories"
        description = "根据语义检索长期记忆。仅在需要回忆相关历史事实时使用，由你自行决定查询数量和最低相似度。"
        args: ClassVar = [
            FunctionCallingArg(
                name="query", _type="str", description="要回忆的主题或事实"
            ),
            FunctionCallingArg(
                name="top_k",
                _type="int",
                description="返回记忆数量，建议 1 到 5，由你根据需要决定",
            ),
            FunctionCallingArg(
                name="min_similarity",
                _type="float",
                description="最低相似度，范围 0 到 1，由你根据需要决定",
            ),
        ]

        @classmethod
        def can_enable(cls) -> EnableCheck:
            if not llm_manager.has_type(ModelType.EMBEDDING):
                return EnableCheck(available=False, reason="未配置 EMBEDDING 类型模型")
            return EnableCheck()

        @staticmethod
        async def call(data: FunctionCallingData):
            query = data.data.get("query", "")
            top_k = data.data.get("top_k", 5)
            min_similarity = data.data.get("min_similarity")
            if not query:
                return []
            return await memory_manager.get_memories(
                query,
                int(top_k),
                min_similarity,
                chat_id=data.chat_id,
                subject=data.sender_id,
            )

    FunctionCallingManager.register(GetMemoriesFunctionCalling)

    class AccessMemoryFunctionCalling(FunctionCalling):
        function_name = "access_memory"
        description = "采用并强化一条已经检索到的长期记忆。只能采用检索结果中的记忆，并自行决定强化值。"
        args: ClassVar = [
            FunctionCallingArg(
                name="memory_id", _type="int", description="检索结果中的记忆 ID"
            ),
            FunctionCallingArg(
                name="reinforce",
                _type="float",
                description="重要度强化值，建议 0 到 0.2，由你根据本次使用价值决定",
            ),
        ]

        @classmethod
        def can_enable(cls) -> EnableCheck:
            if not llm_manager.has_type(ModelType.EMBEDDING):
                return EnableCheck(available=False, reason="未配置 EMBEDDING 类型模型")
            return EnableCheck()

        @staticmethod
        async def call(data: FunctionCallingData):
            from .memory.manage import memory_manager

            memory_id = data.data.get("memory_id")
            reinforce = data.data.get("reinforce", 0.1)
            if memory_id is None:
                return None
            return await memory_manager.access_memory(
                {"id": int(memory_id)}, float(reinforce)
            )

    FunctionCallingManager.register(AccessMemoryFunctionCalling)

    class ListSkillsFunctionCalling(FunctionCalling):
        function_name = "list_skills"
        description = "列出你当前可查阅的技能清单。技能是详细的玩法与操作说明文档，当用户询问某个功能怎么用、某个玩法有哪些指令时，先调用本功能查看有哪些技能，再调用 get_skill_content 获取正文。"
        args: ClassVar[list[FunctionCallingArg]] = []

        @staticmethod
        async def call(data: FunctionCallingData):
            from .skills import skill_manager

            skills = skill_manager.list_queryable()
            if not skills:
                return "当前没有可查询的技能"
            return "\n".join(
                f"{s.name}: [{s.source_label}] {s.description or '无说明'}"
                for s in skills
            )

    FunctionCallingManager.register(ListSkillsFunctionCalling)

    class GetSkillContentFunctionCalling(FunctionCalling):
        function_name = "get_skill_content"
        description = (
            "获取指定技能的完整正文（Markdown）。技能名必须来自 list_skills 的结果。"
        )
        args: ClassVar[list[FunctionCallingArg]] = [
            FunctionCallingArg(
                name="name", _type="str", description="技能名，来自 list_skills 的结果"
            )
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            from .skills import skill_manager

            name = str(data.data.get("name", "")).strip()
            if not name:
                return "请提供技能名"
            skill = skill_manager.get(name)
            if skill is None:
                return f"技能 {name} 不存在，请调用 list_skills 查看可用技能"
            if not skill.enabled:
                return f"技能 {name} 已被停用，无法查看"
            if skill.embedded:
                return f"技能 {name} 已直接写入你的提示词，无需查询"
            return f"# {skill.name}\n{skill.description}\n\n{skill.content}"

    FunctionCallingManager.register(GetSkillContentFunctionCalling)

    from .function_plugin.builtin import register_builtin_function_plugins

    register_builtin_function_plugins()
    _log_function_plugin_problems()

    AgentEventBus.publish_sync(event_type="register_FC", source="agent")


def _log_function_plugin_problems():
    """校验功能插件声明的成员是否真的存在（只记日志，不阻断加载）。"""
    from .function_plugin.manage import function_plugin_manager
    from .skills import skill_manager

    try:
        function_plugin_manager.log_member_problems(
            set(FunctionCallingManager.function_calling),
            {skill.name for skill in skill_manager.list_all()},
        )
    except Exception as e:
        log.warning(f"功能插件成员校验失败:{str_traceback(e)}")
