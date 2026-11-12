"""发送 Markdown 卡片的共享实现（Outbox 与降级直发都用它）。"""

from __future__ import annotations

import time

from ATRI.message import QQMessage, img_msg
from ATRI.system.htmlrender import md_to_pic


async def send_markdown_document(data, payload: dict) -> str:
    """把 ``{title, body, image}`` 渲染成卡片/图片并发送，返回 md 文件名。

    图片只使用 payload 里已经确定的那张；正文里出现的图片链接由
    `reply_pipeline.parse_markdown` 提前删掉，这里不再处理。
    """
    from ..service import plugin

    body = str(payload.get("body") or "").strip()
    title = str(payload.get("title") or "").strip()
    image = str(payload.get("image") or "").strip()
    document = f"# {title}\n\n{body}" if title else body
    if image:
        document += f"\n\n![]({image})"

    md_file = f"{time.time()}.md"
    md_path = plugin.get_path() / "chat" / str(data.chat_id) / "md" / md_file
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(document, encoding="utf-8")

    if getattr(data.sender, "mtype", 0) == 1:
        await data.sender.send(QQMessage.md_msg(document))
    else:
        await data.sender.send(img_msg(await md_to_pic(document)))

    md_ref = getattr(data.sender, "md_refs", None)
    if isinstance(md_ref, list):
        md_ref.append((md_file, document))
    return md_file
