default_chat = 'gemini'


class BaseChat:
    async def generate_content(self, text: str, r_type: str = 'text', data: dict = None) -> str | dict | None:
        """生成回复"""
        pass


class ChatManager:
    def __init__(self):
        self.chats = {}
        from ATRI.system.lkbot.config import config
        self.active_chat = config.active_chat

    def register(self, name: str, chat: BaseChat):
        """添加新的大语言模型"""
        if name in self.chats:
            from ATRI.log import log
            log.warning(f'已经存在大语言模型`{name}`,将会覆盖旧的模型')
        self.chats[name] = chat

    async def generate_content(self, text: str, r_type: str = 'text', data: dict = None) -> str | dict | None:
        """从默认的大语言模型生成内容"""
        if self.active_chat in self.chats:
            resp = await self.chats[self.active_chat].generate_content(text, r_type, data)
            from ATRI.log import log
            log.debug(
                f'model:{self.active_chat} '
                f'type:{r_type} '
                f'input:{text if len(text) <= 20 else f'{text[:10]}...'} '
                f'output:{resp if len(resp) <= 20 else f'{resp[:10]}...'}'
            )
            return resp
        else:
            self.active_chat = default_chat
            from ATRI.system.lkbot.config import save_config
            save_config()
            return "默认大语言模型不存在已切换为默认"

    async def generate_content_from(self, name: str, text: str, r_type: str = 'text',
                                    data: dict = None) -> str | dict | None:
        """从指定的大语言模型生成内容"""
        if name in self.chats:
            resp = await self.chats[name].generate_content(text, r_type, data)
            from ATRI.log import log
            log.debug(
                f'model:{name} '
                f'type:{r_type} '
                f'input:{text if len(text) <= 20 else f'{text[:10]}...'} '
                f'output:{resp if len(resp) <= 20 else f'{resp[:10]}...'}'
            )
            return resp
        return "所选大语言模型不存在，请进行切换"

    def change_chat(self, name: str) -> bool:
        """改变默认的大语言模型名称"""
        if name in self.chats:
            self.active_chat = self.chats[name]
            return True
        return False

    def get_chat(self, name: str):
        """获取大语言模型对象"""
        return self.chats[name]

    def get_chats_name(self) -> list:
        """获取所有的大语言模型"""
        c_list = list(self.chats.keys())
        c_list.sort()
        return c_list


chat_manager = ChatManager()

from . import gemini
