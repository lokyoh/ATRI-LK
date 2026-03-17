from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent
from nonebot.adapters.onebot.v11.helpers import extract_image_urls
from nonebot.internal.matcher import Matcher
from nonebot.internal.params import ArgPlainText
from nonebot.params import CommandArg

from ATRI.message import img_msg
from ATRI.service import Service
from ATRI.utils import request

from .config import LKImgLibConfig
from .data_source import GroupImageManager, image_manager

plugin = Service(
    "图库",
    "额外图库系统，支持全局图库与群聊图库",
    "0.0.3",
    Service.ServiceType.LKPLUGIN,
).main_cmd("图库")

plugin_config = plugin.add_plugin_config(LKImgLibConfig)

from .permission import GLOBAL, GROUP  # noqa: E402

global_tu_add = plugin.cmd_as_group("全局添加", "添加全局图库", permission=GLOBAL)


@global_tu_add.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    path = args.extract_plain_text()
    if path:
        matcher.set_arg("img_path", args)


@global_tu_add.got(
    "img_path", '要添加在哪里呢，请输入"图名"或者"库名 图名"添加进指定库中'
)
@global_tu_add.got("img_data", "请发送要上传的图片")
async def _(event: MessageEvent, text: str = ArgPlainText("img_path")):
    img_url = extract_image_urls(event.message)
    if img_url:
        img_url = img_url[0]
    else:
        await tu_add.finish("请发送图片而不是其他消息，请重新添加")
    text = text.split(" ")
    if len(text) == 1:
        libname = None
        img_name = text[0]
    else:
        libname = text[0]
        img_name = text[1]
    try:
        resp = await request.get(img_url.replace("https://", "http://"))
        resp.raise_for_status()
        image_manager.save_image(
            img_name=img_name, img_data=resp.content, libname=libname
        )
    except Exception as e:
        await global_tu_add.finish(f"怎么办，保存图片失败了捏：{e}")
    await global_tu_add.finish(
        f"添加 {img_name} 进 {libname if libname else '默认'} 库成功"
    )


tu_add = plugin.cmd_as_group("添加", "添加本群图库", permission=GROUP)


@tu_add.handle()
async def _(event: GroupMessageEvent, matcher: Matcher, args: Message = CommandArg()):
    event.get_user_id()
    path = args.extract_plain_text()
    if path:
        matcher.set_arg("img_path", args)


@tu_add.got("img_path", '要添加在哪里呢，请输入"图名"或者"库名 图名"添加进指定库中')
@tu_add.got("img_data", "请发送要上传的图片")
async def _(event: GroupMessageEvent, text: str = ArgPlainText("img_path")):
    img_url = extract_image_urls(event.message)
    if img_url:
        img_url = img_url[0]
    else:
        await tu_add.finish("请发送图片而不是其他消息，请重新添加")
    text = text.split(" ")
    print(text)
    if len(text) == 1:
        libname = None
        img_name = text[0]
    else:
        libname = text[0]
        img_name = text[1]
    manager = GroupImageManager(str(event.group_id))
    try:
        resp = await request.get(img_url.replace("https://", "http://"))
        resp.raise_for_status()
        manager.save_image(img_name=img_name, img_data=resp.content, libname=libname)
    except Exception as e:
        await tu_add.finish(f"怎么办，保存图片失败了捏：{e}")
    await tu_add.finish(f"添加 {img_name} 进 {libname if libname else '默认'} 库成功")


global_tu_show = plugin.cmd_as_group("查看", "查看全局图库图片")


@global_tu_show.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    text = args.extract_plain_text()
    text = text.split(" ")
    if len(text) == 1:
        matcher.set_arg("libname", Message().append(text[0]))
    else:
        matcher.set_arg("libname", Message().append(text[0]))
        matcher.set_arg("img_name", Message().append(text[1]))


@global_tu_show.got("libname", "你要查看哪个库中的图片呢")
@global_tu_show.got("img_name", "你要查看哪张图片呢")
async def _(
    libname: str = ArgPlainText("libname"), img_name: str = ArgPlainText("img_name")
):
    img = image_manager.get_image_from(libname=libname, img_name=img_name)
    if img is not None:
        await global_tu_show.finish(img_msg(img), at_sender=True)
    else:
        await global_tu_show.finish(f"没有找到指定图片{libname}/{img_name}")


tu_show = plugin.cmd_as_group("查看本群", "查看本群图库图片")


@tu_show.handle()
async def _(event: GroupMessageEvent, matcher: Matcher, args: Message = CommandArg()):
    event.get_user_id()
    text = args.extract_plain_text()
    text = text.split(" ")
    if len(text) == 1:
        matcher.set_arg("libname", Message().append(text[0]))
    else:
        matcher.set_arg("libname", Message().append(text[0]))
        matcher.set_arg("img_name", Message().append(text[1]))


@tu_show.got("libname", "你要查看哪个库中的图片呢")
@tu_show.got("img_name", "你要查看哪张图片呢")
async def _(
    event: GroupMessageEvent,
    libname: str = ArgPlainText("libname"),
    img_name: str = ArgPlainText("img_name"),
):
    img = GroupImageManager(str(event.group_id)).get_image_from(
        libname=libname, img_name=img_name
    )
    if img is not None:
        await tu_show.finish(img_msg(img), at_sender=True)
    else:
        await tu_show.finish(f"没有找到指定图片{libname}/{img_name}")


rand_all_img = plugin.cmd_as_group("随机所有图片", "从全局与本群获取随机一张图片")


@rand_all_img.handle()
async def _(event: MessageEvent):
    if type(event) is GroupMessageEvent:
        manager = GroupImageManager(str(event.group_id))
        img = manager.get_all_random_image()
    else:
        img = image_manager.get_random_image()
    if img is not None:
        await rand_all_img.finish(img_msg(img), at_sender=True)
    else:
        await rand_all_img.finish("图库内没有图片")


rand_global_img = plugin.cmd_as_group(
    "随机图片", "从全局获取随机一张图片,当存在参数时指定图库名"
)


@rand_global_img.handle()
async def _(args: Message = CommandArg()):
    libname = args.extract_plain_text()
    if libname:
        img = image_manager.get_image_from(libname=libname)
    else:
        img = image_manager.get_random_image()
    if img is not None:
        await rand_global_img.finish(img_msg(img), at_sender=True)
    else:
        await rand_global_img.finish("图库内没有图片")


rand_group_img = plugin.on_command(
    "图库.随机本群图片",
    "从本群获取随机一张图片,当存在参数时指定图库名",
    aliases={"图片"},
)


@rand_group_img.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    manager = GroupImageManager(str(event.group_id))
    libname = args.extract_plain_text()
    if libname:
        img = manager.get_image_from(libname=libname)
    else:
        img = manager.get_random_image()
    if img is not None:
        await rand_group_img.finish(img_msg(img), at_sender=True)
    else:
        await rand_group_img.finish("图库内没有图片")
