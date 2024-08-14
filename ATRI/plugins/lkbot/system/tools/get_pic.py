import os
import random
from PIL import Image

from ATRI import IMG_DIR
from ATRI.utils.request import get


async def lolicon():
    resp = await get(
        "https://api.lolicon.app/setu/v2",
        params={
            "r18": 0,
            "proxy": "false",
            "excludeAI": "true",
        },
    )
    resp.raise_for_status()
    url = resp.json()["data"][0]["urls"]["original"]
    resp = await get(
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
    resp = await get(
        "https://api.lolicon.app/setu/v2",
        params={
            "r18": 1,
            "proxy": "false",
            "excludeAI": "true",
        },
    )
    resp.raise_for_status()
    url = resp.json()["data"][0]["urls"]["original"]
    resp = await get(
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
    resp = await get("https://www.loliapi.com/acg/pe/")
    resp.raise_for_status()
    return resp.content


def local_image():
    file = random.choice(os.listdir(IMG_DIR / "sbg"))
    img_url = IMG_DIR / "sbg" / file
    return Image.open(img_url).convert("RGB")


async def get_pic_from(src):
    if src == 'lolicon':
        return await lolicon()
    elif src == 'loli':
        return await loli()
    elif src == 'lolicon_r18':
        return await lolicon_r18()
    else:
        return local_image()
