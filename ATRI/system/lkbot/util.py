import re
from pathlib import Path
from random import choice

from nonebot.adapters.onebot.v11 import Message

from ATRI import conf
from ATRI.log import log
from ATRI.permission import MASTER_LIST
from ATRI.utils.event import BaseEvents, BaseEvent

from .config import config
from .data.item import items
from .data.item_func import register_core_func, item_funcs
from .data.shop import shops
from .data.user import users, UserData
from .tools.daily_update import daily_update
from .data.load_item import auto_load_items
from .tools.get_pic import set_local_image_func

PLUGIN_VERSION = "0.11.0"
"""lkbot插件版本"""
PLUGIN_DIR = Path(".") / "data" / "plugins" / "lkbot"
"""lkbot插件数据路径"""


class BaseFunc:
    """lk插件的实用工具，用于其他插件使用lk插件提供的服务"""
    bind_tip = '还未绑定名称哟，使用指令 /绑定 进行绑定'
    safe_mode_tip = '健康模式群聊无法使用此功能'
    chat_switch_off = 'AI聊天服务已关闭'
    test_mode_tip = '此功能为测试功能，只能在测试模式群聊下使用'

    def __init__(self):
        self.bot_names = list(conf.BotConfig.nickname)
        self.bot_name = choice(self.bot_names)

    @staticmethod
    def is_test_group(group_id: str | int) -> bool:
        """检查群聊是否是测试模式的群聊"""
        if type(group_id) is int:
            group_id = str(group_id)
        return group_id in config.test_groups

    @staticmethod
    def is_safe_mode_group(group_id: str | int) -> bool:
        """检查群聊是否是安全模式群聊"""
        if type(group_id) is int:
            group_id = str(group_id)
        return not group_id in config.r18_groups

    @staticmethod
    def is_master(user_id: str | int) -> bool:
        """检查用户是否为主人(超级用户)"""
        if type(user_id) is int:
            user_id = str(user_id)
        return user_id in MASTER_LIST

    @staticmethod
    def is_valid_user(user_id: str | int) -> bool:
        """检查用户是否为注册的有效用户"""
        if type(user_id) is int:
            user_id = str(user_id)
        return users.has_user(user_id)

    def get_name(self, user_id: str | int) -> str | None:
        """获取用户的名字"""
        if type(user_id) is int:
            user_id = str(user_id)
        return users.get_user_name(user_id) if self.is_valid_user(user_id) else None

    def buy_item_func(self, user_data: UserData, shop_name: str, item_name: str, num: int) -> str:
        """用户从商店购买物品,注意数据保护及保存"""
        shop = shops.get_shop_by_name(shop_name)
        index = shop.get_goods_index(item_name)
        price = shop.get_goods_price_by_index(index)
        coin_type = shop.get_goods_coin_type_by_index(index)
        money = price * num
        if coin_type == "ATRI币":
            if user_data.money_change(-money):
                self.item_change_func(user_data, item_name, num)
                return f"购买 {item_name}*{num} 成功，共花费{money}ATRI币，你还有{user_data.money}ATRI币"
            return f"ATRI币不足，需要{money}ATRI币，而你只有{user_data.money}ATRI币"
        else:
            if not items.has_item(coin_type):
                return f"错误，请反馈:\n找不到交易货币`{coin_type}`"
            if self.item_change_func(user_data, coin_type, -money):
                self.item_change_func(user_data, item_name, num)
                return f"购买 {item_name}*{num} 成功，共花费{money}{coin_type}，你还有{user_data.backpack.get_item_stack(coin_type).meta.num}{coin_type}"
            return f"{coin_type}不足，需要{money}{coin_type}，而你只有{user_data.backpack.get_item_stack(coin_type).meta.num}{coin_type}"

    def buy_item(self, user_id: str, shop_name: str, item_name: str, num: int) -> str:
        """用户从商店购买物品,包装后的方法,推荐处理流程少使用"""
        with users.get_user_data(user_id) as user_data:
            return self.buy_item_func(user_data, shop_name, item_name, num)

    def sell_item_func(self, user_data: UserData, item_name: str, num: int) -> str:
        """用户回收(出售)物品,注意数据保护及保存"""
        item = items.get_item_by_name(item_name)
        backpack = user_data.backpack
        if backpack.bp_has_item(item_name):
            item_stack = backpack.get_item_stack(item_name)
            if num == -1:
                num = item_stack.meta.num
            elif num < -1 or num == 0:
                raise ValueError('数量错误')
            if not self.item_change_func(user_data, item_name, -num):
                item_num = item_stack.meta.num
                return f"物品 {item_name} 数量不足{num}个，你只有{item_num}个"
            user_data.money_change(item.get_item_price() * num)
            return f"回收 {item_name}*{num} 成功，获得{item.get_item_price() * num}ATRI币"
        return f"你没有 {item_name}"

    def sell_item(self, user_id: str, item_name: str, num: int) -> str:
        """用户回收(出售)物品,包装后的方法,推荐处理流程少使用"""
        with users.get_user_data(user_id) as user_data:
            return self.sell_item_func(user_data, item_name, num)

    @staticmethod
    def use_item_func(user_data: UserData, item_name: str, num: int) -> tuple[bool, str]:
        """用户使用物品,注意数据保护及保存"""
        item = items.get_item_by_name(item_name)
        if not item.item_can_use():
            return False, f"物品 {item_name} 不能使用"
        backpack = user_data.backpack
        if backpack.bp_has_item(item_name):
            item_num = backpack.get_item_stack(item_name).meta.num
            if num == -1:
                num = item_num
            if item_num < num:
                return False, f"物品 {item_name} 数量不足{num}个，你只有{item_num}个"
            msg = ''
            for i in range(num):
                resp = item.use_item(user_data)
                msg += f'{i + 1}. {resp}\n'
            return True, msg
        else:
            return False, f"你没有物品 {item_name}"

    def use_item(self, user_id: str, item_name: str, num: int) -> tuple[bool, str]:
        """用户使用物品,包装后的方法,推荐处理流程少使用"""
        with users.get_user_data(user_id) as user_data:
            return self.use_item_func(user_data, item_name, num)

    @staticmethod
    def item_change_func(user_data: UserData, item_name: str, num: int):
        """对物品数量进行修改，自动添加或删除物品条目，注意:请确保修改后的数量不为负数,注意数据保护及保存"""
        return user_data.item_num_change(item_name, num)

    def item_change(self, user_id: str, item_name: str, num: int):
        """对物品数量进行修改，自动添加或删除物品条目，注意:请确保修改后的数量不为负数,包装后的方法,推荐处理流程少使用"""
        with users.get_user_data(user_id) as user_data:
            return self.item_change_func(user_data, item_name, num)

    @staticmethod
    def clean_str(original_string: str) -> str:
        """去除字符串中的非法字符"""
        pattern = r"[\\'\"<> :：\(\)（）“”’‘【】\[\]`~]"
        cleaned_string = re.sub(pattern, '', original_string)
        return cleaned_string

    @staticmethod
    def extract_number(s: str) -> tuple[str, int]:
        """从字符串中提取名称与数字,要求格式:物品,物品*n,物品×n,全部物品"""
        match = re.search(r'(.*?)[*×](\d+)$', s)
        if match:
            item_name = match.group(1)
            number = int(match.group(2))
            return item_name, number
        if s.startswith('全部'):
            item_name = s[2:]
            return item_name, -1
        return s, 1

    def is_valid_name(self, name: str) -> bool:
        """检测名称是否含有违禁词"""
        for bot_name in self.bot_names:
            if bot_name in name:
                return False
        if re.search(r'爸|妈|爷|父|母|奶|father|mother|papa|mama|grand|主人|爹|娘', name, re.I):
            return False
        return True

    def get_trans_text(self, o_message: Message) -> str:
        """
        将消息中各项转换为文本。
        :param o_message: 原始消息
        :return: 返回转换后的消息
        """
        text = ''
        for segment in o_message:
            if segment.type == 'text':
                text += segment.data['text']
            elif segment.type == 'at' and self.is_valid_user(segment.data['qq']):
                text += f'[@{self.get_name(segment.data['qq'])}]'
            elif segment.type == "face":
                face_text = segment.data.get("raw", {}).get("faceText", '')
                if face_text:
                    text += face_text
            elif segment.type == "image":
                summary = segment.data.get("summary", '')
                if summary != '[动画表情]':
                    text += summary
        return text

    def user_change_name(self, user_id, new_name, limit: int = 10) -> tuple[bool, str]:
        """
        更改用户名
        :param user_id: 用户id
        :param new_name: 新的用户名
        :param limit: 用于限制名称的长度
        :return 返回两个数据:第一个为操作是否成功，第二个为返回的提示信息
        """
        if not self.is_valid_user(user_id):
            return False, '可惜捏，没找到那个人'
        if len(new_name) > limit or new_name == '':
            return False, f"那个...名称字数超出限制{limit}或为空"
        if not self.is_valid_name(new_name):
            return False, "那个...这个名字不太合适吧"
        if users.change_name(user_id, self.get_name(user_id), new_name):
            return True, f"咦？{user_id}的名字换成 {new_name} 了？"
        return False, "那个...此名称已经被使用了，换个名字吧"


class SignInEvent(BaseEvent):
    """
    签到事件体。
    """

    def __init__(self, user_data: UserData):
        super().__init__('签到事件')
        self.user_data = user_data


class UserInfoEvent(BaseEvent):
    """
    获取玩家信息事件体。
    """

    def __init__(self, user_data: UserData):
        super().__init__('获取玩家信息事件')
        self.user_data = user_data


item_loading_events = BaseEvents()
"""物品加载事件，在加载物品列表时触发"""
sign_in_events = BaseEvents()
"""签到事件，在用户签到时触发"""
func_register_events = BaseEvents()
"""物品功能注册事件，在注册物品时触发"""
init_finish_events = BaseEvents()
"""初始化完成事件，在该插件系统所有的数据加载完成后触发"""
user_info_events = BaseEvents()
"""获取玩家信息事件"""


def load_item_data():
    """加载物品与商店数据，可通过调用以实现随时加载数据"""
    items.items_clear()
    shops.shops_clear()
    # 从本地文件加载物品数据
    auto_load_items()
    # 可以在此事件为物品添加使用方法的添加
    item_loading_events.notify(BaseEvent('物品加载事件'))
    log.success(f'物品商店注册完成:共注册{len(items.get_item_list())}个物品，{len(shops.get_shop_names())}个商店')


def on_startup():
    """所有插件加载完毕后启动时的启动项"""
    from ATRI.system.lkbot import plugin
    register_core_func()
    func_register_events.notify(BaseEvent('物品功能注册事件'))
    log.success(f'物品方法注册成功:共注册{item_funcs.check_num()}个检测器，{item_funcs.func_num()}个物品方法')
    load_item_data()
    plugin.scheduler_jobs().add_job(daily_update, '每日更新任务', 'cron', hour=0, minute=0)
    try:
        from ATRI.system.lk_imglib.data_source import get_background
        set_local_image_func(get_background)
        log.info('启用全局图库内图片作为本地图源')
    except:
        pass
    init_finish_events.notify(BaseEvent('初始化完成事件'))


lk_util = BaseFunc()
"""用户管理工具"""
