import json
import time
import uuid

from ATRI.log import log
from ATRI.scheduler import ATRIScheduler

from .initiative_start import clean_opening_text, has_dialog_model, run_agent_once

#: 单次任务的最小提前量（秒）。太短会导致「刚设定就到点」，
#: 而调度器本身有几十毫秒的启动开销。
MIN_DELAY = 5.0

#: 单个会话最多允许挂多少个未完成任务，防止一次定一堆把调度器塞满。
MAX_TASKS_PER_CHAT = 10

#: 任务名前缀。核心调度器按 `<service>:<name>` 入库，
#: 这里统一加前缀，避免与将来其他使用者的任务重名。
_JOB_PREFIX = "agent:"


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _prune(task: dict) -> dict:
    """删掉空值字段，**不留 `""`**。

    空串和「这个字段不存在」在语义上是一回事，但存下来会骗人：任务 JSON 里
    明明白白写着 `"bot_id": ""`，看的人会以为是「记录了但是空」，进而去猜
    「为什么记了个空 bot」，而真相只是「没有这个信息」。存的时候就没有这一
    项，读的时候用 `.get()` 兜底，两边都干净。
    """
    return {k: v for k, v in task.items() if v is not None and v != ""}


async def _load_rows() -> list:
    """读出库里所有 **agent** 的任务行。"""
    try:
        from ATRI.scheduler.model import SchedulerTask

        return await SchedulerTask.filter(service="ATRI").all()
    except Exception as exc:
        log.debug(f"读取定时任务失败：{exc}")
        return []


def _row_to_task(row) -> dict:
    try:
        spec = json.loads(row.trigger_args or "{}")
    except (TypeError, ValueError):
        spec = {}
    try:
        payload = json.loads(row.job_kwargs or "{}")
    except (TypeError, ValueError):
        payload = {}
    task = payload.get("task") if isinstance(payload, dict) else None
    if not isinstance(task, dict):
        task = {}
    task.setdefault("id", row.name.removeprefix(_JOB_PREFIX))
    task.setdefault("name", row.name)
    task.setdefault("kind", row.trigger)
    task.setdefault("spec", spec)
    task.setdefault("run_at", 0)
    task.setdefault("chat_id", "")
    task.setdefault("message", "")
    task.setdefault("created_at", float(row.created_at or 0))
    #: 老任务可能没有 bot_id（创建时还没记这个字段），此时**不补空串**，
    #: 让「没记」和「记了个空」在数据上是同一件事。
    return _prune(task)


async def list_tasks(
    chat_id: str | None = None, bot_id: str | None = None
) -> list[dict]:
    """列出任务。给了 chat_id 就只列这个会话的，给了 bot_id 就只列这个 bot 的。

    **归属判断看 `row.name`（数据库列），不看 task 字典里的 `name`**：
    后者是给用户看的描述（「提醒我喝水」），与「哪个服务的任务」无关。
    之前用 `task["name"].startswith("agent:")` 过滤，导致所有任务都被丢弃——
    `create_once` 构造的 task 里 `name` 就是用户内容，永远不以 `agent:` 开头。

    :param bot_id: 同一个群里可能同时有好几个 bot（同一份库），任务只属于
        **创建它的那个 bot**。传了才按 bot 过滤；不传是「全都要」，
        只有排障场景才该这么用。
    """
    tasks = []
    for row in await _load_rows():
        if not str(getattr(row, "name", "")).startswith(_JOB_PREFIX):
            continue
        tasks.append(_row_to_task(row))
    if chat_id is not None:
        tasks = [t for t in tasks if t.get("chat_id", "") == str(chat_id)]
    if bot_id is not None:
        tasks = [t for t in tasks if t.get("bot_id", "") == str(bot_id)]
    return sorted(tasks, key=lambda t: t.get("created_at", 0))


async def get_task(task_id: str, bot_id: str | None = None) -> dict | None:
    for t in await list_tasks(bot_id=bot_id):
        if t.get("id") == task_id:
            return t
    return None


async def count_tasks(chat_id: str, bot_id: str | None = None) -> int:
    return len(await list_tasks(chat_id, bot_id))


async def _register(task: dict) -> bool:
    """把任务注册到核心调度器，并**可靠地**写进库。

    用 `add_job_async`：它会 await 持久化步骤。
    同步版 `add_job(persist=True)` 把写库丢到后台任务，在 AnyIO worker
    线程里 `get_event_loop()` 直接抛错、协程从未执行，结果是
    「调度器里有、库里没有」，重启后恢复不出来。

    写库失败时会撤回已注册的作业并抛错——宁可创建失败，
    也不要给用户一个「看起来成功、重启后就没了」的任务。
    """
    name = f"{_JOB_PREFIX}{task['id']}"
    try:
        if task.get("kind") == "once":
            from datetime import datetime

            from apscheduler.triggers.date import DateTrigger

            run_at = float(task.get("run_at") or 0)
            if run_at - _now() <= 0:
                log.debug(f"任务 {task.get('name')} 已过期，跳过注册")
                return False
            await ATRIScheduler.add_job_async(
                _send,
                name,
                DateTrigger(run_date=datetime.fromtimestamp(run_at)),
                kwargs={"task": task},
                misfire_grace_time=300,
                persist=True,
            )
            return True
        await ATRIScheduler.add_job_async(
            _send,
            name,
            task.get("kind"),
            kwargs={"task": task},
            misfire_grace_time=300,
            persist=True,
            **(task.get("spec") or {}),
        )
        return True
    except Exception as exc:
        log.warning(f"注册定时任务 {task.get('name')} 失败：{exc}")
        return False


async def _unregister(task_id: str) -> None:
    """撤下作业并删掉库里的定义；失败不抛。

    **不能只靠调度器**：补发路径（`restore` → `_run_expired`）从没把任务注册
    进调度器，`has_job` 为假，只摘内存的话库里那一行会一直留着——下次启动又
    被当成过期任务补发一遍。所以兜底直接删表里那一行（键就是 `ATRI:agent:<id>`）。
    """
    name = f"{_JOB_PREFIX}{task_id}"
    try:
        if ATRIScheduler.has_job(name):
            ATRIScheduler.remove_job(name)
            return
    except Exception as exc:
        log.debug(f"撤下定时任务失败：{exc}")
    try:
        from ATRI.scheduler.model import SchedulerTask

        await SchedulerTask.filter(key=f"ATRI:{name}").delete()
    except Exception as exc:
        log.debug(f"删除任务记录 {name} 失败：{exc}")


#: 等 bot 上线的上限（秒）。机器人刚启动时 WebSocket 反向连接还没建立，
#: `get_bots()` 是空的——此时直接发只会得到「没有可用的 bot」。
#: 任务是真的到点了，等几秒比丢掉强。
WAIT_BOT_TIMEOUT = 30.0

#: 轮询间隔（秒）
_WAIT_BOT_INTERVAL = 1.5


def _usable_bots() -> list:
    """当前已连接、能发群消息的 bot（OneBot 与 QQ 官方都收）。"""
    import nonebot

    from .sender import bot_mtype

    bots = []
    try:
        connected = list(nonebot.get_bots().values())
    except Exception:
        return []
    for b in connected:
        try:
            from nonebot.adapters.onebot.v11 import Bot as OneBot

            if isinstance(b, OneBot):
                bots.append(b)
                continue
        except Exception:
            pass
        if bot_mtype(b) == 1:
            bots.append(b)
    return bots


async def _wait_for_bots() -> list:
    """等到至少有一个可用的 bot，最多等 `WAIT_BOT_TIMEOUT` 秒。

    定时任务常在启动阶段被恢复，那时 bot 还没连上。
    之前的实现直接 `if not bots: return`，任务就此丢掉——
    用户看到的是「到点没反应」，日志里只有一行 warning。
    """
    import asyncio

    bots = _usable_bots()
    if bots:
        return bots
    waited = 0.0
    while waited < WAIT_BOT_TIMEOUT:
        await asyncio.sleep(_WAIT_BOT_INTERVAL)
        waited += _WAIT_BOT_INTERVAL
        bots = _usable_bots()
        if bots:
            log.info(f"定时任务等待 {waited:.1f} 秒后 bot 上线")
            return bots
    return []


async def _send(task: dict) -> None:
    """到点执行：**唤起 Agent 思考**，由她自己决定说什么。

    **没有去向的任务一律不调用**：`bot_id` 缺失意味着这条任务无法归属到
    任何一个 bot，发出去就是「不知道谁在哪个群说话」。这种任务不猜、不试，
    直接作废并删掉。

    为什么不是直接发一句固定文本：定时任务的价值在于「到点提醒她该做这件事」，
    而不是「到点念一句录音」。让她重新思考一次，能结合当下的心情、日程、
    刚发生的事来说——「该提醒主人喝水了」和「主人你三小时没喝水了诶」是两种东西。

    真正发送由 `BotChatSender` 完成：思考链路里的 `send_message` 会经它落到群里，
    并自动写入聊天历史（与正常对话同一条路径）。
    """
    chat_id = str(task.get("chat_id") or "")
    cue = str(task.get("message") or "")
    if not chat_id or not cue:
        return

    wanted = str(task.get("bot_id") or "")
    if not wanted:
        # 没有去向就不准调用：这条任务无法归属到任何一个 bot，发出去就是
        # 「不知道谁在哪个群说话」。不猜、不试，直接作废并删掉——
        # 留着它只会在每次启动时被当成「过期任务」补发一遍。
        log.warning(f"定时任务「{task.get('name')}」没有去向（无 bot_id），已作废")
        await _unregister(task.get("id") or "")
        return

    from .sender import is_chat_gone

    if is_chat_gone(chat_id):
        # 这个群已经确认发不进去了（注销/被踢）。再唤起一次 Agent 只会重复
        # 生成内容、重复失败，还会让用户以为「她一直在说话」。
        log.warning(f"定时任务「{task.get('name')}」的会话 {chat_id} 已不可用，跳过")
        return

    bots = await _wait_for_bots()
    if not bots:
        log.warning(
            f"定时任务 {task.get('name')} 到点但等了 {WAIT_BOT_TIMEOUT:.0f} 秒"
            "仍没有可用的 bot，放弃本次"
        )
        return
    bot = None
    for b in bots:
        if str(getattr(b, "self_id", "")) == wanted:
            bot = b
            break
    if bot is None:
        # **不换 bot**。会话是「某个 bot + 某个群」的组合：群号只对创建
        # 这个任务的那个 bot 有意义——别的 bot 未必在那个群，甚至群标识
        # 的形态都不同（QQ 官方是 group_openid，OneBot 是数字群号）。
        # 换一个 bot 去发，轻则「请求的资源不存在(用户/群已注销)」，
        # 重则把话说进不相关的群。到点没发出去，也比发错地方好。
        log.warning(
            f"定时任务「{task.get('name')}」原定 bot {wanted} 不在线，"
            "放弃本次（换 bot 发往同一个群不安全）"
        )
        return
    try:
        await _run_agent(
            chat_id,
            bot,
            cue,
            task.get("name") or "",
            user_id=str(task.get("user_id") or ""),
        )
    except Exception as exc:
        log.warning(f"定时任务「{task.get('name')}」唤起 Agent 失败：{exc}")
        return
    from .sender import is_chat_gone as _gone

    if _gone(chat_id):
        # 思考过程中发送失败，且平台明确表示这个会话没了。日志要如实说
        # 「没发出去」，而不是只报「已唤起 Agent」——后者会让排查的人以为
        # 消息发出去了，只是没看到内容。
        log.warning(
            f"定时任务「{task.get('name')}」已唤起 Agent，但会话 {chat_id} "
            "已不可用，本次内容没有发出"
        )
    else:
        log.info(f"定时任务「{task.get('name')}」已唤起 Agent（会话 {chat_id}）")
    if task.get("kind") == "once":
        await _unregister(task.get("id") or "")


async def _run_agent(
    chat_id: str, bot, cue: str, name: str = "", user_id: str = ""
) -> None:
    """用一次完整的思考来回应定时任务的提醒。

    :param user_id: 委托这条提醒的人。传进去之后，提示词里会带上
        「和这个人的关系 / 关于他的记忆 / 用户画像」——
        定时任务本来就是对某个人说的，不该当成「没有目标用户」。
    """
    if not has_dialog_model():
        log.debug("没有可用的对话模型，定时任务跳过唤起 Agent")
        return

    from .sender import BotChatSender

    trigger = f"（定时提醒）{cue}"
    sender = BotChatSender(bot, chat_id, cleaner=clean_opening_text)
    await run_agent_once(
        chat_id, bot, trigger, sender, mode="scheduled", user_id=user_id
    )
    if sender.sent_count:
        from .history import log_bot_message
        from .initiative_start import readable_history_text

        for text in sender.sent:
            plain = readable_history_text(text)
            if plain:
                log_bot_message(chat_id, plain)
        for md_file, _ in sender.md_refs:
            log_bot_message(chat_id, f"[md:{md_file}]")


async def create_once(
    chat_id: str,
    message: str,
    delay_seconds: float,
    name: str = "",
    bot_id: str = "",
    user_id: str = "",
) -> str:
    """创建一个「N 秒后说这句话」的任务。返回任务 id。

    :param bot_id: 任务的**去向**：到点时用哪个 bot 发、发到哪个会话。
        必填——不知道去向的任务到点只会乱猜 bot，不如当场拒绝。
    :param user_id: 委托这条提醒的人。到点时把它当作对话人，
        这样她能带着「和这个人的关系、关于他的记忆」来说话，
        而不是在一个没有对象的空上下文里硬找话。
    :raises ValueError: 参数不合法或超出数量上限。
    """
    bot_id = str(bot_id or "").strip()
    if not bot_id:
        raise ValueError("任务必须指定去向（哪个 bot 发），没有去向不能创建")
    if delay_seconds < MIN_DELAY:
        raise ValueError(f"至少要 {MIN_DELAY:.0f} 秒之后")
    if not (message or "").strip():
        raise ValueError("要说的内容不能为空")
    if await count_tasks(chat_id, bot_id) >= MAX_TASKS_PER_CHAT:
        raise ValueError(f"这个会话已经有 {MAX_TASKS_PER_CHAT} 个任务了，先取消一些")

    task = _prune(
        {
            "id": _new_id(),
            "name": (name or message)[:30],
            "kind": "once",
            "spec": {},
            "run_at": _now() + float(delay_seconds),
            "chat_id": str(chat_id),
            "message": message.strip(),
            "created_at": _now(),
            "bot_id": bot_id,
            "user_id": str(user_id or ""),
        }
    )
    if not await _register(task):
        raise ValueError("任务注册失败，请稍后再试")
    return task["id"]


async def create_recurring(
    chat_id: str,
    message: str,
    *,
    kind: str,
    spec: dict,
    name: str = "",
    bot_id: str = "",
    user_id: str = "",
) -> str:
    """创建一个周期性任务（每天/每隔多久说固定的话）。

    :param kind: ``cron`` 或 ``interval``
    :param spec: 触发器参数，如 ``{"hour": 8, "minute": 0}``
    :param bot_id: 任务的**去向**，必填（同 `create_once`）。
    """
    bot_id = str(bot_id or "").strip()
    if not bot_id:
        raise ValueError("任务必须指定去向（哪个 bot 发），没有去向不能创建")
    if kind not in ("cron", "interval"):
        raise ValueError("kind 只能是 cron 或 interval")
    if not (message or "").strip():
        raise ValueError("要说的内容不能为空")
    if not spec:
        raise ValueError("缺少触发时间参数")
    if await count_tasks(chat_id, bot_id) >= MAX_TASKS_PER_CHAT:
        raise ValueError(f"这个会话已经有 {MAX_TASKS_PER_CHAT} 个任务了，先取消一些")

    task = _prune(
        {
            "id": _new_id(),
            "name": (name or message)[:30],
            "kind": kind,
            "spec": spec,
            "run_at": 0,
            "chat_id": str(chat_id),
            "message": message.strip(),
            "created_at": _now(),
            "bot_id": bot_id,
            "user_id": str(user_id or ""),
        }
    )
    if not await _register(task):
        raise ValueError("任务注册失败，请检查时间参数是否合法")
    return task["id"]


async def cancel(
    task_id: str, chat_id: str | None = None, bot_id: str | None = None
) -> bool:
    """取消任务。

    给了 chat_id 就只允许取消该会话自己的任务；给了 bot_id 就只允许取消
    **本机器人**的任务——同一个群里可能有好几个 bot，任务是谁安排的，
    就只有谁有权动它。
    """
    task = await get_task(task_id, bot_id)
    if task is None:
        return False
    if chat_id is not None and task.get("chat_id", "") != str(chat_id):
        return False
    await _unregister(task_id)
    return True


def _when_text(task: dict) -> str:
    """把触发时间渲染成人话。"""
    kind = task.get("kind")
    if kind == "once":
        left = max(0, int(float(task.get("run_at") or 0) - _now()))
        if left >= 3600:
            return f"{left // 3600} 小时后"
        if left >= 60:
            return f"{left // 60} 分钟后"
        return f"{left} 秒后"
    if kind == "cron":
        spec = task.get("spec") or {}
        hour = spec.get("hour")
        minute = spec.get("minute", 0)
        if hour is not None:
            try:
                return f"每天 {int(hour):02d}:{int(minute):02d}"
            except (TypeError, ValueError):
                return "按 cron 规则"
        return "按 cron 规则"
    spec = task.get("spec") or {}
    try:
        secs = (
            float(spec.get("seconds") or 0)
            or float(spec.get("minutes") or 0) * 60
            or float(spec.get("hours") or 0) * 3600
        )
    except (TypeError, ValueError):
        secs = 0
    return f"每 {int(secs)} 秒" if secs else "按间隔规则"


#: 任务类型的中文说法，给管理指令与 WebUI 看
KIND_LABEL = {"once": "一次性", "cron": "每天", "interval": "周期"}
_KIND_LABEL = KIND_LABEL


def when_text(task: dict) -> str:
    """触发时间的人话描述。对外入口（WebUI 与指令共用同一套说法）。"""
    return _when_text(task)


def describe(task: dict) -> str:
    """把任务渲染成一句人能看懂的话，供模型回复用户。"""
    return f"[{task.get('id', '')}] {_when_text(task)}「{task.get('message', '')}」"


def describe_line(task: dict) -> str:
    """管理指令用的单行摘要：`[id] 时间 · 类型 · 委托人「内容」`。

    比 `describe` 多了「是哪种任务」「谁委托的」——在群里看列表时，
    这两条决定了「这条要不要留」。
    """
    parts = [f"[{task.get('id', '')}]", _when_text(task)]
    label = _KIND_LABEL.get(str(task.get("kind") or ""))
    if label:
        parts.append(label)
    user_id = str(task.get("user_id") or "")
    if user_id:
        # QQ 官方适配器的 user_id 是几十个字符的 openid，整条列出来会把
        # 一行撑爆。截断不影响辨认——同一个人的前缀是稳定的。
        if len(user_id) > 16:
            user_id = f"{user_id[:8]}…"
        parts.append(f"委托人 {user_id}")
    return " · ".join(parts) + f"「{task.get('message', '')}」"


async def _clean_stored_tasks() -> int:
    """整理库里的任务：**删掉没有去向的一律作废**，并抹掉其余任务里的空值。

    旧版本把「没记 bot」写成 ``"bot_id": ""``。这种任务到了也没有可归属的
    bot，不该执行，也不该留在库里等下一次启动再被补发一遍——所以直接删。
    剩下带去向的，顺手把空值字段抹掉，让数据和语义一致。
    """
    try:
        from ATRI.scheduler.model import SchedulerTask
    except Exception as exc:
        log.debug(f"读不到任务表，跳过任务整理：{exc}")
        return 0
    changed = 0
    dropped = 0
    for row in await _load_rows():
        if not str(getattr(row, "name", "")).startswith(_JOB_PREFIX):
            continue
        try:
            payload = json.loads(row.job_kwargs or "{}")
        except (TypeError, ValueError):
            continue
        task = payload.get("task") if isinstance(payload, dict) else None
        if not isinstance(task, dict):
            continue
        if not str(task.get("bot_id") or "").strip():
            try:
                await SchedulerTask.filter(key=row.key).delete()
            except Exception as exc:
                log.debug(f"删除无去向任务 {row.key} 失败：{exc}")
                continue
            dropped += 1
            continue
        cleaned = _prune(task)
        if cleaned == task:
            continue
        payload["task"] = cleaned
        try:
            await SchedulerTask.filter(key=row.key).update(
                job_kwargs=json.dumps(payload, ensure_ascii=False)
            )
        except Exception as exc:
            log.debug(f"清理任务 {row.key} 的空值字段失败：{exc}")
            continue
        changed += 1
    if dropped:
        log.info(f"已删除 {dropped} 个没有去向的旧任务（无 bot_id，不会执行）")
    if changed:
        log.info(f"已清掉 {changed} 个旧任务里的空值字段")
    return changed + dropped


async def restore_agent_tasks() -> int:
    """启动时把库里属于 agent 的任务重新注册。返回成功数量。

    **恢复动作交给核心 `ATRIScheduler.restore()`**：任务定义是它写进库的，
    读回来也该由它负责。这里只做两件 agent 侧的事：

    1. 提供「任务名 → 可调用对象」映射（函数对象无法序列化，只能运行时给）；
    2. 任务名统一带 `_JOB_PREFIX`，避免与其他服务重名。

    之前这里自己遍历库、自己 `_register`，与核心调度器的持久化逻辑重复，
    而且 `load_persisted()` 从未被调用——「会存不会读」。
    """
    try:
        await _clean_stored_tasks()
    except Exception as exc:
        log.debug(f"清理旧任务空值失败（不影响恢复）：{exc}")
    try:
        from ATRI.scheduler import restore
    except Exception as exc:
        log.warning(f"核心调度器不可用，跳过任务恢复：{exc}")
        return 0
    funcs: dict[str, object] = {}
    for task in await list_tasks():
        funcs[f"{_JOB_PREFIX}{task['id']}"] = _send
    if not funcs:
        return 0
    restored = await restore(funcs)
    if restored:
        log.info(f"已恢复 {restored} 个 Agent 定时任务")
    return restored
