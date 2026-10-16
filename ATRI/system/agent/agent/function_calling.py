import time
from pathlib import Path
from typing import ClassVar

import anyio

from ATRI.exceptions import str_traceback
from ATRI.message import QQMessage, img_msg, rec_msg_from_path
from ATRI.system.htmlrender import md_to_pic
from ATRI.utils.datetime import now

from ..config import config
from ..event import AgentEventBus
from ..llm import ModelType, llm_manager
from ..utils import log, request
from .file.reader import read_file
from .file.tree_string import default_ignore_dirs, get_tree_string
from .history import History
from .memes import FaceManager
from .memory.manage import memory_manager
from .sender import ChatSender, OneBotChatSender, QQChatSender
from .user import get_user_info, save_user_info
from .user_profile import UserProfile


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


class FunctionCalling:
    function_name = ""
    description = ""
    args: ClassVar[list[FunctionCallingArg]] = []

    @staticmethod
    async def call(data: FunctionCallingData):
        pass


class FunctionCallingManager:
    function_calling: ClassVar[dict[str, type[FunctionCalling]]] = {}

    @classmethod
    def register(cls, func: type[FunctionCalling]):
        cls.function_calling[func.function_name] = func

    @classmethod
    async def call(cls, func_name: str, data: FunctionCallingData):
        return await cls.function_calling[func_name].call(data)

    @classmethod
    def clear_all(cls):
        cls.function_calling.clear()


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
        args: ClassVar[list[FunctionCallingArg]] = []

        @staticmethod
        async def call(data: FunctionCallingData):
            from ..brain.thinking import StopThinkingError

            raise StopThinkingError("结束思考")

    FunctionCallingManager.register(StopThinkingFunctionCalling)

    class SendMessageFunctionCalling(FunctionCalling):
        function_name = "send_message"
        description = "发送消息,消息符合及时聊天软件的简短聊天风格"
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
        description = "更改你对用户的好感度"
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

    class SendFaceFunctionCalling(FunctionCalling):
        function_name = "send_face"
        description = "仅在表达自己的强烈情绪时发送表情"
        args: ClassVar = [
            FunctionCallingArg(
                name="meme", _type="str", description=f"表情词,定义:{meme_example_str}"
            )
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
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

    if llm_manager.has_type(ModelType.TOOL):

        class UserProfileGetFunctionCalling(FunctionCalling):
            function_name = "user_profile"
            description = "获取或修改指定用户的用户画像。用户画像是用于存储用户的个性化信息和偏好设置的文本数据，它可以包含用户的兴趣、行为模式、偏好设置等信息，用于在与用户交互时提供更个性化的体验。同时也可以用于存储你对用户的看法和评价，可以多次调用"
            args: ClassVar = [
                FunctionCallingArg(
                    name="opt",
                    _type="str",
                    description="操作类型，'get'表示获取，'change'表示修改",
                ),
                FunctionCallingArg(
                    name="user_id", _type="str", description="目标用户ID"
                ),
                FunctionCallingArg(
                    name="profile_change",
                    _type="str",
                    description="如何更改用户画像，除自己以外的人物全都使用用户ID，仅在change操作时需要",
                ),
            ]

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

    if config.get("search.enable"):

        class SearchFunctionCalling(FunctionCalling):
            function_name = "search"
            description = "搜索网络上的信息，并到一个总结性回答"
            args: ClassVar = [
                FunctionCallingArg(name="query", _type="str", description="搜索关键词")
            ]

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

    if llm_manager.has_type(ModelType.EMBEDDING):

        class MemoryChangeFunctionCalling(FunctionCalling):
            function_name = "memory_change"
            description = "更改你对用户的临时长期记忆，该记忆存在数量上限11"
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
                    log.debug(
                        f"用户 {data.sender_id} 新增记忆：{now().strftime('%Y年%m月%d日%a-%H时%M分')} {mem}"
                    )
                    msg.append("新增记忆成功")
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

            @staticmethod
            async def call(data: FunctionCallingData):
                query = data.data.get("query", "")
                top_k = data.data.get("top_k", 5)
                min_similarity = data.data.get("min_similarity")
                if not query:
                    return []
                return await memory_manager.get_memories(
                    query, int(top_k), min_similarity
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

    class GetDirTreeFunctionCalling(FunctionCalling):
        function_name = "get_dir_tree"
        description = "查询文件目录:root:不包含后续目录根目录;ATRI:系统代码目录;system_plugins:系统插件目录;plugins:插件目录;config:配置目录;errors:最近7天的错误报告目录"
        args: ClassVar = [
            FunctionCallingArg(name="dir", _type="str", description="目录名")
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            dir_name = data.data.get("dir", "")
            if dir_name == "root":
                path = Path(".")
                return f"{path.resolve()}/\n" + get_tree_string(
                    path, ignore_dirs={"ATRI", "data", "plugins"} | default_ignore_dirs
                )
            if dir_name == "ATRI":
                path = Path(".") / "ATRI"
                return f"{path.resolve()}/\n" + get_tree_string(
                    path, ignore_dirs={"system"} | default_ignore_dirs
                )
            if dir_name == "system_plugins":
                path = Path(".") / "ATRI" / "system"
                return f"{path.resolve()}/\n" + get_tree_string(path)
            if dir_name == "plugins":
                path = Path(".") / "plugins"
                return f"{path.resolve()}/\n" + get_tree_string(path)
            if dir_name == "config":
                path = Path(".") / "data" / "config"
                return f"{path.resolve()}/\n" + get_tree_string(path)
            if dir_name == "errors":
                path = Path(".") / "data" / "errors"
                return f"{path.resolve()}/\n" + get_tree_string(path, recent_days=7)
            return "请提供正确的目录名。"

    FunctionCallingManager.register(GetDirTreeFunctionCalling)

    class ReadFileFunctionCalling(FunctionCalling):
        function_name = "read_file"
        description = "读取指定文件"
        args: ClassVar = [
            FunctionCallingArg(name="path", _type="str", description="文件路径"),
            FunctionCallingArg(
                name="start", _type="int", description="可选,起始行数,默认1"
            ),
            FunctionCallingArg(
                name="end", _type="int", description="可选,结束行数,默认0"
            ),
        ]

        @staticmethod
        async def call(data: FunctionCallingData):
            path = data.data.get("path", "")
            start = data.data.get("start", 1)
            end = data.data.get("end", 0)
            return read_file(path, start, end)

    FunctionCallingManager.register(ReadFileFunctionCalling)

    AgentEventBus.publish_sync(event_type="register_FC", source="agent")
