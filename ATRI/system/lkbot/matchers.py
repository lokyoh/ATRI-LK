from ATRI.bot import BotUtils
from ATRI.permission import ADMIN, MASTER

from . import plugin, plugin_admin, plugin_master

cmd_str = BotUtils.get_command_start()
my_info = plugin.on_command(cmd="我的信息", docs="查询自己的信息")
my_backpack = plugin.on_command(cmd="我的背包", docs="查看背包中的内容")
item_inquiry = plugin.on_command(cmd="物品查询", docs="查询指定物品信息")
use_item = plugin.on_command(
    cmd="使用", docs="使用指定物品,'全部物品'使用全部,'物品*n'使用n个物品"
)
recycle_item = plugin.on_command(
    cmd="回收", docs="将指定数量物品换成ATRI币,'全部物品'回收全部,'物品*n'回收n个物品"
)
shop_list = plugin.on_command(cmd="商店列表", docs="列出所有商店")
goods_list = plugin.on_command(cmd="商品列表", docs="列出指定商店的商品列表")
buy_item = plugin.on_command(
    cmd="购买",
    docs=f"从指定商店中购买指定数量的商品\n用法:{cmd_str}购买 [商店名] [物品|物品*n]",
)
change_name = plugin.on_command(cmd="改名", docs="用改名卡修改自己的名称")
bind = plugin.on_command(cmd="绑定", docs="为自己绑定一个名称")
get_binding_secret = plugin.on_command(
    cmd="获取绑定密钥", docs="私聊获取一个小时有效的一次性账号绑定密钥"
)
bind_existing_account = plugin.on_command(
    cmd="绑定已有账号", docs="用绑定密钥将当前账号关联到已有主账号"
)
unbind_account = plugin.on_command(cmd="解绑", docs="解除当前账号与主账号的绑定关系")
rank = plugin.on_command("排行", "查看排行")
account_info = plugin.on_command("账号信息", "查看当前账号的信息")

user_list = plugin_admin.on_command(
    cmd="用户列表", docs="列出本群所有的用户", permission=ADMIN
)
sup_bind = plugin_admin.on_command(
    cmd="添加绑定",
    docs=f"用法:{cmd_str}添加绑定 @用户 [名称]\n为指定用户绑定名称",
    permission=ADMIN,
)
r18_mode_switch = plugin_admin.on_command(
    cmd="健康模式开关", docs="使用后更改群聊的健康模式", permission=ADMIN
)
broad_new_switch = plugin_admin.on_command(
    cmd="尝新开关", docs="使用后更改群聊是否能使用测试内容", permission=ADMIN
)

force_change_name = plugin_master.on_command(
    cmd="强制改名",
    docs=f"用法:{cmd_str}强制改名 @用户 [名称]\n将指定用户名称修改成指定名称",
    permission=MASTER,
)
all_user_list = plugin_master.on_command(
    cmd="所有用户", docs="列出所有的用户", permission=MASTER
)
force_bind_account = plugin_master.on_command(
    cmd="强制绑定已有账号",
    docs=f"用法:{cmd_str}强制绑定已有账号 [目标用户ID] [主账号ID]",
    permission=MASTER,
)
force_unbind_account = plugin_master.on_command(
    cmd="强制解绑",
    docs=f"用法:{cmd_str}强制解绑 [目标用户ID]",
    permission=MASTER,
)
