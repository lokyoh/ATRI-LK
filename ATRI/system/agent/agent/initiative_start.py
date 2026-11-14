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


def pick_opening() -> Opening | None:
    """挑一条「值得说」的素材：自我记忆优先，当天日程兜底。没有就返回 None。

    刻意不碰 `memories` 表：那是过去某天的总结，翻旧账比不说话更怪。
    """
    spoken_set = set(_load_spoken())
    return _pick_from_self(spoken_set) or _pick_from_life(spoken_set)


#: 这些活动当话题太琐碎，说出来像流水账。
_LOW_SIGNAL_ACTIVITY = ("睡觉", "起床", "洗漱", "空闲")


def _tellable(entry) -> bool:
    """这段日程值不值得当话题。"""
    activity = str(getattr(entry, "activity", "") or "").strip()
    if not activity:
        return False
    return not any(word in activity for word in _LOW_SIGNAL_ACTIVITY)


def _schedule_entries():
    """当天日程（固定作息 + 填入层）。读不到返回空列表。"""
    try:
        from .life import get_life_system

        return list(get_life_system().plan())
    except Exception as exc:
        log.debug(f"读取当天日程失败，主动开口跳过：{exc}")
        return []


def _pick_from_life(spoken_set: set[str], *, minute: int | None = None) -> Opening | None:
    """从今天的日程里挑一段已经开始的事。

    自己填的条目优先，固定作息只作兜底。
    :param minute: 当天窗口内的分钟数；None 表示此刻。
    """
    from .life.model import EntryKind, format_time, now_minutes

    entries = _schedule_entries()
    if minute is None:
        minute = now_minutes()
    candidates = [
        entry
        for entry in entries
        if entry.kind is EntryKind.STAY
        and entry.start_min <= minute
        and _tellable(entry)
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda e: (e.source != "routine", e.end_min))
    entry = candidates[-1]
    text = (
        f"今天{format_time(entry.start)}到{format_time(entry.end)}"
        f"我在{entry.place}，{entry.activity}"
    )
    if _key_of(text) in spoken_set:
        return None
    return Opening(text=text, reason=f"当天日程：{entry.place}/{entry.activity}")


def _pick_from_self(spoken_set: set[str]) -> Opening | None:
    """从自我记忆里挑一条没说过的、情绪够强的。

    自我记忆是**她自己的经历**，本来就不分群——不按 chat_id 过滤。
    """
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


def clean_opening_text(content: str) -> str | None:
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


def _ensure_fallback_history(chat_id: str, chat_history) -> None:
    """确保兜底历史已经加载（与 `TempHisManager.add_message` 同口径）。

    为什么要补：`fallback_history` 只在**用户发消息**时加载。
    主动开口 / 定时任务不经过那条路径，直接读会拿到空列表，
    提示词里就成了「无历史聊天记录」——她不知道群里最近在聊什么，
    于是在空上下文里硬找话说。

    只在内存里没有历史时才从磁盘读，避免覆盖已有的实时消息；
    读失败不影响主流程（返回空历史而已）。
    """
    if getattr(chat_history, "fallback_history_loaded", False):
        return
    try:
        if not chat_history.get_history():
            from ..config import config
            from .history import history_logger

            chat_history.fallback_history = history_logger.get_recent_messages(
                chat_id, config.get("max_history")
            )
    except Exception as exc:
        log.debug(f"加载兜底历史失败（按空处理）：{exc}")
    chat_history.fallback_history_loaded = True


def build_agent_context(
    chat_id: str,
    bot,
    cue: str,
    user_id: str = "",
    level: str = "",
    group_id: str = "",
):
    """构造「没有人当场发言」的思考上下文。

    供**非用户消息触发**的链路共用：主动开口、定时任务到点。

    - `ChatArgs`：给了 ``user_id`` 就用它（定时任务的委托人），
      否则用占位符——主动开口确实没有对话人；
    - `History`：一条代表触发语境的虚拟消息；
    - `ChatMessage`：带上真实队列快照作为已读基线，下一轮思考只取之后的新消息。

    合成对象**不进历史队列**，也不写盘——它只是一次性上下文。

    :param cue: 放进虚拟消息里的那句话，用来告诉模型「现在是什么情形」。
    :param user_id: 这次思考的对话人。定时任务是「谁委托的提醒」，
        主动开口留空。有对话人时提示词里才会带上
        「和这个人的关系 / 关于他的记忆 / 用户画像」——
        否则她只能在一个没有对象的空上下文里说话。
    :param level: 定时任务委托人创建任务时的权限等级。
    :param group_id: 委托人所在的群。
    """
    from .history import ChatHistoryManager, History, LLMMessage, MessageSegment
    from .model import ChatArgs
    from .temp_his import ChatMessage

    args = ChatArgs(
        bot,
        chat_id,
        user_id or _PLACEHOLDER_USER,
        False,
        True,
        level=level,
        group_id=group_id or (chat_id if user_id else ""),
        is_private=not bool(user_id),
    )
    if not user_id and not level:
        # 主动开口没有真实对话人：按内部 system 求值，保留完整工具能力。
        args.level = "system"

    synthetic = History(None, chat_id)
    synthetic.sender = None
    synthetic.message = LLMMessage(chat_id, [MessageSegment(cue)])
    # 标记为合成：`add_response` 见到它会跳过回写，避免往真实队列塞
    # 「你在回复 mid:<合成id>」这种指向不存在消息的记录。
    synthetic.synthetic = True

    chat_history = ChatHistoryManager.get_history(chat_id)
    history_list = chat_history.get_history()
    # 队列快照只用于标记「哪些消息已经出现过」，不参与本次提示词拼装，
    # 这样思考途中新到的消息能被当作及时消息注入，而旧消息不会重复出现。
    chat_msg = ChatMessage(synthetic, history_list, "", [], args)

    _ensure_fallback_history(chat_id, chat_history)
    fallback = chat_history.fallback_history

    return args, synthetic, history_list, fallback, chat_msg


def _build_initiative_context(chat_id: str, bot, opening: Opening):
    """主动开口用的上下文：虚拟消息是「她想起了这件事」。"""
    return build_agent_context(chat_id, bot, opening.text)


def has_dialog_model() -> bool:
    """是否有可用于思考链路的 Agent 模型。

    Agent 只认 ``atri-think`` / ``atri-chat``，不再看旧 ``ATRI`` 或通用
    ``CHAT``。**只要求思考模型**：缺 ``atri-chat`` 时链路会自动降级为
    意图直发（见 ``chat_chain``），主动开口不该因此完全不触发。
    """
    from ..llm import ModelType, llm_manager

    try:
        return bool(llm_manager.has_type(ModelType.ATRI_THINK))
    except Exception:
        return False


async def run_agent_once(
    chat_id: str,
    bot,
    cue: str,
    sender,
    *,
    mode: str = "initiative",
    user_id: str = "",
    level: str = "",
    group_id: str = "",
) -> None:
    """用一次完整思考回应「非用户消息触发」的语境。

    主动开口与定时任务共用这条链路，但**语境不同**，必须用 ``mode`` 区分：

    - ``initiative``：她自己想开口（无人触发，主动搭话）
    - ``scheduled``：定时任务到点（她之前给自己定的提醒）
    - ``life``：早上自己安排一天（先调 plan，再决定要不要说早安）

    之前两者都传「主动开口」，于是定时任务的提示词里出现了
    「这个群没有人在跟你说话」「没有人 @ 你」——那是搭话的语气，
    而定时任务要表达的是「时间到了，该做某件事」。
    模型因此跑偏，去接历史里的旧话题。

    :param cue: 给模型看的触发语（「你想起了一件事」/「定时提醒：xxx」）
    :param sender: 怎么把话发出去（先捕获后校验 / 直接发）
    :param user_id: 这次思考的对话人；定时任务是委托人，主动开口留空
    :param level: 定时任务委托人创建任务时的权限等级
    :param group_id: 委托人所在的群
    """
    from ..brain import ThinkingModel

    args, synthetic, history_list, fallback, chat_msg = build_agent_context(
        chat_id, bot, cue, user_id, level=level, group_id=group_id
    )
    await ThinkingModel().thinking(
        args,
        synthetic,
        history_list,
        "",
        fallback,
        chat_msg,
        sender,
        mode=mode,
    )


async def compose_opening(chat_id: str, bot, opening: Opening):
    """用**完整的 thinking 链路**说一句话——**边说边发**，不先攒着。

    返回 ``(sender, spoke)``：``sender`` 上带着已发送内容与 md 引用，
    供调用方记账；``spoke`` 表示她是否真的说了话。

    为什么改成直接发送：捕获模式看起来能「先校验再发」，
    但校验（`clean_opening_text`）现在由 `BotChatSender` 逐条做，
    效果一样，却能省掉「原始消息 / 纯文本」两套列表、
    元组返回、富媒体特判这一整层。定时任务本来就是直接发的，
    两条链路现在真正一致了。
    """
    from .sender import BotChatSender

    sender = BotChatSender(bot, chat_id, cleaner=clean_opening_text)
    if not has_dialog_model():
        return sender, False

    try:
        await run_agent_once(chat_id, bot, opening.text, sender)
    except Exception as exc:
        log.warning(f"主动开口生成失败：{exc}")
        return sender, False
    return sender, bool(sender.sent_count)


def _group_id(chat_id: str):
    """把 chat_id 转成 OneBot 需要的群号。

    纯数字才转 int；否则原样返回。

    为什么不能直接 ``int()``：OneBot v11 的群号是数字，但 QQ 官方适配器用的是
    **含字母的 openid**。硬转会让这类群永远发不出去，而且异常被最外层吞掉，
    表现成「功能开启了但从来不说话」——极难排查。
    """
    text = str(chat_id).strip()
    return int(text) if text.isdigit() else text


def readable_history_text(text: str) -> str:
    """把可能含 CQ 码的文本转成历史里能读的样子。

    她调用 `md` 时发出去的是图片段，`str()` 之后是
    `[CQ:image,file=xxx.png]` 或一长串 base64。这种内容进历史毫无意义，
    模型读不懂，还占掉大量上下文。
    """
    body = str(text or "").strip()
    if not body:
        return ""
    if body.startswith("[CQ:") or body.startswith("base64://"):
        if "image" in body[:20]:
            return "[发了一张图]"
        if "record" in body[:20]:
            return "[发了一条语音]"
        return "[发了一条富媒体消息]"
    import re

    return re.sub(r"\[CQ:[^\]]*\]", "[富媒体]", body)


def _record_to_history(chat_id: str, texts: str | list[str]) -> None:
    """把主动说出的话写进对话历史。

    为什么必须做：不记录的话，她下一轮对话时**不知道自己刚说过什么**，
    会重复提同一件事、或与刚才的话自相矛盾。人不会这样。

    实现要点：

    - 用 `add_agent_message`，它同时写盘（`user_id=None` = 机器人）和进内存队列，
      与正常回复走同一条路径；
    - **不设 `reply_to_mid`**：主动开口不是在回复某条消息，设了会让历史里
      出现「你在回复 mid:x」这种不存在的关联；
    - 多条时**逐条记录**，与逐条发送保持一致——历史里应能看到
      「诶」「你回来啦」分别是两条，而不是被拼成一句。

    失败不影响已发出的消息——记录是旁路，不能因为写盘失败就报发送失败。
    """
    if isinstance(texts, str):
        texts = [texts]
    for raw in texts:
        text = readable_history_text(raw)
        if not text:
            continue
        try:
            from .history import (
                ChatHistoryManager,
                History,
                LLMMessage,
                MessageSegment,
            )

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
        opening = pick_opening() if ok else None
        if ok and opening is None:
            ok, reason = False, "没有值得说的素材（或都已说过）"
        # 拒绝也要记，否则「她为什么不开口」查不出来；一次尝试只记一条，
        # 先记放行再记没素材会让放行数翻倍。
        telemetry.initiative(chat_id=chat_id, allowed=ok, reason=reason, kind="start")
        if not ok:
            log.debug(f"群 {chat_id} 不主动开口：{reason}")
            return None

        sender, spoke = await compose_opening(chat_id, bot, opening)
        if not spoke:
            log.info(f"群 {chat_id} 的主动发言被模型判断为不合适，跳过")
            return None

        sent = list(sender.sent)
        _record_to_history(chat_id, sent)
        for md_file, _ in sender.md_refs:
            _record_to_history(chat_id, f"[md:{md_file}]")
        mark_spoken(chat_id, opening)
        telemetry.record(
            "initiative_start",
            chat_id=chat_id,
            reason=opening.reason,
            text_len=sum(len(t) for t in sent),
            count=len(sent),
        )
        log.info(f"主动开口（群 {chat_id}，{len(sent)} 条）：{' / '.join(sent)}")
        return "\n".join(sent)
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

    global _patrol_running
    if _patrol_running:
        log.warning("上一轮主动发起巡检还没结束，跳过本轮")
        return 0
    _patrol_running = True
    try:
        return await _patrol_body(nonebot)
    finally:
        _patrol_running = False


_patrol_running = False


def _available_bots() -> list:
    """当前已连接、可用于主动发言的 bot。

    **同时支持 OneBot 与 QQ 官方适配器**：两条链路都走
    `send_to_chat`，它会按 bot 类型分派 API。
    之前只取 OneBot，导致纯 QQ 部署下主动开口与定时任务永远不工作。
    """
    import nonebot

    from .sender import bot_mtype

    bots = []
    for b in nonebot.get_bots().values():
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


def _pick_bot(bots: list):
    """按配置挑一个 bot 用来发送。

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


async def _patrol_body(nonebot_module) -> int:
    groups = whitelisted_groups()
    if not groups:
        return 0

    bots = _available_bots()
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

    try:
        service.scheduler_jobs().add_job(
            morning_life_plan,
            name="agent-morning-life",
            trigger="cron",
            hour=6,
            minute=10,
            misfire_grace_time=600,
            use_log=True,
        )
    except Exception as exc:
        log.warning(f"注册早间生活计划跳过：{exc}")


async def morning_life_plan() -> bool:
    """早上 06:10 让她自己安排一天；配置关闭时直接返回。"""
    try:
        from ..config import config

        if not config.get("life_morning_plan"):
            return False
    except Exception:
        return False
    groups = whitelisted_groups()
    if not groups:
        return False
    bots = _available_bots()
    if not bots:
        return False
    bot, why = _pick_bot(bots)
    if bot is None:
        log.warning(f"早间生活计划：{why}")
        return False
    chat_id = groups[0]
    from .sender import BotChatSender

    # 与主动开口一致：早安也是「无人触发的发言」，说得不得体代价同样高，
    # 所以逐条过 clean_opening_text。
    sender = BotChatSender(bot, chat_id, cleaner=clean_opening_text)
    try:
        await run_agent_once(
            chat_id,
            bot,
            "（新的一天）天亮了。先决定今天怎么过，再决定要不要跟群里说早安。",
            sender,
            mode="life",
        )
    except Exception as exc:
        log.warning(f"早间生活计划失败：{exc}")
        return False
    if not sender.sent_count:
        return False

    # 写历史：否则她下一轮不知道自己早上说过什么（add_response 对合成
    # 上下文是 no-op，主动链路的记账必须在这里补）。
    from .history import log_bot_message

    for text in sender.sent:
        plain = readable_history_text(text)
        if plain:
            log_bot_message(chat_id, plain)
    for md_file, _ in sender.md_refs:
        log_bot_message(chat_id, f"[md:{md_file}]")
    return True
