import re
from pathlib import Path

from nonebot.adapters.onebot.v11 import Message

from ATRI import conf, driver
from ATRI.log import log
from ATRI.utils.event import Event

from .config import config
from .system.data.item import Item, ItemType, items
from .system.data.shop import Shop, shops
from .system.data.user import users

PLUGIN_VERSION = "0.4.5"
PLUGIN_DIR = Path(".") / "data" / "plugins" / "lkbot"


class BaseFunc:
    """lk插件的实用工具，用于其他插件使用lk插件提供的服务，当这里没有所需的方法时再从.system中使用底层代码
    其中.system.tools目录为工具类合集可随意使用，这些工具是lk插件添加的但并不包含lk插件的服务"""
    bind_tip = '还未绑定名称哟，使用指令 /lk.绑定 进行绑定'
    safe_mode_tip = '健康模式群聊无法使用此功能'
    chat_switch_off = '此服务已关闭'
    test_mode_tip = '此功能为测试功能，只能在测试模式群聊下使用'

    def __init__(self):
        self.master_ids = list(conf.BotConfig.superusers)
        self.bot_names = list(conf.BotConfig.nickname)
        self.bot_name = self.bot_names[0]

    @staticmethod
    def is_test_group(group_id: str | int) -> bool:
        """检查群聊是否是测试模式的群聊"""
        if type(group_id) is int:
            group_id = str(group_id)
        if group_id in config.config.test_groups:
            return True
        return False

    @staticmethod
    def is_safe_mode_group(group_id: str | int) -> bool:
        """检查群聊是否是安全模式群聊"""
        if type(group_id) is int:
            group_id = str(group_id)
        if group_id in config.config.r18_groups:
            return False
        return True

    def is_master(self, user_id: str | int) -> bool:
        """检查用户是否为主人(超级用户)"""
        if type(user_id) is int:
            user_id = str(user_id)
        if user_id in self.master_ids:
            return True
        return False

    @staticmethod
    def is_valid_user(user_id: str | int) -> bool:
        """检查用户是否为注册的有效用户"""
        if type(user_id) is int:
            user_id = str(user_id)
        if users.has_user(user_id):
            return True
        return False

    @staticmethod
    def get_name(user_id: str | int) -> str | None:
        """获取用户的名字"""
        if type(user_id) is int:
            user_id = str(user_id)
        try:
            return users.get_user_name(user_id)
        except Exception:
            return None

    def buy_item(self, user_id, shop_name, item_name: str, num: int) -> str:
        """用户从商店购买物品"""
        shop = shops.get_shop_by_name(shop_name)
        index = shop.get_goods_index(item_name)
        price = shop.get_goods_price_by_index(index)
        coin_type = shop.get_goods_coin_type_by_index(index)
        if coin_type == "ATRI币":
            money = price * num
            if users.money_change(user_id, -money):
                self.item_change(user_id, item_name, num)
                return f"购买 {item_name}*{num} 成功，共花费{money}ATRI币，你还有{users.get_money(user_id)}ATRI币"
            return f"ATRI币不足，需要{money}ATRI币，而你只有{users.get_money(user_id)}ATRI币"
        else:
            return "暂不支持物品购买"

    def sell_item(self, user_id, item_name, num) -> str:
        """用户回收(出售)物品"""
        item = items.get_item_by_name(item_name)
        backpack = users.get_backpack(user_id)
        if backpack.bp_has_item(item_name):
            if not self.item_change(user_id, item_name, -num):
                item_num = backpack[item_name]["num"]
                return f"物品 {item_name} 数量不足{num}个, 你只有{item_num}个"
            users.money_change(user_id, item.get_item_price() * num)
            return f"回收 {item_name}*{num} 成功"
        return f"你没有 {item_name}"

    @staticmethod
    def use_item(user_id: str, item_name: str, num: int) -> tuple[bool, str]:
        """用户使用物品"""
        item = items.get_item_by_name(item_name)
        if not item.item_can_use():
            return False, f"物品 {item_name} 不能使用"
        backpack = users.get_backpack(user_id)
        if item_name in backpack:
            item_num = backpack[item_name]["num"]
            if num == -1:
                num = item_num
            if item_num < num:
                return False, f"物品 {item_name} 数量不足{num}个, 你只有{item_num}个"
            msg = ''
            for i in range(num):
                resp = item.use_item(user_id)
                msg += f'{i + 1}. {resp}\n'
            return True, msg
        else:
            return False, f"你没有物品 {item_name}"

    @staticmethod
    def item_change(user_id, item_name: str, num: int):
        """对物品数量进行修改，自动添加或删除物品条目，注意:请确保修改后的数量不为负数"""
        return users.item_num_change(user_id, item_name, num)

    @staticmethod
    def clean_str(original_string: str) -> str:
        """去除字符串中的非法字符"""
        pattern = r"[\\'\"<> :：\(\)（）“”’‘【】\[\]`~]"
        cleaned_string = re.sub(pattern, '', original_string)
        return cleaned_string

    @staticmethod
    def extract_number(s: str) -> tuple[str, int]:
        """从字符串中提取名称与数字,要求格式:物品*n,物品,全部物品"""
        match = re.search(r'(.*?)\*(\d+)$', s)
        if match:
            item_name = match.group(1)
            number = int(match.group(2))
            return item_name, number
        if "全部" in s:
            item_name = s.replace("全部", "")
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
        将消息中的@转换为lk用户名
        :param o_message: 原始消息
        :return: 返回转换后的消息
        """
        text = ''
        for segment in o_message:
            if segment.type == 'at' and self.is_valid_user(segment.data['qq']):
                text = text + '[' + self.get_name(segment.data['qq']) + ']'
            elif segment.type == 'text':
                text = text + segment.data['text']
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

class SignInEvent(Event):
    def __init__(self):
        super().__init__()

    def notify(self, user_id):
        msg = ""
        for listener in self.listeners:
            r = listener(user_id)
            if msg:
                msg += r
        return msg

item_loading_event = Event()
sign_in_event = SignInEvent()


def load_item_data():
    """加载物品与商店数据"""
    # items.items_clear()
    # shops.shops_clear()
    """定义物品"""
    rename_card = Item("改名卡", ItemType.PROP, item_info='用于修改自己的名字，改名时自动使用哦')
    items.register(rename_card)

    """定义商店与添加物品"""
    base_shop = Shop("基础商店", "亚托莉开的小店,专门售卖一些实用基础物品给客户使用。")
    base_shop.add_goods(rename_card, 100)
    shops.register(base_shop)

    item_loading_event.notify()

    log.success(f'物品商店注册完成:共注册{len(items.get_item_list())}个物品，{len(shops.get_shop_names())}个商店')


driver().on_startup(load_item_data)

lk_util = BaseFunc()
