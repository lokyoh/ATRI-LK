import os
import random

from ATRI.dir import IMG_DIR
from ATRI.utils import request

img_sources = {}


async def lolicon():
    """获取一张来自lolicon的图片"""
    resp = await request.get(
        "https://api.lolicon.app/setu/v2",
        params={
            "r18": 0,
            "proxy": "false",
            "excludeAI": "true",
        },
    )
    resp.raise_for_status()
    url = resp.json()["data"][0]["urls"]["original"]
    resp = await request.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/119.0.0.0 "
                "Safari/537.36"
            ),
            "Referer": "https://www.pixiv.net/",
        },
    )
    resp.raise_for_status()
    return resp.content


async def lolicon_r18():
    """获取一张来自lolicon的r18图片"""
    resp = await request.get(
        "https://api.lolicon.app/setu/v2",
        params={
            "r18": 1,
            "proxy": "false",
            "excludeAI": "true",
        },
    )
    resp.raise_for_status()
    url = resp.json()["data"][0]["urls"]["original"]
    resp = await request.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/119.0.0.0 "
                "Safari/537.36"
            ),
            "Referer": "https://www.pixiv.net/",
        },
    )
    resp.raise_for_status()
    return resp.content


async def loli():
    """获取一张来自loli的图片"""
    resp = await request.get("https://www.loliapi.com/acg/pe/")
    resp.raise_for_status()
    return resp.content


def local_image_func(group_id: str = None):
    """获取一张来自本地res/img/sbg的图片"""
    file = random.choice(os.listdir(IMG_DIR / "sbg"))
    img_url = IMG_DIR / "sbg" / file
    with open(img_url, "rb") as f:
        return f.read()


_local_image = local_image_func


def set_local_image_func(func):
    global _local_image
    _local_image = func


def local_image(group_id: str = None):
    """
    本地背景图片获取。
    :param group_id: 群聊id
    :return: 图片数据
    """
    return _local_image(group_id)


async def get_pic_from(src, group_id: str = None) -> bytes:
    """
    获取一张来自指定图源的图片，默认本地。
    :param src: 图片源
    :param group_id: 群聊id
    :return: 图片数据
    """
    if src in img_sources:
        return await img_sources[src]()
    return local_image(group_id)


img_sources["lolicon"] = lolicon
img_sources["lolicon_r18"] = lolicon_r18
img_sources["loli"] = loli


def has_source(src: str) -> bool:
    """
    是否含有指定图片源。
    :param src: 图片源
    :return: 是否含有
    """
    if src == 'local':
        return True
    return src in img_sources
