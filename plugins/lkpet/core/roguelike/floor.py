"""交互处理：处理玩家在局内的编号输入。"""

from typing import Any

from ._common import _find_event, _load_rogue_config, _persist, _text
from .relics import (
    _grant_relic,
    _run_relics,
    relic_effect_note,
    relic_multipliers,
    relic_specs,
)
from .run_state import (
    RogueRun,
    _chosen_set,
    _has_run,
    _relic_price,
    _render_pending,
    advance,
    state_from_pending,
)
from .tower import region_of


def _choice_effect_note(effect: dict[str, Any]) -> str:
    """事件选项效果的紧凑标注。"""
    parts: list[str] = []
    hp = int(effect.get("run_hp", 0))
    if hp:
        parts.append(f"HP{hp:+d}")
    coins = int(effect.get("coins", 0))
    if coins:
        parts.append(f"金币{coins:+d}")
    exp = int(effect.get("exp", 0))
    if exp:
        parts.append(f"经验{exp:+d}")
    relic_id = effect.get("relic")
    if relic_id:
        spec = relic_specs().get(str(relic_id)) or {}
        note = relic_effect_note(spec.get("effect") or {})
        name = spec.get("name", str(relic_id))
        parts.append(f"获得遗物（{name}：{note}）" if note else f"获得遗物（{name}）")
    return "，".join(parts) or _text("ev_no_effect", "没有效果")


async def handle_number(pet, rogue, number: int) -> str:
    """处理局内的编号回复。"""
    if not _has_run(rogue):
        return _text("no_run", "当前没有进行中的局。发送「/宠物.肉鸽 开始」入场。")

    run = RogueRun.from_dict(rogue.run_state)
    cfg = _load_rogue_config()
    state = state_from_pending(run.pending)

    if state.type_name == "need_ap":
        return await advance(pet, rogue)

    if state.type_name == "relic_choice":
        offer = state.offer
        if not offer:
            return await advance(pet, rogue)
        if not 1 <= number <= len(offer):
            return _text("invalid_choice", "编号超出范围。", options=str(len(offer)))
        relic_id = offer[number - 1]
        if relic_id in _run_relics(rogue):
            return _text("relic_owned", "这件遗物本局已经拥有了。")
        spec = _grant_relic(rogue, relic_id) or {}
        await _persist(rogue)
        gained = _text(
            "relic_gained",
            "✨ 获得遗物（本局生效）：{name}（{stat}） — {desc}",
            name=spec.get("name", relic_id),
            stat=relic_effect_note(spec.get("effect") or {}),
            desc=spec.get("desc", ""),
        )
        return gained + "\n" + await advance(pet, rogue)

    if state.type_name == "battle":
        if number != 1:
            return _text("invalid", "回复「1」迎战。")
        from .battle import _do_battle

        return await _do_battle(pet, rogue, cfg)

    if state.type_name == "floor":
        options = state.options
        if not 1 <= number <= len(options):
            return _text("invalid_choice", "编号超出范围。", options=str(len(options)))
        choice = options[number - 1]
        if choice == "battle":
            from .battle import _do_battle

            return await _do_battle(
                pet,
                rogue,
                cfg,
                enemy_index=state.enemy_at(number),
                elite=state.elite,
            )
        if choice == "event":
            from .run_state import EventState

            new_state = EventState(event_id=state.event_id)
            run.pending = new_state.to_pending()
            rogue.run_state = run.to_dict()
            await _persist(rogue)
            return _render_pending(run, cfg, _chosen_set(rogue))
        # 切换到商店状态
        from .run_state import ShopState

        new_state = ShopState(stock=state.stock)
        run.pending = new_state.to_pending()
        rogue.run_state = run.to_dict()
        await _persist(rogue)
        return _render_pending(run, cfg, _chosen_set(rogue))

    if state.type_name == "event":
        return await _resolve_event(pet, rogue, cfg, number)

    if state.type_name == "shop":
        if number == 0:
            return await advance(pet, rogue)
        return await _buy(pet, rogue, cfg, number)

    return _text("unknown", "状态异常，发送「/宠物.肉鸽 进度」查看。")


async def _resolve_event(pet, rogue, cfg: dict[str, Any], number: int) -> str:
    """处理事件选择。"""
    run = RogueRun.from_dict(rogue.run_state)
    pending = run.pending
    event = _find_event(pending.get("event_id"), region_of(run.floor, cfg), cfg)
    if event is None:
        return await advance(pet, rogue)
    choices = event.get("choices", [])
    if not 1 <= number <= len(choices):
        return _text("invalid_choice", "编号超出范围。", options=str(len(choices)))

    effect = choices[number - 1].get("effect", {}) or {}
    choice_key = f"{event.get('id', pending.get('event_id'))}:{number}"
    if choice_key not in rogue.choices:
        rogue.choices.append(choice_key)
        await _persist(rogue)

    lines = [_text("event_result", "→ {choice}", choice=choices[number - 1]["text"])]
    coins = int(effect.get("coins", 0))
    if coins:
        run.coins = max(0, run.coins + coins)
        lines.append(
            _text(
                "ev_coins",
                "局内金币 {delta:+d}（现有 {coins}）",
                delta=coins,
                coins=run.coins,
            )
        )
    hp_delta = int(effect.get("run_hp", 0))
    if hp_delta and pending.get("supply"):
        region = region_of(run.floor, cfg)
        heal_mult = float((region.get("shop") or {}).get("heal_mult", 1))
        hp_delta = round(hp_delta * heal_mult)
    if hp_delta:
        run.hp = max(1, min(run.hp_max, run.hp + hp_delta))
        lines.append(
            _text(
                "ev_hp",
                "局内生命 {delta:+d}（现有 {hp}/{hp_max}）",
                delta=hp_delta,
                hp=run.hp,
                hp_max=run.hp_max,
            )
        )
    exp = int(effect.get("exp", 0))
    if exp:
        run.exp = max(0, run.exp + exp)
        lines.append(
            _text(
                "ev_exp", "本局经验 {delta:+d}（累计 {exp}）", delta=exp, exp=run.exp
            )
        )
    relic_id = effect.get("relic")
    if relic_id and relic_id not in _run_relics(rogue):
        # 同 _buy：_grant_relic 写的是 rogue.run_state，必须先落再发，
        # 否则下面 run.to_dict() 会把事件送的遗物覆盖掉。
        rogue.run_state = run.to_dict()
        spec = _grant_relic(rogue, str(relic_id)) or {}
        run = RogueRun.from_dict(rogue.run_state)
        await _persist(rogue)
        lines.append(
            _text(
                "relic_gained",
                "✨ 获得遗物（本局生效）：{name}（{stat}） — {desc}",
                name=spec.get("name", relic_id),
                stat=relic_effect_note(spec.get("effect") or {}),
                desc=spec.get("desc", ""),
            )
        )

    rogue.run_state = run.to_dict()
    advance_text = await advance(pet, rogue)
    return "\n".join(lines) + "\n" + advance_text


async def _buy(pet, rogue, cfg: dict[str, Any], number: int) -> str:
    """遗物商店购买。"""
    run = RogueRun.from_dict(rogue.run_state)
    pending = run.pending
    stock = pending.get("stock", [])
    if not 1 <= number <= len(stock):
        return _text(
            "invalid_choice", "编号超出范围。回复 0 离开。", options=str(len(stock))
        )
    spec = relic_specs().get(stock[number - 1])
    if spec is None:
        return _text("shop_missing", "这件商品不见了。")
    if spec.get("id") in _run_relics(rogue):
        return _text("relic_owned", "这件遗物本局已经拥有了。")

    price = _relic_price(spec, cfg)
    discount = relic_multipliers(rogue).get("shop_discount", 1.0)
    price = max(1, round(price * discount))
    if run.coins < price:
        return _text(
            "shop_poor",
            "金币不足（需要 {price}，现有 {coins}）。",
            price=price,
            coins=run.coins,
        )

    coins_before = int(run.coins)
    run.coins -= price
    hp_max_before = int(run.hp_max)
    # 先落回 rogue.run_state：_grant_relic 改写的是那份 dict，若晚于它再
    # to_dict() 覆盖，刚买的遗物（及其即时生效的上限/金币）会被丢掉。
    rogue.run_state = run.to_dict()
    _grant_relic(rogue, spec.get("id"))
    run = RogueRun.from_dict(rogue.run_state)
    lines = [
        _text(
            "shop_bought",
            "已购买 {name}（-{price} 金，剩余 {coins}）",
            name=spec.get("name", ""),
            price=price,
            coins=run.coins,
        ),
        _text(
            "relic_gained",
            "✨ 获得遗物（本局生效）：{name}（{stat}） — {desc}",
            name=spec.get("name", ""),
            stat=relic_effect_note(spec.get("effect") or {}),
            desc=spec.get("desc", ""),
        ),
    ]
    if int(run.hp_max) > hp_max_before:
        lines.append(
            _text(
                "shop_hp_max",
                "局内生命上限 +{delta}（{hp}/{hp_max}）",
                delta=int(run.hp_max) - hp_max_before,
                hp=run.hp,
                hp_max=run.hp_max,
            )
        )
    if int(run.coins) > coins_before:
        lines.append(
            _text(
                "ev_coins",
                "局内金币 {delta:+d}（现有 {coins}）",
                delta=int(run.coins) - coins_before,
                coins=run.coins,
            )
        )

    rogue.run_state = run.to_dict()
    await _persist(rogue)

    # 重新渲染商店文本
    lines.append(_render_pending(run, cfg, _chosen_set(rogue)))
    return "\n".join(lines)
