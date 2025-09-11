import asyncio

from nonebot.adapters.onebot.v11 import MessageEvent, Message
from nonebot.internal.matcher import Matcher
from nonebot.internal.params import ArgPlainText

from ATRI.service import Service
from ATRI.message import img_msg
from ATRI.system.lkapi.bot.checker import IsLkUser
from ATRI.system.lkapi.bot import util as lk_util
from ATRI.system.lkapi.entity.user import get_user_data
from ATRI.system.htmlrender import md_to_pic

from .data.bait import bait_dict
from .data.exception import FishingException
from .data.fishing_rod import FishingRod, fishing_rod_dict
from .data.user import get_fish_user_data
from .data.achievement import achievements
from .data_source import FishingController

plugin = Service(
    "钓鱼",
    "ATRI的钓鱼插件",
    "0.1.3",
    Service.ServiceType.ENTERTAINMENT
).main_cmd("/钓鱼")

player_info = plugin.cmd_as_group('我的信息', '查看自己的个人信息')


@player_info.handle([IsLkUser])
async def _(event: MessageEvent):
    info = get_fish_user_data(event.user_id)
    await player_info.finish(str(info))


achievement = plugin.cmd_as_group('成就', '查看个人成就')


@achievement.handle([IsLkUser])
async def _(event: MessageEvent):
    a_l = []
    u_a_l = []
    with get_fish_user_data(event.user_id) as user:
        achieved = user.achievement.achieve
        for a in achievements:
            if a.name not in achieved:
                u_a_l.append(f'- {a.name} - {a.description}')
            else:
                a_l.append(f'- {a.name} - {a.description}')
    msg = f'# {lk_util.get_name(event.user_id)}的钓鱼成就:\n\n'
    msg += '## 已达成的成就:\n\n'
    if a_l:
        msg += '\n'.join(a_l)
    else:
        msg += '还没有成就达成哦!'
    msg += '\n\n## 未达成的成就:\n\n'
    if u_a_l:
        msg += '\n'.join(u_a_l)
    else:
        msg += '所有成就都达成了哦!'
    await achievement.finish(img_msg(await md_to_pic(msg)))


through = plugin.on_command('/钓鱼', '抛出鱼钩', aliases={'/抛钩'})


@through.handle([IsLkUser])
async def _(event: MessageEvent):
    user_id = event.user_id
    try:
        wait_time = FishingController.add_new_fishing_man(user_id)
        await through.send(f'🎣中...请等待🐟上钩...')
        await asyncio.sleep(wait_time)
        FishingController.fish_bite(user_id)
        await through.send('!!!🐟上钩了,快输入[/收线]!!!', at_sender=True)
    except FishingException as e:
        await through.finish(e.message)


take = plugin.on_command('/收线', '鱼上钩后收起鱼线')


@take.handle([IsLkUser])
async def _(event: MessageEvent):
    user_id = event.user_id
    try:
        fish, msg = FishingController.take_up(user_id)
        if msg:
            await take.send(f'获得成就:\n{'\n'.join(f'{m}' for m in msg)}')
        await take.finish(
            f'{lk_util.get_name(user_id)}钓到了[{fish.fish.name}{f'-{fish.quality}' if fish.quality else ''}]!\n'
            f'介绍:{fish.fish.description}\n'
            f'{
            f'长度:{fish.length}cm{' 最大长度!!!' if fish.length == fish.fish.size['max'] else ''}\n'
            if fish.length else ''
            }'
        )
    except FishingException as e:
        await through.finish(e.message)


goto = plugin.on_command('/前往', '前往不同的钓鱼地点.如:湖、河、海')


@goto.handle([IsLkUser])
async def _(event: MessageEvent):
    user_id = event.user_id
    user_data = get_fish_user_data(user_id)
    cmd = event.get_plaintext().split(' ')
    if len(cmd) == 2:
        text = cmd[1]
        if text == '湖':
            user_data.position = 'lake'
            user_data.save_data()
            await goto.finish('前往湖成功')
        elif text == '海':
            user_data.position = 'ocean'
            user_data.save_data()
            await goto.finish('前往海成功')
        elif text == '河':
            user_data.position = 'river'
            user_data.save_data()
            await goto.finish('前往河成功')
        await goto.finish(f'前往{text}失败,不存在地名')
    await goto.finish(f'请输入地名')


equip = plugin.on_command('/装备', '装备指定的鱼竿、鱼饵或鱼具')


@equip.handle([IsLkUser])
async def _(event: MessageEvent, matcher: Matcher):
    cmd = event.get_plaintext().split(' ')
    if len(cmd) == 2:
        matcher.set_arg('equipment', Message().append(cmd[1]))


@equip.got('equipment', '请输入要装备的装备名')
async def _(event: MessageEvent, eq: str = ArgPlainText("equipment")):
    user_id = event.user_id
    msg = f'装备{eq}成功\n'
    with get_fish_user_data(user_id) as user:
        with get_user_data(user_id) as user_data:
            if user.equip_rod(eq, user_data) or user.equip_bait(eq, user_data) or user.equip_tackle(eq, user_data):
                msg += str(user)
            else:
                msg = f'你没有装备{eq}或者{eq}不能装备'
    await equip.finish(msg)
