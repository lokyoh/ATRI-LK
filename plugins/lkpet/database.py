import json
from datetime import datetime
from typing import Any, get_origin

from pydantic import Field, ValidationError

from ATRI.system.lkapi.bot import db as lk_db
from ATRI.utils.datetime import now as atri_now
from ATRI.utils.model import BaseModel
from ATRI.utils.sqlite import DBTable


class Pet(BaseModel):
    player_id: str
    name: str
    instruction: str = ""
    rarity: str = "common"
    personality: str = "gentle"
    level: int = 1
    exp: int = 0
    exp_next: int = 100
    attributes: dict[str, int] = Field(default_factory=dict)
    qualification: dict[str, float] = Field(default_factory=dict)
    trained_attributes: dict[str, int] = Field(default_factory=dict)
    mood: int = 80
    hunger: int = 80
    fatigue: int = 0
    cleanliness: int = 100
    affection: int = 0
    health_state: str = "healthy"
    emotion_state: str = "normal"
    adopted_at: datetime = Field(default_factory=datetime.now)
    cycle_started_at: datetime = Field(default_factory=datetime.now)
    age_days: int = 0
    lifespan_days: int = 120
    action_points: int = 10
    ap_reset_day: int = 0
    current_day: int = 0
    last_settlement_day: int = 0
    last_settlement_at: datetime | None = None
    last_login_at: datetime | None = None
    daily_flags: dict[str, bool] = Field(default_factory=dict)
    weekly_flags: dict[str, bool] = Field(default_factory=dict)
    monthly_flags: dict[str, bool] = Field(default_factory=dict)
    skills: dict[str, list[str]] = Field(
        default_factory=lambda: {"active": [], "passive": []}
    )
    """已掌握的技能库（收集池）：``{"active": [...], "passive": [...]}``。

    无数量上限，只增不减（轮回时按来源裁剪，见 ``core.skills``）。
    """
    skill_equipped: dict[str, list[str]] = Field(
        default_factory=lambda: {"active": [], "passive": []}
    )
    """当前装备的技能：主动 / 被动各至多 ``limits.*_slots`` 个（默认 4 + 4）。

    战斗与被动加成只结算这里；未装备的留在 :attr:`skills` 里随时可换。
    """
    skill_sources: dict[str, str] = Field(default_factory=dict)
    """技能 id → 来源（starter/regular/reincarnation/rogue/legacy）。

    决定「轮回时保留什么」：只保留 reincarnation 与 rogue。
    """
    skill_grants: dict[str, int] = Field(default_factory=dict)
    """等级里程碑的已发放计数 ``{"active": n, "passive": m}``。

    每达到 ``grants.active_every`` / ``passive_every`` 的倍数就发一个，用计数保证
    幂等（重复读档不会重复发放，轮回后归零重新累计）。
    """
    skill_proficiency: dict[str, float] = Field(default_factory=dict)
    talent_points: int = 0
    talents: dict[str, int] = Field(default_factory=dict)
    equipment: dict[str, Any] = Field(default_factory=dict)
    skin: str = "default"
    title: str | None = None
    cooldowns: dict[str, Any] = Field(default_factory=dict)
    stats: dict[str, Any] = Field(default_factory=dict)
    cycle_count: int = 0
    is_alive: bool = True


class Reincarnation(BaseModel):
    player_id: str
    achievement_points: int = 0
    spent_points: int = 0
    total_cycles: int = 0
    best_level: int = 1
    best_age_days: int = 0
    best_attributes: dict[str, int] = Field(default_factory=dict)
    permanent_bonuses: dict[str, int | float] = Field(default_factory=dict)
    """永久加成，由 ``PERK_LEVELS`` 派生出来的**缓存**。

    不要直接增减这个字段：改 ``perk_levels`` 后必须重算一次，
    否则会出现"等级在涨、加成不动"的不一致。
    """
    perk_levels: dict[str, int] = Field(default_factory=dict)
    """商店持有情况的**唯一真相**：``{商品 id: 已买等级}``。

    商品 id 与等级上限来自 ``reincarnation.yaml`` 的商店配置；未定稿前恒为空。
    """
    respec_tickets: int = 0
    """洗点券张数（每完成一次轮回赠送，也可在商店购买）。"""
    unlocked_perks: list[str] = Field(default_factory=list)
    """**只读遗留列**：旧版"已兑换特权"列表。

    迁移折算进 :attr:`perk_levels` 后不再写入，仅保留以便旧档排查与回滚。
    """
    past_pets: list[dict[str, Any]] = Field(default_factory=list)
    achievements_unlocked: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class Biography(BaseModel):
    player_id: str
    entries: list[dict[str, Any]] = Field(default_factory=list)
    titles_unlocked: list[str] = Field(default_factory=list)
    key_choices: list[dict[str, Any]] = Field(default_factory=list)
    memories: list[dict[str, Any]] = Field(default_factory=list)
    daily_logs: list[dict[str, Any]] = Field(default_factory=list)
    ending: str | None = None


class Rogue(BaseModel):
    """肉鸽爬塔进度（任务书 §12.2）：历史统计 + 进行中的 run。

    ``run_state`` 为空 dict 表示当前没有进行中的局；局内生命/金币/增益
    只存在于 run_state，结算时一次性写回，绝不直接落养成数据列。
    ``unlocked`` 记录 Boss 层首通解锁的奖励 id（幂等，不重复发放）。
    """

    player_id: str
    best_floor: int = 0
    total_runs: int = 0
    clears: int = 0
    unlocked: list[str] = Field(default_factory=list)
    relics: list[str] = Field(default_factory=list)
    """已收集的遗物 id（跨 run 永久；含 Boss 专属遗物，见 rogue_relics.yaml）。"""
    choices: list[str] = Field(default_factory=list)
    """历史选过的事件选项，条目为 ``"事件id:选项编号"``（跨 run 永久，仿 RELICS 图鉴）。

    再次遭遇同一事件时，选过的选项会在文本后标注实际效果（见
    ``core.roguelike._choice_effect_note``）。
    """
    run_state: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=datetime.now)


PETDATA_VERSION = 2
"""PETDATA 表结构版本。

1 = 旧聊天插件的三列表重建为完整养成表；
2 = 技能系统重构：新增 SKILL_EQUIPPED / SKILL_SOURCES / SKILL_GRANTS
    （收集池 + 装备槽 + 来源 + 里程碑计数），并把旧的「已学即装备」拆开。
"""
# 注：LOVE 是旧聊天插件的 0-100 好感列，属于历史遗留。它保留在表里以避免动既有数据，
# 但**已无代码读取**，也不参与任何迁移（新养成的亲密度是 AFFECTION，旧宠物从 0 开始）。
PETDATA_COLUMNS = """
ID                  TEXT PRIMARY KEY,
NAME                TEXT NOT NULL,
INSTRUCTION         TEXT NOT NULL,
RARITY              TEXT NOT NULL DEFAULT 'common',
PERSONALITY         TEXT NOT NULL DEFAULT 'gentle',
LEVEL               INTEGER NOT NULL DEFAULT 1,
EXP                 INTEGER NOT NULL DEFAULT 0,
EXP_NEXT            INTEGER NOT NULL DEFAULT 100,
ATTRIBUTES          TEXT NOT NULL DEFAULT '{}',
QUALIFICATION       TEXT NOT NULL DEFAULT '{}',
TRAINED_ATTRIBUTES  TEXT NOT NULL DEFAULT '{}',
MOOD                INTEGER NOT NULL DEFAULT 80,
HUNGER              INTEGER NOT NULL DEFAULT 80,
FATIGUE             INTEGER NOT NULL DEFAULT 0,
CLEANLINESS         INTEGER NOT NULL DEFAULT 100,
AFFECTION           INTEGER NOT NULL DEFAULT 0,
HEALTH_STATE        TEXT NOT NULL DEFAULT 'healthy',
EMOTION_STATE       TEXT NOT NULL DEFAULT 'normal',
ADOPTED_AT          TEXT,
CYCLE_STARTED_AT    TEXT,
AGE_DAYS            INTEGER NOT NULL DEFAULT 0,
LIFESPAN_DAYS       INTEGER NOT NULL DEFAULT 120,
ACTION_POINTS       INTEGER NOT NULL DEFAULT 10,
AP_RESET_DAY        INTEGER NOT NULL DEFAULT 0,
CURRENT_DAY         INTEGER NOT NULL DEFAULT 0,
LAST_SETTLEMENT_DAY INTEGER NOT NULL DEFAULT 0,
LAST_SETTLEMENT_AT  TEXT,
LAST_LOGIN_AT       TEXT,
DAILY_FLAGS         TEXT NOT NULL DEFAULT '{}',
WEEKLY_FLAGS        TEXT NOT NULL DEFAULT '{}',
MONTHLY_FLAGS       TEXT NOT NULL DEFAULT '{}',
SKILLS              TEXT NOT NULL DEFAULT '{"active": [], "passive": []}',
SKILL_EQUIPPED      TEXT NOT NULL DEFAULT '{"active": [], "passive": []}',
SKILL_SOURCES       TEXT NOT NULL DEFAULT '{}',
SKILL_GRANTS        TEXT NOT NULL DEFAULT '{}',
SKILL_PROFICIENCY   TEXT NOT NULL DEFAULT '{}',
TALENT_POINTS       INTEGER NOT NULL DEFAULT 0,
TALENTS             TEXT NOT NULL DEFAULT '{}',
EQUIPMENT           TEXT NOT NULL DEFAULT '{}',
SKIN                TEXT NOT NULL DEFAULT 'default',
TITLE               TEXT,
COOLDOWNS           TEXT NOT NULL DEFAULT '{}',
STATS               TEXT NOT NULL DEFAULT '{}',
CYCLE_COUNT         INTEGER NOT NULL DEFAULT 0,
IS_ALIVE            INTEGER NOT NULL DEFAULT 1
"""

PETDATA_MIGRATION_COLUMNS = {
    "RARITY": "TEXT NOT NULL DEFAULT 'common'",
    "PERSONALITY": "TEXT NOT NULL DEFAULT 'gentle'",
    "LEVEL": "INTEGER NOT NULL DEFAULT 1",
    "EXP": "INTEGER NOT NULL DEFAULT 0",
    "EXP_NEXT": "INTEGER NOT NULL DEFAULT 100",
    "ATTRIBUTES": "TEXT NOT NULL DEFAULT '{}'",
    "QUALIFICATION": "TEXT NOT NULL DEFAULT '{}'",
    "TRAINED_ATTRIBUTES": "TEXT NOT NULL DEFAULT '{}'",
    "MOOD": "INTEGER NOT NULL DEFAULT 80",
    "HUNGER": "INTEGER NOT NULL DEFAULT 80",
    "FATIGUE": "INTEGER NOT NULL DEFAULT 0",
    "CLEANLINESS": "INTEGER NOT NULL DEFAULT 100",
    "AFFECTION": "INTEGER NOT NULL DEFAULT 0",
    "HEALTH_STATE": "TEXT NOT NULL DEFAULT 'healthy'",
    "EMOTION_STATE": "TEXT NOT NULL DEFAULT 'normal'",
    "ADOPTED_AT": "TEXT",
    "CYCLE_STARTED_AT": "TEXT",
    "AGE_DAYS": "INTEGER NOT NULL DEFAULT 0",
    "LIFESPAN_DAYS": "INTEGER NOT NULL DEFAULT 120",
    "ACTION_POINTS": "INTEGER NOT NULL DEFAULT 10",
    "AP_RESET_DAY": "INTEGER NOT NULL DEFAULT 0",
    "CURRENT_DAY": "INTEGER NOT NULL DEFAULT 0",
    "LAST_SETTLEMENT_DAY": "INTEGER NOT NULL DEFAULT 0",
    "LAST_SETTLEMENT_AT": "TEXT",
    "LAST_LOGIN_AT": "TEXT",
    "DAILY_FLAGS": "TEXT NOT NULL DEFAULT '{}'",
    "WEEKLY_FLAGS": "TEXT NOT NULL DEFAULT '{}'",
    "MONTHLY_FLAGS": "TEXT NOT NULL DEFAULT '{}'",
    "SKILLS": 'TEXT NOT NULL DEFAULT \'{"active": [], "passive": []}\'',
    "SKILL_EQUIPPED": 'TEXT NOT NULL DEFAULT \'{"active": [], "passive": []}\'',
    "SKILL_SOURCES": "TEXT NOT NULL DEFAULT '{}'",
    "SKILL_GRANTS": "TEXT NOT NULL DEFAULT '{}'",
    "SKILL_PROFICIENCY": "TEXT NOT NULL DEFAULT '{}'",
    "TALENT_POINTS": "INTEGER NOT NULL DEFAULT 0",
    "TALENTS": "TEXT NOT NULL DEFAULT '{}'",
    "EQUIPMENT": "TEXT NOT NULL DEFAULT '{}'",
    "SKIN": "TEXT NOT NULL DEFAULT 'default'",
    "TITLE": "TEXT",
    "COOLDOWNS": "TEXT NOT NULL DEFAULT '{}'",
    "STATS": "TEXT NOT NULL DEFAULT '{}'",
    "CYCLE_COUNT": "INTEGER NOT NULL DEFAULT 0",
    "IS_ALIVE": "INTEGER NOT NULL DEFAULT 1",
}


def update_pet_db(connection, version: int) -> None:
    """幂等的迁移：先按 PRAGMA 补齐缺失列，再按版本做数据搬运。

    v1 → v2（技能系统重构）：旧模型里 ``SKILLS`` 既是「已学」也是「已装备」。
    新模型把两者拆开——``SKILLS`` 变成收集池、``SKILL_EQUIPPED`` 是装备槽、
    ``SKILL_SOURCES`` 记录来源、``SKILL_GRANTS`` 记录里程碑计数。
    这里把旧列原样复制进收集池，装备槽取各类型前 ``active_slots`` /
    ``passive_slots`` 个（多出来的留在收集池里可以再装），来源统一标成 legacy
    （轮回时被清空，与旧技能「靠等级临时获得」的定位一致）。
    """
    cursor = connection.cursor()
    try:
        if version < 1:
            cursor.execute("DROP TABLE IF EXISTS `PETDATA_TEMP`")
            cursor.execute(
                "CREATE TABLE `PETDATA_TEMP` (ID TEXT PRIMARY KEY, NAME TEXT NOT NULL, INSTRUCTION TEXT NOT NULL)"
            )
            cursor.execute(
                """
                        INSERT INTO `PETDATA_TEMP` (`ID`, `NAME`, `INSTRUCTION`)
                        SELECT CAST(ID AS TEXT), `NAME`, `INSTRUCTION` FROM `PETDATA`
                        """
            )
            cursor.execute("DROP TABLE `PETDATA`")
            cursor.execute("ALTER TABLE `PETDATA_TEMP` RENAME TO `PETDATA`")

        columns = {row[1] for row in cursor.execute("PRAGMA table_info(PETDATA)")}
        # 用插件统一的时区（ATRI.utils.datetime）而不是 SQLite 的 CURRENT_TIMESTAMP ——
        # 后者是 UTC，会让旧宠物的 CYCLE_STARTED_AT 比本地时间早 8 小时，
        # 在本地 00:00~08:00 之间迁移时 .date() 会差一天，导致多结算一天。
        stamp = atri_now().strftime("%Y-%m-%d %H:%M:%S")
        with connection:
            for column, definition in PETDATA_MIGRATION_COLUMNS.items():
                if column not in columns:
                    cursor.execute(
                        f"ALTER TABLE PETDATA ADD COLUMN {column} {definition}"
                    )
            if version < 1:
                cursor.execute(
                    "UPDATE PETDATA SET ADOPTED_AT = ? "
                    "WHERE ADOPTED_AT IS NULL OR ADOPTED_AT = ''",
                    (stamp,),
                )
                cursor.execute(
                    "UPDATE PETDATA SET CYCLE_STARTED_AT = ADOPTED_AT "
                    "WHERE CYCLE_STARTED_AT IS NULL OR CYCLE_STARTED_AT = ''"
                )
                cursor.execute(
                    "UPDATE PETDATA SET LAST_SETTLEMENT_AT = ADOPTED_AT "
                    "WHERE LAST_SETTLEMENT_AT IS NULL OR LAST_SETTLEMENT_AT = ''"
                )
        if version < 2:
            _migrate_pet_skills_v2(connection, cursor)
    finally:
        cursor.close()


def _migrate_pet_skills_v2(connection, cursor) -> None:
    """把旧「已学 = 已装备」的技能列拆成收集池 + 装备槽 + 来源 + 里程碑计数。"""
    from .core.skills import SLOT_TYPES, equipped_limit, infer_source

    limits = {slot: equipped_limit(slot) for slot in SLOT_TYPES}
    rows = list(cursor.execute("SELECT ID, SKILLS FROM PETDATA"))
    with connection:
        for player_id, raw in rows:
            try:
                learned = json.loads(raw or "{}")
            except (TypeError, ValueError):
                learned = {}
            if not isinstance(learned, dict):
                learned = {}

            collection = {
                slot: [str(sid) for sid in learned.get(slot, []) or []]
                for slot in SLOT_TYPES
            }
            all_ids = [sid for slot in SLOT_TYPES for sid in collection[slot]]
            sources = {sid: infer_source(sid) for sid in all_ids}
            equipped = {slot: collection[slot][: limits[slot]] for slot in SLOT_TYPES}
            # 旧技能全部来自等级解锁，视为本世临时技能。里程碑计数按 0 起算，
            # 于是旧存档首次读档时会按当前等级补发常规池技能（见
            # ``pet_core.ensure_initialized``），与旧版「到等级就学」的体验对齐。
            cursor.execute(
                "UPDATE PETDATA SET SKILLS = ?, SKILL_EQUIPPED = ?, "
                "SKILL_SOURCES = ?, SKILL_GRANTS = ? WHERE ID = ?",
                (
                    json.dumps(collection, ensure_ascii=False),
                    json.dumps(equipped, ensure_ascii=False),
                    json.dumps(sources, ensure_ascii=False),
                    json.dumps({"active": 0, "passive": 0}, ensure_ascii=False),
                    player_id,
                ),
            )


REINCARNATION_VERSION = 1
"""PETREINCARNATION 表结构版本。

0 = 初版（成绩点 + 特权列表）。
1 = 商店改版：新增 PERK_LEVELS（等级化持有）与 RESPEC_TICKETS（洗点券）。
    ``UNLOCKED_PERKS`` 降级为只读遗留列——迁移时折算进 ``PERK_LEVELS``，之后不再写它。
"""

REINCARNATION_COLUMNS = """
PLAYER_ID             TEXT PRIMARY KEY,
ACHIEVEMENT_POINTS    INTEGER NOT NULL DEFAULT 0,
SPENT_POINTS          INTEGER NOT NULL DEFAULT 0,
TOTAL_CYCLES          INTEGER NOT NULL DEFAULT 0,
BEST_LEVEL            INTEGER NOT NULL DEFAULT 1,
BEST_AGE_DAYS         INTEGER NOT NULL DEFAULT 0,
BEST_ATTRIBUTES       TEXT NOT NULL DEFAULT '{}',
PERMANENT_BONUSES     TEXT NOT NULL DEFAULT '{}',
PERK_LEVELS           TEXT NOT NULL DEFAULT '{}',
RESPEC_TICKETS        INTEGER NOT NULL DEFAULT 0,
UNLOCKED_PERKS        TEXT NOT NULL DEFAULT '[]',
PAST_PETS             TEXT NOT NULL DEFAULT '[]',
ACHIEVEMENTS_UNLOCKED TEXT NOT NULL DEFAULT '[]',
CREATED_AT            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
UPDATED_AT            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
"""

REINCARNATION_MIGRATION_COLUMNS = {
    "PERK_LEVELS": "TEXT NOT NULL DEFAULT '{}'",
    "RESPEC_TICKETS": "INTEGER NOT NULL DEFAULT 0",
}
"""v0 → v1 及之后所有版本共用：``PRAGMA table_info`` 缺什么补什么（幂等）。

与 ``PETDATA_MIGRATION_COLUMNS`` / ``ROGUE_MIGRATION_COLUMNS`` 同一策略：
不看版本号本身，只补列，因此可重复执行。
"""

LEGACY_PERK_MAP: dict[str, tuple[str, int, int]] = {
    # 旧 id → (新商品 id, 折算级数, 旧单价)。**新商品 id 一旦定下就不能再改**，
    # 它同时是 PERK_LEVELS 的键与商店配置的键。
    #
    # 目前**故意留空**：商店商品尚未定稿，旧特权的归属需要人工决定
    # （一个旧 id 可能拆成多个新商品，或合并进同一个商品的不同等级）。
    # 留空时 ``_migrate_perks_v1`` 会把 UNLOCKED_PERKS 原样保留、不折算、
    # 不退款，玩家数据零损失，等商品定稿后补一行映射即可生效。
}

BIOGRAPHY_COLUMNS = """
PLAYER_ID      TEXT PRIMARY KEY,
ENTRIES        TEXT NOT NULL DEFAULT '[]',
TITLES_UNLOCKED TEXT NOT NULL DEFAULT '[]',
KEY_CHOICES    TEXT NOT NULL DEFAULT '[]',
MEMORIES       TEXT NOT NULL DEFAULT '[]',
DAILY_LOGS     TEXT NOT NULL DEFAULT '[]',
ENDING         TEXT
"""


ROGUE_VERSION = 2
"""PETROGUE 表结构版本：1 = 新增 RELICS 列（遗物收藏）；
2 = 新增 CHOICES 列（事件选项历史，跨 run 永久，用于选项效果剧透标注）。

改表结构必须同步提升此版本号，否则已到该版本的库会跳过迁移。
"""

ROGUE_COLUMNS = """
PLAYER_ID      TEXT PRIMARY KEY,
BEST_FLOOR     INTEGER NOT NULL DEFAULT 0,
TOTAL_RUNS     INTEGER NOT NULL DEFAULT 0,
CLEARS         INTEGER NOT NULL DEFAULT 0,
UNLOCKED       TEXT NOT NULL DEFAULT '[]',
RELICS         TEXT NOT NULL DEFAULT '[]',
CHOICES        TEXT NOT NULL DEFAULT '[]',
RUN_STATE      TEXT NOT NULL DEFAULT '{}',
UPDATED_AT     TEXT
"""

ROGUE_MIGRATION_COLUMNS = {
    "RELICS": "TEXT NOT NULL DEFAULT '[]'",
    "CHOICES": "TEXT NOT NULL DEFAULT '[]'",
}


def update_rogue_db(connection, version: int) -> None:
    """幂等的列差异补齐（与 update_pet_db 同策略：读 PRAGMA，缺什么补什么）。"""
    cursor = connection.cursor()
    try:
        existing = {
            str(row[1]).upper() for row in cursor.execute("PRAGMA table_info(PETROGUE)")
        }
        with connection:
            for column, definition in ROGUE_MIGRATION_COLUMNS.items():
                if column not in existing:
                    cursor.execute(
                        f"ALTER TABLE PETROGUE ADD COLUMN {column} {definition}"
                    )
        if version < 2:
            cursor.execute("DROP TABLE IF EXISTS `PETTRPG`")
    finally:
        cursor.close()


def update_reincarnation_db(connection, version: int) -> None:
    """PETREINCARNATION 迁移：补列 + 把 UNLOCKED_PERKS 折算进 PERK_LEVELS。

    两步都与版本号无关，只看实际列与数据，因此可重复执行：
    1. ``PRAGMA table_info`` 补齐缺失列；
    2. ``LEGACY_PERK_MAP`` 非空时，把仍为空 ``PERK_LEVELS`` 的行折算一次。
       已经折算过的行（``PERK_LEVELS`` 非 ``'{}'``）跳过，不会重复退款。
    """
    cursor = connection.cursor()
    try:
        existing = {
            str(row[1]).upper()
            for row in cursor.execute("PRAGMA table_info(PETREINCARNATION)")
        }
        with connection:
            for column, definition in REINCARNATION_MIGRATION_COLUMNS.items():
                if column not in existing:
                    cursor.execute(
                        f"ALTER TABLE PETREINCARNATION ADD COLUMN {column} {definition}"
                    )
        _migrate_legacy_perks(connection, cursor)
    finally:
        cursor.close()


def _migrate_legacy_perks(connection, cursor) -> None:
    """把旧 ``UNLOCKED_PERKS`` 的次数折算成 ``PERK_LEVELS`` 的等级。

    ``LEGACY_PERK_MAP`` 为空时**直接返回**，玩家数据原样不动——商品未定稿前
    不应该替玩家做任何决定。映射就位后才开始折算：

    * 出现次数 = 等级；``(新 id, 级数, 旧单价)`` 里的级数用于"一次算几级"
      （旧商店里 ``start_level_5`` 与 ``start_level_10`` 是两条独立商品，
      各自折算成新商品的 1 级 / 2 级）；
    * 超出上限的部分按**旧单价**退：减 ``SPENT_POINTS``，因为
      可用点数 = ``ACHIEVEMENT_POINTS - SPENT_POINTS``，减后者等价于退款，
      且不需要动累计值。
    """
    if not LEGACY_PERK_MAP:
        return

    try:
        from .core.shop import catalog, recompute_bonuses, spec_level_cap
    except ImportError:  # 商店模块尚未落地时跳过折算，下次启动再试
        return

    rows = list(
        cursor.execute(
            "SELECT PLAYER_ID, UNLOCKED_PERKS, SPENT_POINTS FROM PETREINCARNATION"
        )
    )
    if not rows:
        return

    try:
        catalog_map = catalog()
    except (FileNotFoundError, TypeError, ValueError):
        # 配置还没写（商品未定稿）时不算，避免把玩家数据写坏。
        return
    if not catalog_map:
        return

    with connection:
        for player_id, raw_perks, spent in rows:
            try:
                perks = json.loads(raw_perks or "[]")
            except (TypeError, ValueError):
                continue
            if not isinstance(perks, list) or not perks:
                continue

            levels: dict[str, int] = {}
            refund = 0
            handled = False
            for perk_id in perks:
                mapped = LEGACY_PERK_MAP.get(str(perk_id))
                if mapped is None:
                    continue
                handled = True
                item_id, step, legacy_cost = mapped
                spec = catalog_map.get(item_id)
                if spec is None:
                    continue
                cap = spec_level_cap(spec)
                current = levels.get(item_id, 0)
                room = max(0, cap - current)
                take = min(step, room)
                if take:
                    levels[item_id] = current + take
                # 超出上限的级数按旧单价退还（含"部分放不下"的情况）。
                refund += (step - take) * legacy_cost

            if not handled:
                continue

            bonus = recompute_bonuses(levels)
            # **把 UNLOCKED_PERKS 清空，作为"已折算"标记**。
            # 不能用 ``PERK_LEVELS`` 当标记：合法折算的结果可能就是空 dict
            # （旧 id 全部被上限截断时），于是"已处理"与"未处理"无法区分。
            # 实测不清空时，重跑迁移会把退款重复扣（210 → 60）。
            cursor.execute(
                "UPDATE PETREINCARNATION SET PERK_LEVELS = ?, PERMANENT_BONUSES = ?, "
                "SPENT_POINTS = MAX(0, ? - ?), UNLOCKED_PERKS = '[]' WHERE PLAYER_ID = ?",
                (
                    json.dumps(levels, ensure_ascii=False),
                    json.dumps(bonus, ensure_ascii=False),
                    int(spent or 0),
                    refund,
                    player_id,
                ),
            )


pet_table: DBTable = lk_db.get_table(
    "PETDATA", PETDATA_COLUMNS, PETDATA_VERSION, update_pet_db
)
reincarnation_table: DBTable = lk_db.get_table(
    "PETREINCARNATION",
    REINCARNATION_COLUMNS,
    REINCARNATION_VERSION,
    update_reincarnation_db,
)
biography_table: DBTable = lk_db.get_table("PETBIOGRAPHY", BIOGRAPHY_COLUMNS, 0)
rogue_table: DBTable = lk_db.get_table(
    "PETROGUE", ROGUE_COLUMNS, ROGUE_VERSION, update_rogue_db
)

PET_KEY_COLUMN = "ID"
REINCARNATION_KEY_COLUMN = "PLAYER_ID"
BIOGRAPHY_KEY_COLUMN = "PLAYER_ID"
ROGUE_KEY_COLUMN = "PLAYER_ID"
"""各表主键列名；模型侧统一叫 ``player_id``。"""

TABLE_FIELD_ALIASES: dict[str, dict[str, str]] = {
    "PETDATA": {"player_id": "ID"},
}
"""模型字段名与数据库列名不一致时的对照表（仅 PETDATA 的旧主键需要）。"""

STRING_KEY_FIELDS: dict[str, frozenset[str]] = {
    "Pet": frozenset({"player_id"}),
    "Reincarnation": frozenset({"player_id"}),
    "Biography": frozenset({"player_id"}),
    "Rogue": frozenset({"player_id"}),
}
"""必须按字符串读回的主键字段（见 :func:`_normalize_string_keys`）。"""


def _encode_value(value: Any) -> Any:
    """Convert a model value into something SQLite can hold."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    return value


def _decode_value(raw: Any, annotation: Any) -> Any:
    """Inverse of :func:`_encode_value`, driven by the model's field annotation."""
    if isinstance(raw, str) and get_origin(annotation) in (dict, list):
        if raw == "":
            return {} if get_origin(annotation) is dict else []
        return json.loads(raw)
    return raw


def table_field_pairs(table: DBTable, model: type[BaseModel]) -> list[tuple[str, str]]:
    aliases = TABLE_FIELD_ALIASES.get(table.table_name, {})
    with table.get_cursor() as cursor:
        existing = {
            str(row[1]).lower()
            for row in cursor.execute(f"PRAGMA table_info({table.table_name})")
        }
    pairs = []
    for field in model.model_fields:
        column = aliases.get(field, field)
        if column.lower() in existing:
            pairs.append((column.upper(), field))
    return pairs


def _normalize_string_keys(model_cls: type[BaseModel], data: dict[str, Any]) -> None:
    """Force the key fields declared as ``str`` back to ``str`` in place.

    ``PETREINCARNATION`` / ``PETBIOGRAPHY`` declare their primary key as ``TXT``,
    which carries no SQLite type affinity, so a numeric player id round-trips as
    an ``int`` and then fails the ``str``-typed model on validation.  A legacy
    ``PETDATA`` whose ``ID`` column ended up with numeric affinity (old schema
    rebuilds) shows the same symptom, so ``Pet`` is normalized too — coercing a
    value that is already ``str`` is a no-op.
    """
    for field in STRING_KEY_FIELDS.get(model_cls.__name__, ()):
        if field in data and data[field] is not None:
            data[field] = str(data[field])


def load_model(
    table: DBTable,
    model_cls: type[BaseModel],
    key_column: str,
    key_value: Any,
) -> BaseModel | None:
    """Read one row by primary key and validate it into ``model_cls``."""
    pairs = table_field_pairs(table, model_cls)
    columns = ", ".join(column for column, _ in pairs)
    rows = table.select(columns, {key_column: key_value})
    if not rows:
        return None

    data = {
        field: _decode_value(raw, model_cls.model_fields[field].annotation)
        for (_, field), raw in zip(pairs, rows[0], strict=False)
    }
    _normalize_string_keys(model_cls, data)
    return model_cls.model_validate(data)


def save_model(table: DBTable, model: BaseModel, key_column: str) -> None:
    """Insert or update one model instance, keyed on ``key_column``."""
    pairs = table_field_pairs(table, type(model))
    content = {column: _encode_value(getattr(model, field)) for column, field in pairs}
    key_value = content[key_column]
    if table.select(key_column, {key_column: key_value}):
        table.update(content, {key_column: key_value})
    else:
        table.insert(content)


def load_reincarnation(player_id: str | int) -> Reincarnation | None:
    """Read a player's reincarnation row, or ``None`` when never created.

    ``player_id`` 收 ``str`` 也收 ``int``：调用方从事件拿到的通常是 ``int``，
    而 ``Pet.player_id`` 落库是 ``str``。两者都按 ``int`` 归一后查库。
    """
    return load_model(
        reincarnation_table, Reincarnation, REINCARNATION_KEY_COLUMN, int(player_id)
    )


def save_reincarnation(reinc: Reincarnation) -> None:
    """Persist a reincarnation row (insert or update)."""
    save_model(reincarnation_table, reinc, REINCARNATION_KEY_COLUMN)


def load_biography(player_id: str) -> Biography | None:
    """Read a player's biography row, or ``None`` when never created."""
    return load_model(biography_table, Biography, BIOGRAPHY_KEY_COLUMN, player_id)


def save_biography(bio: Biography) -> None:
    """Persist a biography row (insert or update)."""
    save_model(biography_table, bio, BIOGRAPHY_KEY_COLUMN)


def load_rogue(player_id: str | int) -> Rogue | None:
    """Read a player's roguelike progress row, or ``None`` when never created."""
    return load_model(rogue_table, Rogue, ROGUE_KEY_COLUMN, str(player_id))


def save_rogue(rogue: Rogue) -> None:
    """Persist a roguelike progress row (insert or update)."""
    save_model(rogue_table, rogue, ROGUE_KEY_COLUMN)


def load_all_pets() -> list[Pet]:
    """Load every pet in ``PETDATA``, in table order.

    Rows that fail to validate are skipped instead of aborting the whole pass:
    the settlement notify job walks every pet and one malformed legacy row must
    not stop the others from reaching their new day.
    """
    pairs = table_field_pairs(pet_table, Pet)
    columns = ", ".join(column for column, _ in pairs)
    pets: list[Pet] = []
    for row in pet_table.select_all(columns):
        data = {
            field: _decode_value(raw, Pet.model_fields[field].annotation)
            for (_, field), raw in zip(pairs, row, strict=False)
        }
        _normalize_string_keys(Pet, data)
        try:
            pets.append(Pet.model_validate(data))
        except ValidationError:
            continue
    return pets


def load_pet_name(player_id: str) -> str | None:
    """Synchronous pet-name lookup.

    ATRI's ``BaseEvents`` rejects coroutine listeners, so the user-info event
    handler cannot await; ``DBTable`` itself is synchronous anyway.
    """
    rows = pet_table.select("NAME", {PET_KEY_COLUMN: player_id})
    return rows[0][0] if rows else None


def load_pet_names() -> dict[str, str]:
    """Synchronous ``{player_id: name}`` map of every adopted pet."""
    return {int(row[0]): row[1] for row in pet_table.select_all("ID, NAME")}
