"""无尽模式的统一模板代理。

```BattleSession``` 只通过属性访问 ``pet```，没有类型检查，所以不必构造真实的
``Pet```（那会带上 player_id、equipment、talents、title、age_days 等一堆不该
参与无尽模式的输入）。这里提供只读代理，把契约属性挡在门外。

契约（逐个核实过的实际读取点）：

    attributes          battle.py:296, _common.py:182
    name                战报 _blog
    level               _common.py:272 level_combat_mults
    skill_equipped      skills.py:318 -> equipped_specs
    skills              skills.py:328 is_known
    skill_proficiency   skills.py:694/718
    affection           battle.py:446 apply_affection_bonus
"""

from __future__ import annotations

from typing import Any


class EndlessAvatar:
    """把模板面板包装成 BattleSession 能接受的只读 pet。"""

    __slots__ = (
        "_attrs",
        "_cfg",
        "_enhance",
        "_equipped",
        "_level",
        "_name",
        "_prof",
        "_ro",
        "_skills",
    )

    def __init__(
        self,
        attributes: dict[str, int],
        *,
        level: int = 1,
        name: str = "旅人",
        skills: dict[str, list[str]] | None = None,
        proficiency: dict[str, float] | None = None,
        skills_config: dict[str, Any] | None = None,
        enhance: dict[str, int] | None = None,
    ) -> None:
        # 用 object.__setattr__ 绕开自己的 __setattr__ 保护
        object.__setattr__(self, "_attrs", dict(attributes))
        object.__setattr__(self, "_level", int(level))
        object.__setattr__(self, "_name", str(name) or "旅人")
        merged = {"active": [], "passive": []}
        for slot, ids in (skills or {}).items():
            if slot in merged:
                merged[slot] = [str(i) for i in ids]
        object.__setattr__(self, "_skills", merged)
        object.__setattr__(self, "_equipped", {k: list(v) for k, v in merged.items()})
        object.__setattr__(
            self, "_prof", proficiency if proficiency is not None else {}
        )
        object.__setattr__(self, "_cfg", skills_config)

        object.__setattr__(self, "_enhance", dict(enhance or {}))
        object.__setattr__(self, "_ro", True)

    def __setattr__(self, key: str, value: Any) -> None:
        # skills.py:734 是「重新绑定」而不是原地写，所以熟练度必须放行。
        # 写回同一个 dict，外部持有的引用不会失效。
        if key == "_prof" or key == "skill_proficiency":
            current = object.__getattribute__(self, "_prof")
            current.clear()
            current.update(dict(value or {}))
            return
        raise AttributeError(f"EndlessAvatar 只读，不允许赋值：{key}")

    # -- 契约属性 ---------------------------------------------------------

    @property
    def attributes(self) -> dict[str, int]:
        return self._attrs

    @property
    def name(self) -> str:
        return self._name

    @property
    def level(self) -> int:
        return self._level

    @property
    def skill_equipped(self) -> dict[str, list[str]]:
        return self._equipped

    @property
    def skills(self) -> dict[str, list[str]]:
        return self._skills

    @property
    def skill_enhance(self) -> dict[str, int]:
        """各技能的强化次数（由 RogueRun.pending["enhance"] 维护）。"""
        return self._enhance

    @property
    def skills_config(self) -> dict[str, Any] | None:
        """本局使用的技能定义表；None = 退回爬塔的 skills.yaml。"""
        return self._cfg

    @property
    def skill_proficiency(self) -> dict[str, float]:
        # 可变 dict：skills.py:718 会原地写它，局内熟练度由此跟随 run 状态
        return self._prof

    @property
    def affection(self) -> int:
        # 本模式不继承好感，固定 0 才没有 +10% 输出乘区
        return 0
