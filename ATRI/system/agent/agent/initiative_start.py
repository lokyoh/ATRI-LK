from __future__ import annotations

import re
import time
from dataclasses import dataclass

from ATRI.log import log

#: 允许主动开口的时段（含首不含尾）。与 `state.fatigue_for_hour` 的作息一致：
#: 02:00–06:00 是她的睡眠时间，那段时间她**不该**在群里说话。
SPEAK_HOUR_START = 8
SPEAK_HOUR_END = 24

#: 主动发起的最小间隔（秒）**默认值**。
#: 实际值来自配置 `proactive_start_interval`——用 2 小时作默认，
#: 是因为主动发起比「插话」打扰得多，保守起步。
MIN_INTERVAL = 2 * 60 * 60


def _min_interval() -> float:
    """从配置读最小间隔；读不到时用默认值。

    每次调用都读而不是缓存：配置改了应该立即生效，
    不该因为进程没重启而继续用旧值。
    """
    try:
        from ..config import config

        minutes = int(config.get("proactive_start_interval") or 120)
        return max(60.0, float(minutes) * 60.0)
    except Exception:
        return float(MIN_INTERVAL)


def _enabled() -> bool:
    """主动发起总开关。默认关——这个功能打扰性最强，必须显式打开。"""
    try:
        from ..config import config

        return bool(config.get("proactive_start_enabled"))
    except Exception:
        return False


def _speak_hours() -> tuple[int, int]:
    """当前配置的允许时段 (start, end)。读不到就用常量默认值。

    每次调用都读配置：改了要立即生效，不该等重启。
    """
    try:
        from ..config import config

        start = int(config.get("proactive_start_hour_start"))
        end = int(config.get("proactive_start_hour_end"))
        return max(0, min(23, start)), max(1, min(24, end))
    except Exception:
        return SPEAK_HOUR_START, SPEAK_HOUR_END


def in_speak_window(now: float | None = None) -> tuple[bool, str]:
    """现在是否在她的「醒着且适合说话」时段内。

    两个判据，任一不满足就沉默：

    1. **不在睡眠时段**（02:00–06:00）。与 `state.fatigue_for_hour` 的作息一致——
       那段时间她「本该在睡觉」，在群里说话会直接破坏人设。
       **这条是硬编码的**，不受配置影响：即使把时段设成 0-24，
       也不会在睡眠时间开口。
    2. **在配置的时段内**。`proactive_start_hour_start` ~ `_end` 之外一律不说：
       凌晨一点没人想被机器人搭话。

    用本地时区（`ATRI.utils.datetime.now`），与日程、疲劳曲线的口径一致，
    不要用 `time.localtime` 或 UTC，否则跨时区部署时会错位。

    ``now`` 是 Unix 时间戳（不是小时数）；传 None 表示「就是此刻」。
    测试需要固定时间时传具体时间戳。
    """
    try:
        if now is None:
            from ATRI.utils.datetime import now as _now

            hour = _now().hour
        else:
            from ATRI.utils.datetime import fromtimestamp

            hour = fromtimestamp(float(now)).hour
    except Exception:
        return True, ""

    if 2 <= hour < 6:
        return False, f"现在是 {hour} 点，她在睡觉"

    start_hour, end_hour = _speak_hours()
    if start_hour <= hour < end_hour:
        return True, ""
    return (
        False,
        f"现在是 {hour} 点，不在适合说话的时段（{start_hour}-{end_hour} 点）",
    )


#: 一条自我记忆被认为「值得说」的情绪阈值（绝对值）。
#: 低于它的事太琐碎，专门拿出来说会显得莫名其妙。
EMOTION_THRESHOLD = 0.5

#: 群「太冷」的判定：超过这么久没有任何消息，就不主动开口。
#: 群已经冷了不是因为「她该说话」，是因为大家都散了。
QUIET_GROUP_SECONDS = 3 * 24 * 60 * 60


@dataclass
class Opening:
    """一次主动开口的素材。"""

    text: str
    """要说的话。"""

    reason: str
    """为什么说——只用于日志与埋点，不进提示词。"""


def _load_spoken() -> list[str]:
    """取「已经说过的经历」列表，**按说出顺序**（旧 → 新）。

    用 list 而不是 set：`set` 无法保留顺序，淘汰时只能按字典序砍，
    那会导致「以『阿』开头的记忆永远留着、以『浙』开头的刚说完就被丢」，
    于是同一件事被反复说。顺序必须反映「什么时候说的」。
    """
    from .database import user_table

    rows = user_table.select("DATA", "ID='__atri_spoken__'")
    if not rows:
        return []
    try:
        import json

        data = json.loads(rows[0][0] or "{}")
        items = data.get("spoken") or []
        return [str(x) for x in items]
    except Exception:
        return []


def _save_spoken(spoken: list[str], keep: int = 40) -> None:
    """持久化已说过的标记，**淘汰最旧的**。

    保留更少（40）是为了让很久以前说过的事有机会再提一次——
    但淘汰必须按「说的先后」而不是字典序。
    """
    import json

    from .database import user_table

    payload = json.dumps({"spoken": list(spoken)[-keep:]}, ensure_ascii=False)
    try:
        if user_table.select("DATA", "ID='__atri_spoken__'"):
            user_table.update((("DATA",), (payload,)), "ID='__atri_spoken__'")
        else:
            user_table.insert("ID, DATA", ("__atri_spoken__", payload))
    except Exception as exc:
        log.warning(f"保存主动发起标记失败（忽略）：{exc}")


def pick_opening(chat_id: str | None = None) -> Opening | None:
    """挑一条「值得说」的经历。没有就返回 None。

    素材有两个来源，按优先级：

    1. **自我记忆**（`self_memory`）——她自己的经历。共 8 条上限，
       讲完就没了，但那是「有前因」的事，最像人。
    2. **记忆表里关于**该群**的高情绪条目**——从 `memories` 挑。
       它们本来用于检索，这里复用为「可以主动提起的事」。

    加第二个来源是因为：只有 8 条自我记忆时，讲完一轮就永久沉默。
    而记忆表随日常对话持续增长，能让她长期有话可说。

    :param chat_id: 限定从该群的记忆里挑（避免把 A 群的事说到 B 群）。
    """
    spoken = _load_spoken()
    spoken_set = set(spoken)
    picked = _pick_from_self(spoken_set)
    if picked is not None:
        return picked
    return _pick_from_memory_table(chat_id, spoken_set)


def _pick_from_self(spoken_set: set[str]) -> Opening | None:
    """从自我记忆里挑一条没说过的、情绪够强的。"""
    from .memory.persona import read_emotion, strip_timestamp
    from .user import get_self_memories

    try:
        memories = get_self_memories()
    except Exception:
        return None

    candidates: list[tuple[float, str]] = []
    for raw in memories:
        text = strip_timestamp(raw)
        if not text:
            continue
        if _key_of(text) in spoken_set:
            continue
        charge = abs(read_emotion(raw))
        if charge < EMOTION_THRESHOLD:
            continue
        candidates.append((charge, text))

    if not candidates:
        return None

    candidates.sort(key=lambda c: c[0])
    charge, text = candidates[-1]
    return Opening(text=text, reason=f"自我记忆情绪强度 {charge:.2f}")


def _pick_from_memory_table(
    chat_id: str | None, spoken_set: set[str]
) -> Opening | None:
    """从 `memories` 表挑一条高情绪、未说过的记忆。

    两道**安全约束**，缺一不可：

    1. **必须限定 chat_id**：记忆表里有各群的内容，不限定就会把 A 群的事
       拿去 B 群说。传 None 时不做补充。
    2. **只挑群级内容**（`subject` 为空）：带具体 `subject` 的条目是
       「某人告诉我的事」，主动当众说出来就是背叛信任。
       比如「userA 私聊告诉我的秘密」，在群里主动提起是绝对不能发生的。

    这也是为什么不做「跨用户记忆的主动开口」：检索场景下「用户问起才想起来」
    是合理的，但**主动说出来**是完全不同的事。
    """
    if not chat_id:
        return None

    from .memory.database import EMOTION_KEY

    try:
        from .memory.manage import memory_manager

        rows = memory_manager.database.all_memories()
    except Exception:
        return None

    scope = str(chat_id)
    candidates: list[tuple[float, str]] = []
    for row in rows:
        meta = row.get("metadata") or {}
        # 安全约束 2：带 subject 的一律跳过（那是关于某个人的私事）
        if str(meta.get("subject") or ""):
            continue
        # 安全约束 1：只挑本群或群级（无 chat_id）的内容
        row_chat = str(meta.get("chat_id") or "")
        if row_chat and row_chat != scope:
            continue
        content = str(row.get("content") or "").strip()
        if not content or _key_of(content) in spoken_set:
            continue
        try:
            charge = abs(float(meta.get(EMOTION_KEY) or 0.0))
        except (TypeError, ValueError):
            charge = 0.0
        if charge < EMOTION_THRESHOLD:
            continue
        candidates.append((charge, content))

    if not candidates:
        return None

    candidates.sort(key=lambda c: c[0])
    charge, text = candidates[-1]
    return Opening(text=text, reason=f"群记忆情绪强度 {charge:.2f}")


def should_speak_now(chat_id: str, *, now: float | None = None) -> tuple[bool, str]:
    """群层面是否适合开口。返回 (是否, 原因)。

    五道检查按**代价从低到高**排列，任一不过就沉默：

    1. 总开关是否打开（读配置，最便宜）；
    2. 现在是否在允许的时段（读时钟）；
    3. 距上次主动开口是否够久（读一次 DB）；
    4. 她是否烦躁（读内存状态）；
    5. 这个群最近是否还有人在说话（读历史文件，最贵）。

    先跑便宜的检查是刻意的：巡检每 30 分钟一次、群多的时候，
    没必要为每个群都去读历史文件。
    """
    from .state import get_state

    now = time.time() if now is None else now

    if not _enabled():
        return False, "主动开口未开启（proactive_start_enabled）"

    # 时段检查放在最前：如果现在是凌晨，后面的检查都没意义
    in_window, window_reason = in_speak_window(now)
    if not in_window:
        return False, window_reason

    interval = _min_interval()
    last = _last_spoken_at(chat_id)
    if last and (now - last) < interval:
        left = int((interval - (now - last)) / 60)
        return False, f"距上次主动开口还有 {left} 分钟"

    try:
        if get_state().irritation >= 0.5:
            return False, "她正在烦躁，不想说话"
    except Exception:
        pass

    if _group_is_quiet(chat_id, now):
        return False, "这个群已经冷了很久，此时开口很突兀"

    return True, ""


def _last_spoken_at(chat_id: str) -> float:
    """上次主动发起的时间戳（按群）。"""
    import json

    from .database import user_table

    try:
        rows = user_table.select("DATA", "ID='__atri_last_speak__'")
        if not rows:
            return 0.0
        data = json.loads(rows[0][0] or "{}")
        return float((data.get("at") or {}).get(str(chat_id)) or 0.0)
    except Exception:
        return 0.0


def _key_of(text: str) -> str:
    """记忆的去重键：取前 40 字。

    用前缀而不是整句，是因为同一条记忆在合并、二次渲染后可能尾部有细微差别；
    前缀足够区分，也不至于让键太长。
    """
    return str(text or "").strip()[:40]


def mark_spoken(chat_id: str, opening: Opening, *, now: float | None = None) -> None:
    """记录「这次真的说出口了」。

    只在实际发送成功后调用——决定了没说却记成说过，那条经历就永远沉默了。
    """
    import json

    from .database import user_table

    now = time.time() if now is None else now
    spoken = _load_spoken()
    key = _key_of(opening.text)
    if key not in spoken:
        spoken.append(key)
    _save_spoken(spoken)

    try:
        rows = user_table.select("DATA", "ID='__atri_last_speak__'")
        data = json.loads(rows[0][0]) if rows and rows[0][0] else {}
        at = dict(data.get("at") or {})
        at[str(chat_id)] = now
        payload = json.dumps({"at": at}, ensure_ascii=False)
        if rows:
            user_table.update((("DATA",), (payload,)), "ID='__atri_last_speak__'")
        else:
            user_table.insert("ID, DATA", ("__atri_last_speak__", payload))
    except Exception as exc:
        log.warning(f"记录主动发起时间失败（忽略）：{exc}")


def _group_is_quiet(chat_id: str, now: float) -> bool:
    """该群最近是否完全没有消息。

    读最近的历史文件：只要近 QUIET_GROUP_SECONDS 内有任何一条记录，
    就不算冷群。读不到历史时不判定为冷（宁可让她说，也不要因读失败永久沉默）。
    """
    try:
        from .history import history_logger

        recent = history_logger.get_recent_messages(chat_id, limit=1)
        if not recent:
            return True
        group_path = history_logger.path / str(chat_id)
        if not group_path.exists():
            return True
        newest = 0.0
        for f in sorted(group_path.glob("*.json"), reverse=True)[:2]:
            try:
                if f.stat().st_mtime > newest:
                    newest = f.stat().st_mtime
            except OSError:
                continue
        return bool(newest and (now - newest) > QUIET_GROUP_SECONDS)
    except Exception:
        return False


_REASON_PAREN = re.compile(r"^\s*[（(【\[].*?[）)】\]]\s*$")
_SPEAKER_PREFIX = re.compile(r"^\s*(亚托莉|ATRI|atri)\s*[:：]\s*")
_MD_MARKS = re.compile(r"(\*\*|__|~~|`)")


def _clean_opening_text(content: str) -> str | None:
    """把模型输出清洗成可以直接发的一句话。清洗不掉就返回 None（不发）。

    这一步是必要的防御：主动开口是**无人触发**的发言，说错话代价极高。
    宁可这一次不说，也不要把「（因为想起了昨天的事）」这种元叙述发到群里。
    """
    text = (content or "").strip()
    if not text:
        return None
    for line in text.splitlines():
        candidate = line.strip()
        if candidate:
            text = candidate
            break
    text = _SPEAKER_PREFIX.sub("", text).strip()
    text = text.strip('"').strip("「」").strip("'").strip()
    text = _MD_MARKS.sub("", text).strip()
    if not text or _REASON_PAREN.match(text):
        return None
    if text in {"不说", "不说。"}:
        return None
    if len(text) > 120:
        return None
    return text


_PLACEHOLDER_USER = "__initiative__"


class _CaptureSender:
    """捕获 `send_message` 发出的内容，而不是真的发出去。

    为什么需要它：走完整 thinking 后，输出是通过 `send_message` 工具产生的。
    但我们不能在思考阶段真的发消息——还要过一遍长度/格式校验，
    并且要和 `mark_spoken`、telemetry 一起构成一个原子操作。

    实现上 duck-typing 对齐 `ChatSender` 的接口即可（`send` / `sent_count`），
    不必继承——继承会带入真实的 matcher 依赖。
    """

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.sent_count = 0

    async def send(self, message):
        text = str(message or "").strip()
        if text:
            self.sent.append(text)
            self.sent_count += 1

    async def finish(self, message=None):
        if message is not None:
            await self.send(message)


def _build_initiative_context(chat_id: str, bot, opening: Opening):
    """构造「没有对话人」的思考上下文。

    - `ChatArgs`：user_id 用占位符，这样 `get_user_info` 读到空档案；
    - `History`：一条代表「她刚想起了这件事」的虚拟消息；
    - `ChatMessage`：空队列，避免误读真实待处理消息。
    """
    from .history import ChatHistoryManager, History, LLMMessage, MessageSegment
    from .model import ChatArgs
    from .temp_his import ChatMessage

    args = ChatArgs(bot, chat_id, _PLACEHOLDER_USER, False, True)

    synthetic = History(None, chat_id)
    synthetic.sender = None
    synthetic.message = LLMMessage(chat_id, [MessageSegment(opening.text)])

    chat_msg = ChatMessage(synthetic, [], "", [], args)

    chat_history = ChatHistoryManager.get_history(chat_id)
    history_list = chat_history.get_history()
    fallback = chat_history.fallback_history

    return args, synthetic, history_list, fallback, chat_msg


async def compose_opening(chat_id: str, bot, opening: Opening) -> str | None:
    """用**完整的 thinking 链路**生成一句话。

    :return: 要说的话；模型选择不说、或输出不合规时返回 None。
    """
    from ..brain import ThinkingModel
    from ..llm import ModelType, llm_manager

    if not llm_manager.has_type(ModelType.CHAT):
        return None

    sender = _CaptureSender()
    try:
        args, synthetic, history_list, fallback, chat_msg = _build_initiative_context(
            chat_id, bot, opening
        )
        await ThinkingModel().thinking(
            args,
            synthetic,
            history_list,
            "",
            fallback,
            chat_msg,
            sender,
            initiative=True,
        )
    except Exception as exc:
        log.warning(f"主动开口生成失败：{exc}")
        return None
    if not sender.sent:
        return None
    return _clean_opening_text(sender.sent[0])


def _group_id(chat_id: str):
    """把 chat_id 转成 OneBot 需要的群号。

    纯数字才转 int；否则原样返回。

    为什么不能直接 ``int()``：OneBot v11 的群号是数字，但 QQ 官方适配器用的是
    **含字母的 openid**。硬转会让这类群永远发不出去，而且异常被最外层吞掉，
    表现成「功能开启了但从来不说话」——极难排查。
    """
    text = str(chat_id).strip()
    return int(text) if text.isdigit() else text


def _record_to_history(chat_id: str, text: str) -> None:
    """把主动说出的话写进对话历史。

    为什么必须做：不记录的话，她下一轮对话时**不知道自己刚说过什么**，
    会重复提同一件事、或与刚才的话自相矛盾。人不会这样。

    实现要点：

    - 用 `add_agent_message`，它同时写盘（`user_id=None` = 机器人）和进内存队列，
      与正常回复走同一条路径；
    - **不设 `reply_to_mid`**：主动开口不是在回复某条消息，设了会让历史里
      出现「你在回复 mid:x」这种不存在的关联。

    失败不影响已发出的消息——记录是旁路，不能因为写盘失败就报发送失败。
    """
    try:
        from .history import ChatHistoryManager, History, LLMMessage, MessageSegment

        reply = History(None, chat_id)
        reply.message = LLMMessage(chat_id, [MessageSegment(text)])
        ChatHistoryManager.get_history(chat_id).add_agent_message(reply, text)
    except Exception as exc:
        log.warning(f"主动发言写入历史失败（消息已发出，忽略）：{exc}")


async def try_speak(chat_id: str, bot) -> str | None:
    """某个群尝试主动开口一次。返回实际说出的话（没说则 None）。

    完整链路：群层面检查 → 挑一条经历 → 生成措辞 → 发送 → 记账。
    任何一步失败都安静返回，不抛异常——这是旁路功能，
    不能因为它出错影响正常回复。
    """
    from .telemetry import telemetry

    try:
        ok, reason = should_speak_now(chat_id)
        if not ok:
            log.debug(f"群 {chat_id} 不主动开口：{reason}")
            return None

        opening = pick_opening(chat_id)
        if opening is None:
            return None

        text = await compose_opening(chat_id, bot, opening)
        if not text:
            log.info(f"群 {chat_id} 的主动发言被模型判断为不合适，跳过")
            return None

        await bot.send_group_msg(group_id=_group_id(chat_id), message=text)
        _record_to_history(chat_id, text)
        mark_spoken(chat_id, opening)
        telemetry.record(
            "initiative_start",
            chat_id=chat_id,
            reason=opening.reason,
            text_len=len(text),
        )
        log.info(f"主动开口（群 {chat_id}）：{text}")
        return text
    except Exception as exc:
        log.warning(f"群 {chat_id} 主动发起失败：{exc}")
        return None


def whitelisted_groups() -> list[str]:
    """取允许**主动开口**的群。"""
    try:
        from ..config import config

        groups = config.get("proactive_start_whitelist") or []
        return [str(g).strip() for g in groups if str(g).strip()]
    except Exception as exc:
        log.warning(f"读取主动发起白名单失败（按空处理）：{exc}")
        return []


async def patrol_once() -> int:
    """巡检一轮：对白名单群尝试主动开口。返回实际发出的条数。

    一轮里**最多开口一次**：一次巡检在多个群同时说话不是「她开口了」，
    是广播。某个群说成了就结束本轮。

    带**重入保护**：一次巡检里要调模型，慢的时候可能超过 30 分钟的调度间隔。
    没有锁的话两轮会并发跑，各自判断「上次发言已经够久」，
    然后几乎同时发两条——冷却检查形同虚设。
    """
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot as OneBot

    global _patrol_running
    if _patrol_running:
        log.warning("上一轮主动发起巡检还没结束，跳过本轮")
        return 0
    _patrol_running = True
    try:
        return await _patrol_body(nonebot, OneBot)
    finally:
        _patrol_running = False


_patrol_running = False


def _pick_bot(bots: list):
    """从已连接的 OneBot 实例里选一个用来发送。

    :return: (选中的 bot, 说明)。没得选时 bot 为 None。
    """
    try:
        from ..config import config

        wanted = str(config.get("proactive_start_bot") or "").strip()
    except Exception:
        wanted = ""

    if not wanted:
        return (bots[0] if bots else None), ""

    for b in bots:
        if str(getattr(b, "self_id", "")) == wanted:
            return b, ""
    return None, f"配置的 bot {wanted} 未连接（当前已连接 {len(bots)} 个实例）"


async def _patrol_body(nonebot_module, bot_type) -> int:
    groups = whitelisted_groups()
    if not groups:
        return 0

    bots = [b for b in nonebot_module.get_bots().values() if isinstance(b, bot_type)]
    if not bots:
        return 0
    bot, why = _pick_bot(bots)
    if bot is None:
        log.warning(f"主动发起：{why}")
        return 0

    for group_id in groups:
        spoken = await try_speak(group_id, bot)
        if spoken:
            return 1
    return 0


def register_patrol(service) -> None:
    """把巡检挂到调度器上。由 agent 的 `service.init()` 调用。

    间隔 30 分钟，而群级冷却（`MIN_INTERVAL`）是 2 小时——
    巡检只是「看看有没有机会」，真正的开口频率由 `should_speak_now` 决定。
    """
    try:
        service.scheduler_jobs().add_job(
            patrol_once,
            name="agent-proactive-opening",
            trigger="interval",
            minutes=30,
            misfire_grace_time=300,
            use_log=True,
        )
        log.info("已注册主动发起巡检（每 30 分钟）")
    except Exception as exc:
        log.warning(f"注册主动发起巡检跳过：{exc}")
