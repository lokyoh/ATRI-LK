from datetime import datetime
from typing import Type

from ..config import config
from ..utils import log, request
from .memes import FaceManager
from .user import get_user_info, save_user_info
from .user_profile import UserProfile


class ChatFunctionArg:
    def __init__(self, name: str, _type: str, description: str):
        self.name = name
        self.type = _type
        self.description = description


class ChatFunction:
    def __init__(
        self, function_name: str, description: str, args: list[ChatFunctionArg]
    ):
        self.function_name = function_name
        self.description = description
        self.args = args


class FunctionCallingData:
    def __init__(self, sender_id, group_id, data: dict):
        self.sender_id = sender_id
        self.group_id = group_id
        self.data = data


class FunctionCalling:
    continue_calling = False

    @staticmethod
    async def call(data: FunctionCallingData):
        pass


class FunctionCallingManager:
    calling: dict[str, Type[FunctionCalling]] = {}
    chat_functions: list[ChatFunction] = []

    @classmethod
    def register(
        cls, func_name: str, func: Type[FunctionCalling], func_dif: ChatFunction
    ):
        cls.calling[func_name] = func
        cls.chat_functions.append(func_dif)

    @classmethod
    async def call(cls, func_name: str, data: FunctionCallingData):
        return await cls.calling[func_name].call(data)

    @classmethod
    def is_continue_calling(cls, func_name: str):
        return cls.calling[func_name].continue_calling

    @classmethod
    def clear_all(cls):
        cls.calling.clear()
        cls.chat_functions.clear()


class ReplyFunctionCallingManager(FunctionCallingManager):
    calling: dict[str, Type[FunctionCalling]] = {}
    chat_functions: list[ChatFunction] = []


def register_function_calling():
    FunctionCallingManager.clear_all()

    class LoveChangeFunctionCalling(FunctionCalling):
        @staticmethod
        async def call(data: FunctionCallingData):
            # 处理好感度变化
            num = data.data.get("num", 0)
            if num:
                user_info = get_user_info(data.sender_id)
                user_info.love = user_info.love + num
                log.debug(
                    f"用户 {data.sender_id} 好感度变化：{num}, 当前值：{user_info.love}"
                )
                save_user_info(data.sender_id, user_info)

    FunctionCallingManager.register(
        "love_change",
        LoveChangeFunctionCalling,
        ChatFunction(
            function_name="love_change",
            description="更改你对用户的好感度",
            args=[
                ChatFunctionArg(
                    name="num", _type="int", description="与对话人好感度增减数值"
                )
            ],
        ),
    )

    class MemeryChangeFunctionCalling(FunctionCalling):
        @staticmethod
        async def call(data: FunctionCallingData):
            # 处理记忆变更
            mem = data.data.get("mem")
            del_index = data.data.get("del")
            # 新增记忆
            user_info = get_user_info(data.sender_id)
            if mem:
                user_info.memery.append(
                    f"{datetime.now().strftime('%Y年%m月%d日%a-%H时%M分')} {mem}"
                )
                log.debug(
                    f"用户 {data.sender_id} 新增记忆：{datetime.now().strftime('%Y年%m月%d日%a-%H时%M分')} {mem}"
                )
            if del_index:
                if type(del_index) is int:
                    del_index = [del_index]
                del_index.sort(reverse=True)
                for index in del_index:
                    try:
                        index: int
                        user_info.memery.pop(index)
                        log.debug(f"用户 {data.sender_id} 删除记忆：{index}")
                    except IndexError:
                        log.warning(f"用户 {data.sender_id} 删除记忆失败：{index}")
            save_user_info(data.sender_id, user_info)

    FunctionCallingManager.register(
        "memery_change",
        MemeryChangeFunctionCalling,
        ChatFunction(
            function_name="memery_change",
            description="更改你对用户的临时长期记忆，该记忆存在数量上限11",
            args=[
                ChatFunctionArg(
                    name="mem",
                    _type="str",
                    description="新增记忆临时长期记忆,超出上限则先入先出,不要添加时间戳,直接输出记忆内容,可选",
                ),
                ChatFunctionArg(
                    name="del",
                    _type="list[int]",
                    description="需要删除的记忆列表索引,请积极删除无用与过期的记忆,可选",
                ),
            ],
        ),
    )

    # 生成表情的例子字符串
    meme_examples = []
    for meme in FaceManager.get_all_memes():
        desc = FaceManager.get_meme_description(meme)
        if desc:
            # 只取描述的前一部分，避免过长
            short_desc = desc.split("（")[0] if "（" in desc else desc[:20]
            meme_examples.append(f"{meme}:{short_desc}")
    meme_example_str = ";".join(meme_examples)

    class SendFaceFunctionCalling(FunctionCalling):
        @staticmethod
        async def call(data: FunctionCallingData):
            return FaceManager.get_face(data.data.get("meme", ""))

    ReplyFunctionCallingManager.register(
        "send_face",
        SendFaceFunctionCalling,
        ChatFunction(
            function_name="send_face",
            description=f"(可选)发送一个指定表情词表情来表达自己的强烈情绪。表情词定义:{meme_example_str}",
            args=[ChatFunctionArg(name="meme", _type="str", description="表情词")],
        ),
    )

    if config.search.enable:

        class SearchFunctionCalling(FunctionCalling):
            continue_calling = True

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
                            "Authorization": f"Bearer {config.search.api_key}",
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
                    error_msg = f"搜索出错：{str(e)}"
                    log.error(error_msg)
                    return error_msg

        FunctionCallingManager.register(
            "search",
            SearchFunctionCalling,
            ChatFunction(
                function_name="search",
                description="搜索网络上的信息，并到一个总结性回答",
                args=[
                    ChatFunctionArg(name="query", _type="str", description="搜索关键词")
                ],
            ),
        )

        class UserProfileGetFunctionCalling(FunctionCalling):
            continue_calling = True

            @staticmethod
            async def call(data: FunctionCallingData):
                """
                获取指定用户的用户画像
                """
                user_id = data.data.get("user_id")
                return UserProfile.get_profile(user_id)

        FunctionCallingManager.register(
            "get_user_profile",
            UserProfileGetFunctionCalling,
            ChatFunction(
                function_name="get_user_profile",
                description="获取指定用户的用户画像，可以多次调用",
                args=[
                    ChatFunctionArg(
                        name="user_id", _type="str", description="目标用户ID"
                    )
                ],
            ),
        )

        class UserProfileChangeFunctionCalling(FunctionCalling):
            @staticmethod
            async def call(data: FunctionCallingData):
                """
                获取指定用户的用户画像
                """
                user_id = data.data.get("user_id")
                profile_change = data.data.get("profile_change")
                log.info(f"用户 {user_id} 更改画像：{profile_change}")
                now_user_profile = await UserProfile.change_profile(
                    user_id, profile_change
                )
                log.info(f"用户 {user_id} 画像更改成功: {now_user_profile}")

        FunctionCallingManager.register(
            "change_user_profile",
            UserProfileChangeFunctionCalling,
            ChatFunction(
                function_name="change_user_profile",
                description="更改指定用户的用户画像，用户画像包含客观描写与个人看法，但不是必须都存在的，可以多次调用",
                args=[
                    ChatFunctionArg(
                        name="user_id", _type="str", description="目标用户ID"
                    ),
                    ChatFunctionArg(
                        name="profile_change",
                        _type="str",
                        description="如何更改用户画像，除自己以外的人物全都使用用户ID，仅在change操作时需要",
                    ),
                ],
            ),
        )


register_function_calling()
