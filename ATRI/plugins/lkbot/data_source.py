from nonebot.adapters.onebot.v11 import MessageSegment, Message

from ATRI import __version__
from ATRI.log import log
from ATRI.utils.img_editor import get_image_bytes
from ATRI.plugins.lkbot.util import lk_util, PLUGIN_VERSION, sign_in_event
from ATRI.plugins.lkbot.system.data.user import users
from ATRI.plugins.lkbot.system.sign_in_pic import get_pic


class LKBot:
    new_things = f'''ATRI-LK版
{__version__} 新内容:
    - 新的状态
    - 新的服务列表
    - coser
    - 钉宫语音(@bot 骂&)
    - AI聊天更新
    - /lk.我的信息
    - 全新的签到界面
    - 物品-商店系统第一阶段
    - 健康模式
    - 尝新模式
    - Atri语音'''
    broad_message = f'''*本群已开启尝新模式，这是新功能的人工推送*
#lk插件v{PLUGIN_VERSION}:
    - 全新的签到界面
    - 物品-商店系统第一阶段
    - 新增健康模式
    - 完善尝新功能
    !输入"/帮助 lk插件"查看具体指令!
    !输入"/lk.新内容"查看更新内容!
#lk宠物v0.1.3-fix1:
    - 进行修复与预设词优化
    !输入"/帮助 lk宠物"查看具体指令!
#组队插件1.0.0-fix2-正式版：
    !输入"/帮助 组队插件"查看具体指令!'''

    @staticmethod
    async def sign_in(user_id, r18_mode):
        message = Message().append(MessageSegment.at(user_id))
        sign_result = users.sign(user_id)
        if sign_result:
            msg = sign_in_event.notify(user_id)
            message.append(msg)
        else:
            message.append("\n今日已签到")
        try:
            img_path = await get_pic(user_id, r18_mode=r18_mode)
            log.info(f'{user_id}签到 r18:{r18_mode}')
            message.append(MessageSegment.image(get_image_bytes(img_path)))
        except Exception as e:
            log.warning(f'{e}:\n{e.args}')
            message.append(f'签到成功,你已签到{users.get_user_data(user_id).signdays}天')
        return message

    @staticmethod
    def get_info(user_id):
        user = users.get_user_data(user_id)
        info = f'''用户 {user.name}:
等级:{user.lvl} 升级还需要{user.get_lvl_exp() - user.left_exp}经验
ATRI币:{user.money}
好感:{user.love}
宠物:{user.petname}'''
        return info

    @staticmethod
    def bind(user_id, name):
        name = lk_util.clean_str(name)
        if lk_util.is_valid_user(user_id):
            return f"那个...{user_id} 已绑定名称 {lk_util.get_name(user_id)} 啦"
        else:
            if len(name) > 10 or name == '':
                return "那个...名称字数超出限制10或为空"
            if not lk_util.is_valid_name(name):
                return "那个...这个名字不太合适吧"
            if users.add_user(user_id, name):
                return f"好欸！{user_id} 绑定名称 {name} 成功，咱又多了个新的朋友"
            return "那个...此名称已经被使用了，换个名字吧"
