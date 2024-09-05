from random import choice

from nonebot.internal.matcher import Matcher
from nonebot.params import CommandArg, ArgPlainText
from nonebot.adapters.onebot.v11 import Event, Message, Bot, GroupMessageEvent
from nonebot.adapters.onebot.v11.helpers import Cooldown

from ATRI.service import Service
from ATRI.system.lkbot import lk_util
from ATRI.log import log
from ATRI.permission import ADMIN, MASTER
from ATRI.message import img_msg
from ATRI.system.htmlrender import md_to_pic
from ATRI.system.lkbot.checker import is_lk_user, not_safe_mode, is_test_mode, is_chat_switch_on

from .pet_chat import PetModel
from .pet_data import PetData, pet_manager

plugin = Service("lk宠物").document("l_o_o_k的赛博宠物插件").type(Service.ServiceType.LKPLUGIN).version(
    "0.1.3-fix1").main_cmd("/pet")

_lmt_notice = ["慢...慢一..点❤", "冷静1下", "歇会歇会~~", "呜呜...别急", "太快了...受不了", "不要这么快呀"]

talk_with_pet = plugin.on_command("/宠物", "与赛博宠物聊天")


@talk_with_pet.handle([Cooldown(10, prompt=choice(_lmt_notice))])
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    await is_chat_switch_on(talk_with_pet)
    await not_safe_mode(talk_with_pet, event)
    await is_test_mode(talk_with_pet, event)
    await is_lk_user(talk_with_pet, event)
    user_id = event.get_user_id()
    if user_id not in pet_manager.datas:
        await talk_with_pet.finish("你还没领养过宠物哟，赶快领养一个吧")
    else:
        text = lk_util.get_trans_text(args)
        nickname = lk_util.get_name(user_id)
        if text == "":
            data: PetData = pet_manager.datas[user_id]
            text = data.name
        try:
            convo: PetModel = pet_manager.convos[user_id]
            response = convo.chat_with(nickname + ":" + text)
        except Exception as e:
            log.warning(e)
            await talk_with_pet.finish("很可惜，宠物不理你了")
        log.info(response)
        response = f"{pet_manager.datas[user_id].name}:\n{response}"
        if len(response) > 100:
            await talk_with_pet.finish(img_msg(await md_to_pic(response)))
        await talk_with_pet.finish(response)


adopt = plugin.cmd_as_group("领养", "领养一只专属自己赛博宠物吧")


@adopt.handle()
async def _(event: Event, matcher: Matcher, args: Message = CommandArg()):
    await is_lk_user(adopt, event)
    user_id = event.get_user_id()
    if user_id in pet_manager.datas:
        pet: PetData = pet_manager.datas[user_id]
        await adopt.finish(f"你已经有过一只宠物 {pet.name} 了哟")
    name = args.extract_plain_text()
    if name:
        if name in lk_util.bot_names:
            await adopt.finish("讨厌，不能使用咱的名字哦")
        matcher.set_arg("pet_name", args)


@adopt.got("pet_name", "宠物的名字呢？速速")
@adopt.got("pet_instruction", "你想要个什么样的宠物呢")
async def _(event: Event, name: str = ArgPlainText("pet_name"), instruction: str = ArgPlainText("pet_instruction")):
    if name in lk_util.bot_names:
        await adopt.reject("讨厌，不能使用咱的名字哦")
    name = lk_util.clean_str(name)
    if name == '':
        await adopt.reject("名字为空哦")
    if len(name) > 10:
        await adopt.reject("名称大于10字符")
    user_id = event.get_user_id()
    pet_manager.new_pet(user_id, name, instruction)
    pet: PetData = pet_manager.datas[user_id]
    user_name = lk_util.get_name(user_id)
    await adopt.finish(f"{user_name} 的小可爱 {pet.name} 领养成功啦，快来与它玩耍吧")


check_pet_love = plugin.cmd_as_group("好感度查询", "查询赛博宠物的好感度")


@check_pet_love.handle([Cooldown(30, prompt=choice(_lmt_notice))])
async def _(event: Event):
    user_id = event.get_user_id()
    if user_id not in pet_manager.datas:
        await check_pet_love.finish("还没有领养宠物哟，赶快领养一个吧")
    love = pet_manager.datas[user_id].love
    await check_pet_love.finish(f"宠物的好感度为{love}")


change_pet_name_cmd = plugin.cmd_as_group("改名", "为自己的赛博宠物改名")


@change_pet_name_cmd.handle([Cooldown(300, prompt=choice(_lmt_notice))])
async def _(event: Event, matcher: Matcher, args: Message = CommandArg()):
    user_id = event.get_user_id()
    if user_id not in pet_manager.datas:
        await change_pet_name_cmd.finish("还没有领养宠物哟，赶快领养一个吧")
    name = args.extract_plain_text()
    if name:
        if name in lk_util.bot_names:
            await change_pet_name_cmd.finish("讨厌，不能使用咱的名字哦")
        matcher.set_arg("new_pet_name", args)


@change_pet_name_cmd.got("new_pet_name", "宠物新的名字呢？速速")
async def _(event: Event, pet_name: str = ArgPlainText("new_pet_name")):
    if pet_name in lk_util.bot_names:
        await change_pet_name_cmd.finish("讨厌，不能使用咱的名字哦")
    pet_name = lk_util.clean_str(pet_name)
    if pet_name == '':
        await adopt.reject("名字为空哦")
    if len(pet_name) > 10:
        await change_pet_name_cmd.finish("名称大于10字符")
    user_id = event.get_user_id()
    pet_manager.change_pet_name(user_id, pet_name)
    await change_pet_name_cmd.finish("修改成功啦")


change_pet_inst_cmd = plugin.cmd_as_group("更改设定", "更改赛博宠物的设定")


@change_pet_inst_cmd.handle([Cooldown(300, prompt=choice(_lmt_notice))])
async def _(event: Event, matcher: Matcher, args: Message = CommandArg()):
    user_id = event.get_user_id()
    if user_id not in pet_manager.datas:
        await change_pet_name_cmd.finish("还没有领养宠物哟")
    inst = args.extract_plain_text().replace('\'', '')
    if inst:
        matcher.set_arg("pet_inst", args)


@change_pet_inst_cmd.got("pet_inst", "你想要个什么样的宠物呢？")
async def _(event: Event, inst: str = ArgPlainText("pet_inst")):
    user_id = event.get_user_id()
    pet_manager.change_pet_inst(user_id, inst)
    await change_pet_inst_cmd.finish("修改成功啦")


pet_list = plugin.cmd_as_group(cmd='宠物列表', docs='列出本群所有的宠物', permission=ADMIN)


@pet_list.handle()
async def _(bot: Bot, event: Event):
    group_id = int(event.group_id)
    member_list = await bot.get_group_member_list(group_id=group_id)
    members = []
    for member in member_list:
        user_id = str(member['user_id'])
        if user_id in pet_manager.datas:
            members.append(user_id)
    num = len(members)
    resp = '本群宠物列表:\n'
    i = 0
    j = 0
    while i < num:
        for i in range(20 + j * 20):
            if i == num:
                break
            resp += f'{i + 1}.{pet_manager.datas[members[i]].name}:{members[i]}\n'
        await  pet_list.send(resp + f'宠物总数:{i}/{num}')
        j += 1
        resp = ''


all_pet_list = plugin.cmd_as_group(cmd='所有宠物', docs='列出所有宠物', permission=MASTER)


@all_pet_list.handle()
async def _(bot: Bot):
    group_list = await bot.get_group_list()
    members = []
    for group in group_list:
        group_id = group['group_id']
        member_list = await bot.get_group_member_list(group_id=group_id)
        for member in member_list:
            user_id = str(member['user_id'])
            if user_id not in members and user_id in pet_manager.datas:
                members.append(user_id)
    num = len(members)
    resp = '所有宠物列表:\n'
    i = 0
    j = 0
    while i < num:
        for i in range(20 + j * 20):
            if i == num:
                break
            resp += f'{i + 1}.{pet_manager.datas[members[i]].name}:{members[i]}\n'
        await  all_pet_list.send(resp + f'宠物总数:{i}/{num}')
        j += 1
        resp = ''
