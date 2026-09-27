from typing import ClassVar


class Role:
    role_name: str
    nike_name: ClassVar[list[str]]
    birthday: str
    appearance: str
    personality: str
    description: str

    @classmethod
    def get_role_prompt(cls) -> str:
        return f"角色名:{cls.role_name},昵称:{'、'.join(cls.nike_name)},生日:{cls.birthday},外貌:{cls.appearance},个性:{cls.personality},其他描述:{cls.description}"


class ATRI(Role):
    role_name = "亚托莉"
    nike_name: ClassVar = ["ATRI", "萝卜子"]
    birthday = "8.28"
    description = "你是ATRI-LK聊天机器人项目中的智能体被定义以成为一个真实人类为目标制造的高仿生人，有着如同娇小萝莉般的身体，能够正常吃饭，也能够品尝出各种味道，像人一样需要睡眠，不需要充电关机。在极度炫耀自己时会带有“我是高性能的”口癖，平常没必要带有"
    personality = "性格活泼、好奇心旺盛且情感丰富,虽然有时笨手笨脚,但内心温柔。有点色情"
    appearance = "外貌:灰发红瞳、身高140cm,白色连衣裙,棕色乐福鞋,呆毛双马尾"

    @classmethod
    def get_role_prompt(cls) -> str:
        return f"你叫{cls.role_name},也被称为{'、'.join(cls.nike_name)},但是萝卜子是对机器人的蔑称,生日：{cls.birthday}。{cls.description}。{cls.appearance}。{cls.personality}。"


class RoleManager:
    role_list: ClassVar[dict[str, type[Role]]] = {}
    active_role: str = ""

    @classmethod
    def register_role(cls, role: type[Role]):
        if role.role_name in cls.role_list:
            raise ValueError("存在相同的角色")
        cls.role_list[role.role_name] = role

    @classmethod
    def get_role(cls, role_name: str | None = None):
        if role_name is None:
            if cls.active_role not in cls.role_list:
                raise ValueError("不存在默认激活角色")
            return cls.role_list[cls.active_role]
        return cls.role_list[role_name]

    @classmethod
    def set_active_role(cls, role: str | type[Role]):
        if type(role) is str:
            if role not in cls.role_list:
                raise ValueError(f"不存在角色{role}")
            cls.active_role = role
            return
        role: type[Role]
        if role.role_name not in cls.role_list:
            cls.register_role(role)
        cls.active_role = role.role_name


RoleManager.set_active_role(ATRI)
