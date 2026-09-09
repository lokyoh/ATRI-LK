from ..llm import ModelType, llm_manager
from .user import get_user_info, save_user_info


class UserProfile:
    prompt = """# 角色定位
你是一个用户画像生成器，你需要根据原来的用户画像按照更改请求进行更改。
原来的用户画像不存在时则根据更改生成新的。不要生成不存在的内容。

## 原来的用户画像
{o_profile}

## 更改请求
{profile_change}

用户画像包含客观描写与输入者的个人看法，但不是必须都存在的，现在开始输出更改后的用户画像"""

    @classmethod
    def get_profile(cls, user_id):
        user_info = get_user_info(user_id)
        return user_info.profile

    @classmethod
    async def change_profile(cls, user_id, profile_change):
        user_info = get_user_info(user_id)
        o_profile = user_info.profile
        prompt = cls.prompt.format(o_profile, profile_change)
        try:
            profile = await llm_manager.call_model_by_type(ModelType.TOOL, prompt)
        except Exception:
            return
        user_info.profile = profile.content
        save_user_info(user_id, user_info)
