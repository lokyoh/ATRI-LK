"""冷却依赖：与 nonebot 的 ``Cooldown`` 同语义，但提示语每次随机且走统一渲染。

## 为什么不用 ``nonebot.adapters.onebot.v11.helpers.Cooldown``

它的 ``prompt`` 是**装饰器求值时**就固定下来的字符串：

    Cooldown(10, prompt=_cooldown_notice())

``_cooldown_notice()`` 只在模块导入时被调用一次，于是进程活多久，那句「慢一点」
就固定多久——``texts.yaml`` 里明明列了六句，玩家永远只看到同一句。而且它直接
``matcher.finish(prompt)``，绕过了 :mod:`ATRI.system.lkpet.utils.reply` 的适配器
分流，QQ 端拿到的是一条纯文本。

这里把「选哪句」推迟到**真正触发冷却时**，并用 ``reply.safe_render`` 输出：
QQ 端是 markdown，OneBot 端是纯文本，文案仍然只有一份。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from random import choice

from nonebot.adapters import Event
from nonebot.adapters.onebot.v11.helpers import CooldownIsolateLevel
from nonebot.matcher import Matcher
from nonebot.params import Depends


def cooldown(
    seconds: float,
    *,
    notices: Iterable[str] | None = None,
    isolate_level: CooldownIsolateLevel = CooldownIsolateLevel.USER,
    parallel: int = 1,
) -> object:
    """冷却 ``seconds`` 秒；仍在冷却中时随机发一句 ``notices``。

    隔离级别与 ``Cooldown`` 一致（默认按用户），``parallel`` 控制同一个人可同时
    进行几条。取值全部来自 ``texts.yaml`` 的 ``cooldown.notices``，缺配置时退回
    一句中性提示，**绝不允许提示为空**——空消息在 QQ 端会被渲染成「该消息类型
    暂不支持查看」。
    """
    from asyncio import get_running_loop

    from . import reply

    phrases = [item for item in (notices or []) if item and item.strip()]
    running: defaultdict[str, int] = defaultdict(lambda: parallel)

    def _release(key: str) -> None:
        running[key] += 1
        if running[key] >= parallel:
            del running[key]

    async def dependency(matcher: Matcher, event: Event) -> None:
        loop = get_running_loop()

        group_id = getattr(event, "group_id", None)
        if group_id:
            group_id = str(group_id)
        try:
            user_id = event.get_user_id()
        except Exception:  # pragma: no cover - 取不到就用群维度兜底
            user_id = None

        if isolate_level is CooldownIsolateLevel.GROUP:
            key = group_id or user_id
        elif isolate_level is CooldownIsolateLevel.USER:
            key = user_id
        elif isolate_level is CooldownIsolateLevel.GROUP_USER:
            key = f"{group_id}_{user_id}" if group_id else user_id
        else:
            key = CooldownIsolateLevel.GLOBAL.name

        if not key:
            return

        if running[key] <= 0:
            notice = choice(phrases) if phrases else "慢一点，先歇会儿。"
            await matcher.finish(reply.safe_render(event, notice))
            return

        running[key] -= 1
        loop.call_later(seconds, lambda: _release(key))

    return Depends(dependency)
