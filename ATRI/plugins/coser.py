import time
from typing import Any, Tuple

import aiofiles
from httpx import AsyncClient

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import RegexGroup

from ATRI import TEMP_DIR
from ATRI.service import Service
from ATRI.log import log
from ATRI.utils.img_editor import get_image_bytes

plugin = Service("coser").document("三次元也不戳，嘿嘿嘿").type(Service.ServiceType.ENTERTAINMENT)

coser = plugin.on_regex(r"^(\d)?连?(cos|COS|coser|括丝)$",
                        docs="指令：\n?N连cos/coser\n示例：cos\n示例：5连cos （单次请求张数小于9）", priority=5, block=True)

# 纯cos，较慢:https://picture.yinux.workers.dev
# 比较杂，有福利姬，较快:https://api.jrsgslb.cn/cos/url.php?return=img
url = "https://picture.yinux.workers.dev"


@coser.handle()
async def _(reg_group: Tuple[Any, ...] = RegexGroup()):
    num = reg_group[0] or 1
    for _ in range(int(num)):
        path = TEMP_DIR / f"cos_cc{int(time.time())}.jpeg"
        try:
            async with AsyncClient(
                    follow_redirects=True,
                    timeout=10,
            ) as cli:
                resp = await cli.get(url)
                resp.raise_for_status()
                content = resp.content
                async with aiofiles.open(path, "wb") as wf:
                    await wf.write(content)
                await coser.send(MessageSegment.image(get_image_bytes(path)))
        except Exception as e:
            await coser.send("出错了，你cos给我看！")
            log.error(f"{e}:{e.args}")
