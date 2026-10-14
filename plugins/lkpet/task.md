# 宠物插件任务书（ATRI-LK / Python · 养成系统 · 轮回 · 天为单位 · 无种族）

> **文档用途**：面向 AI 开发助手与维护者，描述 ATRI-LK 宠物插件养成系统的**规范与验收标准**。  
> **阅读对象**：AI（Claude / GPT / Gemini 等）与维护者  
> **文档版本**：v9.5（实现版：全部章节均已实现；肉鸽 5 区 100 层/95 遗物仅本局、行动点仅入场、9 剧本/坏结局/混合 GM）  
> **实现状态**：**全部系统均已实现**。正文只保留规范、结构约束、红线与验收标准；已实现系统的**实现细节一律不在本文重复**，以 §10 索引与 §12/§13 的源码链接为准。  
> **路径说明**：文中源码链接均相对于 `ATRI-LK/plugins/lkpet/`；YAML 配置相对于 `ATRI-LK/`。

## 0. 规范约束（最高优先级）

```
1. 玩家有且仅有一只宠物，不可更换、不可多养、不可交易。
2. 养成系统为插件核心，所有玩法围绕宠物成长展开。
3. 使用 Python 编写，遵循 NoneBot2 插件规范。
4. 使用 OneBot v11 适配器。
5. 所有数值、文案、事件均外置为 YAML 配置文件。
6. 数据持久化见 database.py（已存在，不要另建独立数据库或 ORM）。
7. 服务注册在 __init__.py，指令注册在 matchers.py，不要重复生成注册代码。
8. 养成系统必须「复杂、长期化」，以「天」为基本时间单位。
9. **不包含种族系统**（无 dog/cat/dragon/elf 之分，宠物统一模型）。
10. **不包含进化系统。**
11. 包含轮回成就点系统。
12. 必须包含完整的新手引导流程（按天推进）。
13. **静态系统配置放在 `res/data/lkpet/`；运行时文件放在 `data/plugins/lkpet/`；
    数据库复用 ATRI-LK 原有数据库和 `database.py` 接口，通过多张表持久化。**
14. **轮回仅可由玩家主动发起并明确确认；日结算只能提示轮回条件，
    不得自动终止、死亡或重置宠物。**
15. **需要用户修改的配置项必须带默认值**：插件设置定义在 `plugins/lkpet/config.py`
    （参照 `plugins/lkchat/config.py` 的模式），字段全部给默认值，并在 `__init__.py`
    用 `plugin.add_plugin_config(LKPetConfig)` 注册；游戏数值仍全部外置 YAML。
16. **肉鸽与跑团是附加玩法（§12/§13）**：不得改变养成核心语义——年龄与状态只由日结算
    推进，玩法规则一律外置 YAML，奖励只在结算时写回宠物/轮回数据。
17. **跑团属性独立于养成五维**：存独立表，与 hp/atk/def/spd/mp 互不覆盖、互不换算。
```

数据目录约定：
- 静态数据根目录：`res/data/lkpet/`
- 配置子目录：`res/data/lkpet/config/`
- 用户数据：`data/plugins/lkpet/users/`
- 日志目录：`data/plugins/lkpet/logs/`
- 数据库：复用 ATRI-LK 原有数据库及 `database.py`，不得另建独立 `lkpet.db`

**依赖要点**（实现与修改时必须遵守，否则 `ImportError` 或逻辑漂移）：
- YAML 加载与路径工具是全部核心模块的运行前置：[config_loader.py](config_loader.py)、[utils/paths.py](utils/paths.py)。
- 状态衰减与健康/情绪判定**只在** [core/status.py](core/status.py) 实现，`core/daily.py` 委托调用，禁止复制副本。
- [core/affection.py](core/affection.py) 必须先于 [core/interaction.py](core/interaction.py) 存在（后者 import 前者的 `add_affection`）。
- `core/interaction.py` 与 `core/reincarnation.py` **不得**顶层 import `core/biography.py`；传记写入经可选回调注入，统一适配器在 [utils/callbacks.py](utils/callbacks.py)。

## 1. 项目元信息

```yaml
project:
  name: nonebot-plugin-mypet
  module: lkpet
  language: Python
  framework: NoneBot2
  adapter: OneBot V11
  base_project: ATRI-LK
  core_concept: 单一宠物深度养成 + 轮回成就 + 天为单位（无种族）
  persistence: database.py（PETDATA / PETREINCARNATION / PETBIOGRAPHY 三表）

paths:
    static_root: res/data/lkpet/
    config: res/data/lkpet/config/
    runtime_root: data/plugins/lkpet/
    users: data/plugins/lkpet/users/
    logs: data/plugins/lkpet/logs/
    database: ATRI-LK 原有数据库（见 database.py）

time_system:
  base_unit: day
  action_points_per_day: 10
  decay_per_day: true

design_principles:
  - 唯一性: 玩家只有一只宠物
  - 无种族: 所有宠物统一模型，仅性格与属性差异
  - 深度养成: 多维属性 + 多维状态 + 长期成长
  - 天为单位: 所有结算以天为粒度
  - 行动点限制: 每天有固定行动点，防止肝
  - 长期陪伴: 宠物传记 + 亲密度 + 记忆
  - 轮回重生: 宠物到达终点后轮回，继承成就点
  - 配置化: YAML 外置所有数值与文案
  - 状态联动: 心情/饥饿/疲劳互相影响
```

## 2. 数据目录结构

```
res/data/lkpet/
└── config/                     # 静态配置文件（YAML），见 §9
data/plugins/lkpet/
├── users/                      # 可选运行时缓存
└── logs/lkpet.log              # 插件日志
```

数据库（由 database.py 管理，位于 `data/sql/lkbot.db`）：
- `PETDATA` — 宠物主表（扩展既有表）
- `PETREINCARNATION` — 轮回数据
- `PETBIOGRAPHY` — 传记数据
- `PETROGUE` — 肉鸽进度（历史统计、首通解锁、进行中的 run）
- `PETTRPG` — 跑团卡与会话（组队会话用 `party:<群号>` 行存放）

### 2.1 路径工具

实现：[utils/paths.py](utils/paths.py) — 提供 `STATIC_DATA_ROOT` / `RUNTIME_DATA_ROOT` / `CONFIG_DIR` / `USERS_DIR` / `LOGS_DIR` 常量与 `ensure_dirs()` / `get_config_path()` / `get_user_path()`，复用 `ATRI.dir` 的框架常量，禁止各处硬编码路径。

## 3. 时间系统设计（核心）

### 3.1 时间粒度

| 单位 | 说明 |
|------|------|
| **天（Day）** | 最小结算单位 |
| **周** | 7 天，用于周常 |
| **月** | 30 天，用于月度活动 |
| **季** | 90 天，用于赛季 |
| **世** | 一次轮回的周期 |

每只宠物拥有独立时间线：创建时记录 `adopted_at` 和 `cycle_started_at`，以服务器本地日期与本世起始日期之差计算个人 `current_day`/`age_days`，不使用全服统一游戏纪元。`age_days` 表示本世实际经过的天数；轮回后本世年龄重置为 0，并将 `cycle_started_at` 更新为轮回时间。行动点仍在服务器时间每日 00:00 重置。

### 3.2 每日流程

```
每日 00:00（服务器时间）检查每只宠物是否进入新的个人日并触发「日结算」：
  1. 行动点重置为 10
  2. 状态按天衰减（饥饿-、心情-、疲劳-、清洁-）
     · 随后按「照护是否到位」判定亲密度衰减：饥饿/心情/清洁低于阈值、疲劳高于阈值
       各算一项未满足，未满足 1/2/3/4 项分别乘 1.0/1.1/1.3/1.5 的基础衰减（见 §9.1）
  3. 宠物年龄更新为本世起始后实际经过的天数（离线期间不丢失年龄）
  4. 检查健康状态与情绪状态
  5. 检查并提示是否满足手动轮回条件；日结算不得自动轮回、终止或重置宠物
  6. 记录到传记
  7. 发送「新的一天」通知
```

定时任务实现：[core/notify.py](core/notify.py)（cron 时间与推送群取自插件设置 `settle_check_hour/minute` / `notify_groups` / `settle_notify`）。

### 3.3 行动点（AP）系统

数值外置于 [../../res/data/lkpet/config/daily.yaml](../../res/data/lkpet/config/daily.yaml) 的 `action_points`（`max_per_day: 10`，各互动 `costs`）；校验与扣除实现于 [core/interaction.py](core/interaction.py) 的 `check_action_point` / `consume_action_point`。

## 4. 养成相关数据模型

> 使用 ATRI-LK 原有 `database.py`/`lk_db.get_table` 接口扩展多张表，不引入独立数据库或 ORM；保留并迁移既有 `PETDATA` 表及字段。
>
> **表归属**：[database.py](database.py) 是 `PETDATA` / `PETREINCARNATION` / `PETBIOGRAPHY` / `PETROGUE` / `PETTRPG` 五张表的**唯一注册方**，其他模块一律 `from .database import xxx_table`，不得再自行调用 `get_table`，否则会因同名表重复注册抛 `ValueError`。
>
> **读写接口**：`database.py` 提供通用的行↔模型映射 `table_field_pairs` / `load_model` / `save_model`，以及按表的 `load_reincarnation` / `save_reincarnation` / `load_biography` / `save_biography`。映射按 `PRAGMA table_info` 取实际列名，因此：
> - 遗留列（如 `PETDATA.LOVE`）会被自动跳过；
> - 字段名与列名不一致时用 `TABLE_FIELD_ALIASES` 对照（`PETDATA.ID` ↔ `player_id`）；
> - `datetime` 存 ISO 字符串、`dict`/`list` 存 JSON，解码依据 pydantic 字段注解自动判断。

### 4.1 Pet 宠物主表（无种族字段）

```yaml
Pet:
    table: PETDATA（扩展既有表）
    identity:
        player_id: int PK                  # 对应既有 PETDATA.ID；保证每人仅一只宠物
        adopted_at: timestamp not_null    # 首次领养时间
        cycle_started_at: timestamp not_null # 本世起始时间；领养时及每次轮回时设置

    # 基础信息（无种族）
    name: str(32)
    rarity: str(16)              # common/rare/epic/legendary/mythic
    personality: str(16)         # brave/timid/gentle/cunning/loyal/curious

    # 等级与经验
    level: int default 1
    exp: int default 0
    exp_next: int default 100

    # 属性
    attributes: JSON
    qualification: JSON
    trained_attributes: JSON    # 训练累计增益；升级重算属性时叠加回去，否则会被抹掉

    # 状态（按天衰减）
    mood: int default 80
    hunger: int default 80
    fatigue: int default 0
    cleanliness: int default 100
    affection: int default 0    # 0-1000（比原设计放大 10 倍，见 §5.1）
    health_state: str(16)
    emotion_state: str(16)

    # 年龄与寿命（天）
    age_days: int default 0
    lifespan_days: int default 120

    # 行动点
    action_points: int default 10
    ap_reset_day: int default 0

    # 每日追踪
    current_day: int default 0         # 个人日序号，由 cycle_started_at 推导/更新
    last_settlement_day: int default 0 # 已完成结算的个人日序号
    last_settlement_at: timestamp     # 最近一次结算时间，离线时据此计算经过天数
    last_login_at: timestamp nullable

    # 每日标记
    daily_flags: JSON
    weekly_flags: JSON
    monthly_flags: JSON

    # 技能
    skills: JSON
    skill_proficiency: JSON

    # 天赋
    talent_points: int default 0
    talents: JSON

    # 装备与外观
    equipment: JSON
    skin: str(32) default "default"
    title: str(64) nullable

    # 冷却
    cooldowns: JSON

    # 统计
    stats: JSON                  # 含新手引导进度 tutorial_step / tutorial_done

    # 轮回
    cycle_count: int default 0
    is_alive: bool default True
```

数据库迁移要求：为既有 `PETDATA` 增加养成字段时保留原有 `NAME`、`INSTRUCTION`、`LOVE` 等数据；新增字段 `TRAINED_ATTRIBUTES` 的迁移默认值为 `'{}'`，并把 `PETDATA_VERSION` 定为 1。

**好感度不做迁移**：旧 `LOVE` 是聊天时代的 0–100 好感，与新养成的 0–1000 **亲密度不是同一套语义**（判定、档位、加成全都不同），直接 ×10 沿用等于凭空给出高亲密度。因此**旧宠物的 `affection` 一律从 0 开始**，迁移里不做任何换算。`LOVE` 旧列原样保留在表中（不换算、也不清空），但已无代码读取它。

**版本号语义**：`PETDATA_VERSION` **不决定补哪些列**。`update_pet_db` 只接收 `version` 却从不使用它，实际做法是读 `PRAGMA table_info(PETDATA)`，把所有缺失的列 `ALTER TABLE ADD COLUMN` 补齐 —— 整个迁移是**幂等的列差异补齐**。版本号只决定 `DataBase.get_table` **要不要调用**这个迁移函数（`now_version != table_version`）。**以后每次改表结构都必须再次提升这个版本号**，否则已经到过该版本的库会因"版本相同"跳过迁移。

已有宠物的 `adopted_at` 与 `cycle_started_at` 初始化为迁移时间，`age_days`、个人日序号和结算时间从迁移日开始，不追溯计算迁移前的聊天宠物在线时长。新领养宠物的两个起始时间均设为当前时间。

**时间戳时区**：迁移填充 `ADOPTED_AT` / `CYCLE_STARTED_AT` / `LAST_SETTLEMENT_AT` 时**不得使用 SQLite 的 `CURRENT_TIMESTAMP`** —— 它是 UTC，会比插件其余部分（`ATRI.utils.datetime.now()`，默认 Asia/Shanghai）早 8 小时；在本地 00:00~08:00 之间迁移时 `cycle_started_at.date()` 会差一天，导致旧宠物白多结算一天。正确做法是用 `ATRI.utils.datetime.now()` 生成 `YYYY-MM-DD HH:MM:SS` 再以参数绑定写入。

**旧行的空字段**：迁移只填列默认值，旧行的 `ATTRIBUTES` / `QUALIFICATION` 会是 `'{}'`（`/宠物.状态` 显示 HP 0，且升级时公式重算也不会有成长）。由 [core/pet_core.py](core/pet_core.py) 的 `ensure_initialized` 在**首次读取**时补全并发牌落库。旧宠物也从未启动过新手引导，`tutorial.current_step` 对"未启动"的宠物返回 `None`，避免给老玩家弹「欢迎来到宠物世界」。

### 4.2 Reincarnation 轮回表

```yaml
Reincarnation:
    table: PETREINCARNATION
    identity:
        player_id: int PK
    achievement_points: int default 0
    spent_points: int default 0
    total_cycles: int default 0
    best_level: int default 1
    best_age_days: int default 0
    best_attributes: JSON       # 刷新 best_level 的同一快照写入
    permanent_bonuses: JSON
    unlocked_perks: JSON
    past_pets: JSON
    achievements_unlocked: JSON
    created_at: timestamp
    updated_at: timestamp
```

### 4.3 其他表

```yaml
Biography:
    table: PETBIOGRAPHY
    identity:
        player_id: int PK
    entries: JSON
    titles_unlocked: JSON
    key_choices: JSON
    memories: JSON
    daily_logs: JSON
    ending: str(64) nullable
```

技能与天赋的静态定义分别保存在 `skills.yaml`、`talents.yaml`；宠物已学习技能、熟练度及已分配天赋等级保存在 `PETDATA`，不重复建立静态配置数据库表。

## 5. 日结算系统 `core/daily.py`（核心）

实现：[core/daily.py](core/daily.py)

- `check_daily_reset(pet, *, daily_log_writer=…, reincarnation_checker=…)`：按个人日判断是否需要结算，需要则调用 `settle_days`。
- `settle_days` / `settle_one_day`：逐日推进 `age_days`、调用 `core/status.py` 衰减状态、重置行动点与每日标记、生成日志并经回调写入传记。
- 离线超过 `max_offline_days` 时**仍完整推进年龄并衰减状态**，只裁剪逐日明细（保留最近 N 天，`report_truncated` 置位）。
- `get_current_pet_day` 以服务器本地日期与 `cycle_started_at` 之差计算个人日；时间工具并入本模块，无独立 `utils/time.py`。
- 传记与轮回均通过可选回调注入，本模块不 import `core.biography` 与 `core.reincarnation`。

轮回可用性只作为日结算结果中的提示，不会调用 `perform_reincarnation`，也不会设置宠物死亡或重置状态。宠物仅在玩家主动执行轮回指令并明确确认后才结束当前一世。

### 5.1 状态按天衰减 `core/status.py`

实现：[core/status.py](core/status.py) —— 状态衰减与健康/情绪判定的**唯一实现**。

- 四项照护状态钳制到 `[下限, 100]`；`affection` 另有 0–1000 量表。
- 亲密度按「未满足照护项数」衰减：饥饿/心情/清洁低于阈值、疲劳高于阈值各算一项；1/2/3/4 项分别乘 1.0/1.1/1.3/1.5，全部满足不衰减。基础值、阈值、系数取自 daily.yaml 的 `affection_decay`。判定使用当天状态衰减**之后**的数值。
- 饥饿低于 30 时心情衰减额外加重。
- 健康四档按**严重度从高到低**判定（injured → sick → tired → healthy），保证四档均可命中（否则 `hunger < 10` 蕴含 `hunger < 20`、`fatigue > 95` 蕴含 `fatigue > 80`，会让 sick/injured 成为死代码）。情绪五档按 mood 判定（happy/normal/sad/angry/excited）。
- `apply_daily_decay` 返回**实际变化量**（钳制后的真实增减），而非配置里的名义值。

### 5.2 时间工具

已并入 [core/daily.py](core/daily.py)（`get_current_pet_day` 等），无独立 `utils/time.py`。

## 6. 养成核心模块

### 6.1 宠物属性系统 `core/attributes.py`

实现：[core/attributes.py](core/attributes.py)

- 无种族统一模型：五维（hp/atk/def/spd/mp）按 `pet.yaml` 的 base + growth × level × 随机资质（0.90–1.10）重算；luk/cha/int 另有等级/好感派生。
- cha 加成按好感 0–1000 量表换算（`pet.affection // 100`，仍映射 0–10 点）。
- 年龄加成（上限 +30%）、轮回永久加成、训练增益（`trained_attributes`，升级重算不丢失）、天赋效果依次叠加。
- 装备/称号加成走 `apply_equipment_bonus` / `apply_title_bonus`，最终值 `get_final_attributes`。

### 6.2 等级与经验系统 `core/leveling.py`

实现：[core/leveling.py](core/leveling.py)

- 经验系数由心情/饥饿/疲劳/亲密度（按 1000 量表）/性格/年龄/轮回加成相乘得出。
- `check_level_up` 循环升级：每级 +1 天赋点、触发技能解锁、重算属性。
- `exp_next = int(100 * level ** 1.8)`；任何地方不得写死 100。

### 6.3 技能系统 `core/skills.py`

实现：[core/skills.py](core/skills.py) —— 主动/被动槽位上限、熟练度增益与上限、熟练度对威力的加成比例均取自 `skills.yaml` 的 `limits`；`check_skill_unlock` 按等级解锁。

### 6.4 天赋系统 `core/talents.py`

实现：[core/talents.py](core/talents.py) —— 分配扣天赋点并即时叠加属性；重置按配置精确回退已叠加的属性并返还点数。

### 6.5 互动系统（消耗行动点）`core/interaction.py`

实现：[core/interaction.py](core/interaction.py)

- 配置一律走惰性 `_load_*_config()`，公开函数带可选 `*_config` 关键字参数，便于注入与测试。
- 状态写入统一用 `clamp_status(value, 0)` 钳制到 `[0, 100]`。
- 行动点按 daily.yaml 的 `costs` 校验与扣除（pet/rest 成本为 0）；疲劳 > 80 拦截训练。
- 训练增益同时累进 `trained_attributes`。
- **互动不写传记**：喂食/玩耍/洗澡等是 routine 行为；本模块不 import `core.biography`、不引用 `utils/callbacks`。传记由 `core.daily`（`daily_log_writer`）与 `core.reincarnation`（`biography_hook`）组装。

### 6.6 亲密度系统 `core/affection.py`

实现：[core/affection.py](core/affection.py)

- 量表 **0–1000**（放大 10 倍，使小数系数乘基础值 10 后仍是整数）。所有相关比值、档位、阈值都按 1000 换算。
- 档位：陌生(0–200) / 熟悉(200–400) / 友好(400–600) / 亲密(600–800) / 挚爱(800–950) / 灵魂伴侣(950–1000)。
- `HIGH_AFFECTION = 800`：达到后好感增益减半。
- `core/leveling` 的 `affection_mod` 与 `core/attributes` 的 cha 加成、轮回的 `per_200_affection` 权重均按 1000 量表换算。

### 6.7 轮回成就点系统 `core/reincarnation.py`

实现：[core/reincarnation.py](core/reincarnation.py)

- 读写走 `database.py` 的 `load_reincarnation` / `save_reincarnation`；模型就地修改后必须显式落库（积分、perk 兑换、宠物重置三处各自负责保存）。
- 传记写入走可选回调 `biography_hook`，不顶层 import `core.biography`。
- `perform_reincarnation(pet, player_id, manual=True, biography_hook=…)` 是**唯一**终止当前一世的入口；日结算只能调用 `check_reincarnation_available` 做提示。
- 等级门槛只拦「提前主动轮回」；寿命已尽或已满级属于自然终结，即使等级不足也放行。
- `reset_pet_for_reincarnation`：`exp_next` 必须用 `calc_exp_next(pet.level)`（起始等级可能被特权抬高）；一并清空 `trained_attributes`；保留轮回永久加成。
- 积分公式权重取自 `reincarnation.yaml`；好感每 200 点计一次权重。

### 6.8 传记系统 `core/biography.py`

实现：[core/biography.py](core/biography.py)

- 读写走 `load_biography` / `save_biography`；`get_or_create_biography` 每次返回新载入实例，因此**每个公开写函数都必须显式保存**。
- `unlock_title` 在**同一次载入**上同时写入称号与里程碑再保存，避免互相覆盖。
- 只有 `milestone` / `key_choice` / `battle_victory` / `first_time` / `ending` 五类事件落笔；`type == "ending"` 的条目同时写入 `Biography.ending`。
- 上限：条目 1000、每日日志 365，超出丢弃最旧的。

### 6.9 新手引导（按天推进）`core/tutorial.py`

实现：[core/tutorial.py](core/tutorial.py)

- 进度存 `pet.stats["tutorial_step"]` / `pet.stats["tutorial_done"]`（不存在玩家表，宠物是唯一的玩家级记录）。
- 步骤文案在 `texts.yaml` 的 `tutorial.steps`；`steps[].day` 仅作展示，推进按事件触发。
- `current_step` 对"未启动"（旧宠物）与"已完成"都返回 `None`。
- 创建流程完成后直接把引导推进到 `first_feed`（前三步在创建时已完成）。

## 7. 配置加载器 `config.py` / `config_loader.py`

实现：[config_loader.py](config_loader.py) —— `load_config(name)` 加载并缓存 `res/data/lkpet/config/*.yaml`，文件缺失抛 `FileNotFoundError`；`reload_all()` 清缓存。

### 7.1 插件设置 `config.py`（配置项必须带默认值）

实现：[config.py](config.py) —— `LKPetConfig` 全部字段带默认值（`enable` / `settle_notify` / `settle_check_hour` / `settle_check_minute` / `notify_groups` / `tutorial_enabled`），在 [__init__.py](__init__.py) 用 `plugin.add_plugin_config(LKPetConfig)` 注册，缺失时按默认值落盘到 `data/config/plugins/宠物_config.json`。

分工约定：
- `config.py` 只放**运行行为项**（开关、通知、定时、引导开关）；
- 游戏数值（属性成长、衰减系数、行动点上限、寿命等）只放在 `res/data/lkpet/config/*.yaml`，**不得在两处重复定义**。

## 8. 指令处理器

实现：[handlers/pet.py](handlers/pet.py)（注册见 [matchers.py](matchers.py)，入口见 [handlers/__init__.py](handlers/__init__.py)）

- 全部指令用 `plugin.cmd_as_group("子指令", …)` 注册在主命令 `宠物` 之下（§11 为命令唯一准）；`/帮助` **不要自己注册**，框架的「帮助」服务会自动列出。
- 回调接线：
  - `daily.check_daily_reset(pet, daily_log_writer=biography.add_daily_log, reincarnation_checker=reincarnation.check_reincarnation_available)`
  - `reincarnation.perform_reincarnation(pet, player_id, manual=True, biography_hook=biography.add_biography_entry)`
- **每个会改动宠物的处理器都要 `await pet_core.save_pet(pet)` 落库。**
- 拼接属性时注意 **`def` 是 Python 关键字**，不能用关键字参数传递，必须走字典展开。
- 新手引导进度在宠物身上（`pet.stats`），处理器通过 `_tutorial_prompt` 按事件推进。
- 用户可见文案一律经 [utils/texts.py](utils/texts.py) 查 `texts.yaml`，缺键或占位符对不上时回退默认值；展示层在 [utils/formatters.py](utils/formatters.py)。
- 事件监听（[handlers/events.py](handlers/events.py)）必须是**同步函数**（ATRI 的 `BaseEvents` 拒绝协程），用 `database.py` 提供的同步查询取数据。

## 9. 配置文件（置于 `res/data/lkpet/config/`）

全部已实现，直接看文件；本节只列各文件承载的内容：

| 文件 | 内容 |
|---|---|
| [daily.yaml](../../res/data/lkpet/config/daily.yaml) | **§9.1** 日结算：`max_offline_days`、状态下限、行动点与 costs、按天衰减、亲密度衰减（基础值/阈值/系数表） |
| [pet.yaml](../../res/data/lkpet/config/pet.yaml) | **§9.2** 基础属性、成长、性格（中文名与 exp_mod）、默认寿命、称号（无种族） |
| [interactions.yaml](../../res/data/lkpet/config/interactions.yaml) | **§9.3** 食物与互动效果（好感按 0–1000 量表计） |
| [skills.yaml](../../res/data/lkpet/config/skills.yaml) | **§9.4** 技能定义（主动 6 + 被动 6）、槽位 6/3、冷却与熟练度规则、解锁等级 |
| [talents.yaml](../../res/data/lkpet/config/talents.yaml) | **§9.5** 天赋树与每级效果 |
| [reincarnation.yaml](../../res/data/lkpet/config/reincarnation.yaml) | **§9.6** 等级上限/主动轮回门槛、积分权重、成就点特权 |
| [texts.yaml](../../res/data/lkpet/config/texts.yaml) | **§9.7** 全部用户可见文案：引导步骤、互动消息、状态卡、日结算报告、通知、肉鸽/跑团模板、聊天系统提示 `chat.system_prompt` |
| [achievements.yaml](../../res/data/lkpet/config/achievements.yaml) | **§9.8** 成就目录（预留，暂无消费者） |
| [roguelike.yaml](../../res/data/lkpet/config/roguelike.yaml) | **§12** 肉鸽：5 区域 100 层、遗物层/随机首领/补给层、精英规则、ATB 战斗参数、商店与奖励公式 |
| [rogue_events.yaml](../../res/data/lkpet/config/rogue_events.yaml) | **§12** 肉鸽区域事件：每区域 30 个，共 150 个（按区域分组抽取） |
| [rogue_relics.yaml](../../res/data/lkpet/config/rogue_relics.yaml) | **§12** 肉鸽遗物：普通 50 + Boss 专属 15（effect 键见文件头注释） |
| [trpg.yaml](../../res/data/lkpet/config/trpg.yaml) | **§13** 跑团：六维生成与派生公式、技能表、检定难度档、成长规则、三形式参数、奖励与每日限制 |
| [trpg_chapters.yaml](../../res/data/lkpet/config/trpg_chapters.yaml) | **§13** 跑团剧本场景图：单人 5 个 + 多人 3 个 + 短团本 1 个（trpg.yaml 的 chapters.file 指向它） |

> **§9.7 边界**：参数校验类提示（性格不合法、行动点不足、属性名错误等）属于校验信息而非展示文案，留在代码中与 `raise` 的异常一一对应。
>
> **§9.8 说明**：§0 的规范里没有「成就系统」一步，当前没有任何代码读取该文件。目录格式已固定备用：解锁结果写入 `Reincarnation.achievements_unlocked`，发放的点数计入 `Reincarnation.achievement_points`。

## 10. 实现文件索引

模块与职责一览（链接目标即权威实现）：

| 模块 | 文件 | 职责 |
|---|---|---|
| 服务注册 / 插件设置 | [__init__.py](__init__.py)、[config.py](config.py) | 服务与主命令 `宠物`、`LKPetConfig` 注册 |
| 数据层 | [database.py](database.py) | 五表唯一注册方、迁移、行↔模型映射、同步查询 |
| 配置加载 | [config_loader.py](config_loader.py) | YAML 加载与缓存 |
| 指令注册 | [matchers.py](matchers.py) | 23 条指令（全部在 `宠物` 命名空间下） |
| 指令处理器 | [handlers/pet.py](handlers/pet.py) | 全部指令处理 |
| 事件监听 | [handlers/events.py](handlers/events.py) | 用户信息卡、用户改名同步（同步函数） |
| 日结算 | [core/daily.py](core/daily.py) | 结算主体、个人日计算 |
| 状态 | [core/status.py](core/status.py) | 按天衰减、健康/情绪判定（唯一实现） |
| 属性 / 等级 | [core/attributes.py](core/attributes.py)、[core/leveling.py](core/leveling.py) | 属性重算、经验与升级 |
| 技能 / 天赋 | [core/skills.py](core/skills.py)、[core/talents.py](core/talents.py) | 解锁/熟练度、分配/重置 |
| 亲密度 / 互动 | [core/affection.py](core/affection.py)、[core/interaction.py](core/interaction.py) | 档位与增益、消耗行动点的互动 |
| 宠物主流程 | [core/pet_core.py](core/pet_core.py) | 创建/读取/保存/改名/设定、资质发牌、旧行补全 |
| 聊天 | [core/chat.py](core/chat.py) | LLM 会话管理（合并自旧 pet_chat.py） |
| 轮回 / 传记 | [core/reincarnation.py](core/reincarnation.py)、[core/biography.py](core/biography.py) | 成就点与重置、传记与称号 |
| 新手引导 | [core/tutorial.py](core/tutorial.py) | 按天推进的引导 |
| 定时通知 | [core/notify.py](core/notify.py) | 每日结算推送「新的一天」 |
| 工具 | [utils/paths.py](utils/paths.py)、[utils/callbacks.py](utils/callbacks.py)、[utils/texts.py](utils/texts.py)、[utils/formatters.py](utils/formatters.py) | 路径、回调适配、文案查表、展示层 |
| 肉鸽 | [core/roguelike.py](core/roguelike.py) | 爬塔 run 管理、楼层生成、自动战斗、结算写回 |
| 跑团 | [core/trpg.py](core/trpg.py)、[core/trpg_gm.py](core/trpg_gm.py) | 跑团属性与 d100 检定、成长；GM 场景推进与三形式会话 |
| 新表注册 | [database.py](database.py) | `PETROGUE` / `PETTRPG` 两表与 `Rogue` / `Trpg` 模型（database.py 仍是唯一注册方） |

验证脚本（位于 `ATRI-LK/tests/`）：[smoke_lkpet.py](../../tests/smoke_lkpet.py)（加载与指令注册）、[func_lkpet.py](../../tests/func_lkpet.py)（养成全链路）、[edge_lkpet.py](../../tests/edge_lkpet.py)（旧行迁移与离线结算）、[func_games.py](../../tests/func_games.py)（肉鸽/跑团全链路）。

## 11. 指令列表

> **命名空间**：全部指令挂在主命令 `宠物` 之下，即 `/宠物.xxx`。`训练` / `休息` / `技能` / `天赋` / `轮回` / `特权` 这类通用词**不得占据顶层** —— nonebot 对重复的指令前缀只保留第一个，第二个会被静默丢弃（仅打一条 warning）。命令表以本节为唯一准，**合并前的 `/pet.xxx` 写法不再保留**。

```
/宠物.创建 <性格> <名字> [设定]   创建宠物（仅一次）；性格可写中文名或英文键
/宠物.状态                        查看宠物状态
/宠物.结算                        手动触发日结算
/宠物.喂食 <食物>                 喂食（消耗行动点）
/宠物.玩耍                        玩耍（消耗行动点）
/宠物.抚摸                        抚摸
/宠物.洗澡                        洗澡（消耗行动点）
/宠物.训练 <属性>                 训练（消耗行动点）
/宠物.休息                        休息
/宠物.技能                        技能管理
/宠物.天赋 [天赋ID]               天赋分配
/宠物.传记                        查看传记
/宠物.日志                        查看最近日志
/宠物.轮回 [确认]                 查看轮回条件；带「确认」执行轮回
/宠物.特权 [特权ID]               查看 / 兑换成就点特权
/宠物 <内容>                      与宠物聊天（合并自旧插件）
/宠物.好感度                      查询亲密度（合并自旧插件）
/宠物.改名 <新名字>               改名（合并自旧插件）
/宠物.设定 <设定>                 修改聊天人设（合并自旧插件）
/宠物.列表                        列出本群宠物（合并自旧插件，ADMIN）
/宠物.全部                        列出所有宠物（合并自旧插件，MASTER）
/宠物.肉鸽 [动作]                 肉鸽爬塔（§12：开始/继续/进度/撤退，局内编号应答）
/宠物.跑团 [动作]                 跑团（§13：建卡/卡/重掷/开团/检定/进度/结束，团内自由文字）
/帮助                             由框架的「帮助」服务提供，自动列出以上指令
```

> 共 23 条指令，**全部在 `宠物` 命名空间下**（聊天指令就是主命令本身）。
>
> **性格参数**：`/宠物.创建` 的性格接受**中文名**（`勇敢` / `胆小` / `温和` / `狡猾` / `忠诚` / `好奇`），也兼容 `pet.yaml` 里的英文键（`brave` / `timid` / …），大小写与首尾空格都容错；落库统一存英文键，展示统一用中文名。提示语与创建成功卡都走 `describe_personalities()` / `personality_display()`，不要在文案里硬编码性格清单。
>
> **注册方式**：全部用 `plugin.cmd_as_group("状态", …)`。注意 `ATRI/service.py` 的 `cmd_as_group` 会 `kwargs.pop("aliases")` —— 真需要别名时得改用等价的 `plugin.on_command(("宠物", "状态"), aliases={...})`。
>
> **前缀说明**：`/宠物` 聊天指令拥有 `宠物` 前缀，所以不带点的 `/宠物XXX` 会落入聊天（最长前缀匹配），这是设计代价而非缺陷。
>
> **前缀是动态的**：文档与模板里的 `/` 是字面前缀；运行时由 `ATRI.bot.BotUtils.get_command_start()` 决定实际前缀，
> 全部用户可见文案在 `utils/texts.py` 出口统一替换（`apply_command_start`），改 `command_start` 配置无需改文案。

## 12. 肉鸽系统（爬塔闯关）

实现：[core/roguelike.py](core/roguelike.py)，配置 [roguelike.yaml](../../res/data/lkpet/config/roguelike.yaml)、
区域事件表 [rogue_events.yaml](../../res/data/lkpet/config/rogue_events.yaml) 与遗物表 [rogue_relics.yaml](../../res/data/lkpet/config/rogue_relics.yaml)，
持久化经 [database.py](database.py) 的 `Rogue` 模型与 `PETROGUE` 表（唯一注册方仍是 database.py；`PETROGUE_VERSION` 已升 1 以补 `RELICS` 列），
指令处理在 [handlers/pet.py](handlers/pet.py) 的 `handle_rogue`，文案在 [texts.yaml](../../res/data/lkpet/config/texts.yaml) 的 `rogue` 块。

### 12.1 结构约束（细节以源码与 YAML 为准，本文不再展开）

- **一塔 100 层 = 5 个区域 × 20 层**（青草平原 / 迷雾森林 / 熔岩洞窟 / 冰封王座 / 深渊之巅），区域间难度成倍跳升。
- 普通层**三选一**（战斗 / 事件 / 商店，**选中即开战**无二次确认）；事件每局只触发一次；
- **区域中点固定遗物三选一**（**仅本局生效**，存 `run_state.relics`）；**首领前一层唯一内容 = 回血补给**；
  **末层唯一内容 = 随机一个候选首领**（seed 可复现；Boss 专属遗物与 Boss 一对一，击杀即得）；
- 每区域 30 事件 / 10 小怪 / 5 精英备选（区域前 5 层不生成精英、每区域至多 4 次、倒数第二层保底）；
- **遗物 = 商店化**：95 件分五层rarity（普通 35 / 稀有 15 / 商店专属 20 / 事件专属 10 / Boss 专属 15）；
  商店货架固定 **普通 1 + 稀有 1 + 商店专属 1**（区域前 5 层不刷出，价格按 `shop.tier_price`）；
  事件专属遗物由 10 个指定事件的 `effect.relic` 掉落；
- **行动点仅入场需要**：入场 3 点，之后推进不再消耗（含前进/离开商店，即 `rogue_step: 0`）；
- 战斗为 **ATB 攻击条**模型（速度决定出击频率）+ **技能冷却** + **计时条每轮 +10、每满 100 全体冷却 -1**；
- 全部随机由 run seed 派生、同局可复现；局内会话（编号应答）随 run 持久化，掉线可续。

实现细节（精英与补给规则、随机首领、ATB/冷却/计时条参数、掉落与结算公式、遗物效果键）一律见
[core/roguelike.py](core/roguelike.py) 与配置 [roguelike.yaml](../../res/data/lkpet/config/roguelike.yaml) /
[rogue_events.yaml](../../res/data/lkpet/config/rogue_events.yaml) / [rogue_relics.yaml](../../res/data/lkpet/config/rogue_relics.yaml) /
[skills.yaml](../../res/data/lkpet/config/skills.yaml) —— **数值以 YAML 为准，不在本文档重复**。
指令见 §11（`/宠物.肉鸽 开始|继续|进度|撤退` + 局内编号应答）。

### 12.2 奖励与持久化（概要）

战败/撤退/通关按到达层数结算成就点（含 Boss 加成与通关大奖），Boss 首通另解锁装备/技能/称号；
遗物**只在本局生效**（`run_state.relics`，结算随 run 清空），获得时登记到 `PETROGUE.RELICS`
作「见过的遗物」图鉴（`RELICS` 列由 `PETROGUE_VERSION` 0→1 的幂等迁移补齐）；
历史进度与解锁清单同样存 `PETROGUE`；经验与首通奖励**只在结算时刻**写回。计算与写回逻辑见 `settle_run`。

### 12.3 红线

- 肉鸽**不推进年龄、不触发日结算衰减、不改好感**——日结算仍是年龄与状态的唯一推进者。
- run 内的临时生命/增益**永不直接写** `PETDATA` 的养成状态列；写回只发生在结算时刻（§12.2）。
- 所有数值外置 `roguelike.yaml` / `rogue_events.yaml` / `rogue_relics.yaml`（技能冷却在 `skills.yaml`），文案外置 `texts.yaml`。

## 13. 跑团系统（COC 风格 d100）

实现：[core/trpg.py](core/trpg.py)（六维/技能/检定/成长）、[core/trpg_gm.py](core/trpg_gm.py)（GM 与三形式会话），
配置 [trpg.yaml](../../res/data/lkpet/config/trpg.yaml) 与章节场景图 [trpg_chapters.yaml](../../res/data/lkpet/config/trpg_chapters.yaml)，
持久化经 [database.py](database.py) 的 `Trpg` 模型与 `PETTRPG` 表，
指令处理在 [handlers/pet.py](handlers/pet.py) 的 `handle_trpg`，文案在 texts.yaml 的 `trpg` 块。

### 13.1 结构约束（细节以源码与 YAML 为准，本文不再展开）

- **跑团属性独立于养成**：六维（STR/CON/DEX/POW/INT/APP，`3d6×5`，建卡可重掷一次）+ 百分比技能 +
  派生值，永久存 `PETTRPG`，与养成五维**互不覆盖、互不换算**（新表不动 `PETDATA_VERSION`）。
- **d100 检定**：六档（大成功/极难/困难/常规/失败/大失败，含技能 <50 时 96–100 大失败）；
  团后**成长检定** `d100 > 技能 → +1d10`（上限 95）——规则值全部在 `trpg.yaml`，实现在 `core/trpg.py`。
- **三形式并存**，共用同一套属性/检定/成长：**单人剧情 5 个剧本**按序推进（自由文字行动 + 关键抉择写传记
  `key_choice`）、**多人组队 3 个剧本**（群内 2–5 人，队长代提交、全体成员按 DEX 序检定、多数定分支、仅队长可结束）、
  **短团本 1 个**（≤30 分钟随开随玩）——共 9 个剧本，场景图在 `trpg_chapters.yaml`；
  **规模约束（冒烟校验）**：每章 ≥8 个场景、≥3 个结局、≥4 个多选分支场景，场景文本为多句氛围描写（≥40 字），
  全部场景从 start 可达（无死内容），「回复 1」最短链 ≤6 步通关（短团 ≤5 步），多人本开场第 1 项必带检定。
- **会话态**存 `PETTRPG.session`（组队用 `party:<群号>` 行），通关发放成就点并写传记 `memories`；
  通关/每日次数限制、奖励权重、三形式参数全部外置 `trpg.yaml`。
- **混合 GM**（`core/trpg_gm.py` 的 `llm_rule`）：编号/选项关键词/技能名命中走模板；
  **未命中的自由文字行动交 LLM 裁定**（限选卡内技能与难度、成败两段 ≤50 字叙事、禁止推进场景），
  15 秒超时、输出不合法、网络失败一律回退模板提示（`trpg.fallback`）——LLM 不可用不阻断跑团。
- **结局有代价**：终局场景带 `outcome: good|bad`；坏结局（共 11 个）**不发成就点**，
  成长与传记（失手记忆）照常，成败叙事随文本分发。

GM 场景推进、检定裁定、三形式会话与剧本结构的实现细节一律见
[core/trpg.py](core/trpg.py)、[core/trpg_gm.py](core/trpg_gm.py) 与配置
[trpg.yaml](../../res/data/lkpet/config/trpg.yaml) / [trpg_chapters.yaml](../../res/data/lkpet/config/trpg_chapters.yaml)——**不在本文档重复**。
指令见 §11（`/宠物.跑团 建卡|卡|重掷|开团|检定|进度|结束` + 团内自由文字）。

### 13.2 红线

- 章节/短团通关发放**少量轮回成就点**（权重在 `trpg.yaml`）并写入传记 `memories`；组队按各自参与单独记贡献；
  **坏结局（`outcome: bad`）不发成就点**，只写失手记忆——失败有重量。
- 跑团**不改养成五维与状态**；六维/技能的任何成长只落 `PETTRPG`。
- 自由文字输入只在**进行中的团**内生效；无团时提示用法，不落入聊天指令（本指令自身即 `跑团` 子命令，不存在前缀歧义）。
- 六维生成、技能表、难度档、成长上限、三形式参数（组队人数、短团时长）、奖励权重全部外置 `trpg.yaml`。

## 14. 验收标准（自检清单）

```
[ ] 所有状态按「天」衰减
[ ] 状态衰减与健康/情绪判定仅由 core/status.py 实现，core/daily.py 委托调用
[ ] 健康状态四档（healthy / tired / sick / injured）均可实际命中
[ ] 每项互动按 `daily.yaml` 的 `costs` 校验并扣除行动点（pet / rest 成本为 0）
[ ] `core/interaction.py` 不硬依赖 `core/biography.py`，传记写入走可选回调
[ ] `core/affection.py` 先生成，`core/interaction.py` 才能 import
[ ] 行动点系统生效（每天重置为 10）
[ ] 离线逐日明细最多保留 30 天；超过部分仍完整推进年龄并结算状态衰减
[ ] 离线状态衰减至 YAML 配置的最低值，不因超过 30 天而停止
[ ] 静态配置位于 res/data/lkpet/，运行时缓存和日志位于 data/plugins/lkpet/
[ ] 数据库复用 ATRI-LK 原有 database.py，并使用多表扩展
[ ] 宠物时间线按各自领养日期独立计算，不使用全服固定纪元
[ ] 年龄以天为单位累加
[ ] 寿命以天为单位计算
[ ] 日结算触发生成日志
[ ] 轮回成就点按综合公式计算
[ ] 轮回后保留永久加成
[ ] 轮回条件仅提供提示；只有玩家主动确认后才终止当前一世
[ ] 亲密度按「未满足的照护项数」衰减：1/2/3/4 项 → 基础值 ×1.0/1.1/1.3/1.5，全满足则不衰减
[ ] 好感量表为 0–1000；档位、`HIGH_AFFECTION`、`cha` 加成、`exp` 系数、轮回权重均按 1000 换算
[ ] 训练增益累计在 `trained_attributes`，升级重算属性后不会丢失
[ ] 轮回重置会一并清空 `trained_attributes`
[ ] `database.py` 是五张表的唯一注册方，其他模块引用其表对象，不再自行 `get_table`
[ ] `PETDATA` 从 version 0 迁移到 1 时补全全部养成列，旧数据（`NAME` / `INSTRUCTION` / `LOVE`）保留
[ ] 好感度**不做迁移**：旧宠物 `affection` 从 0 开始，`LOVE` 不换算也不清空
[ ] 轮回积分、perk 兑换、宠物重置三处改动都显式调用 `save_reincarnation` 落库
[ ] `reset_pet_for_reincarnation` 的 `exp_next` 用 `calc_exp_next(pet.level)`，不写死 100
[ ] `core/reincarnation.py` 的传记写入走可选回调，不硬依赖 `core/biography.py`
[ ] 互动不写传记；只有 `milestone` / `key_choice` / `battle_victory` / `first_time` / `ending` 五类事件入库
[ ] `core/biography.py` 每个公开写函数都显式 `save_biography`，改动不会静默丢失
[ ] `unlock_title` 在同一次载入里写完称号与里程碑，两者不会互相覆盖
[ ] `type == "ending"` 的条目同时写入 `Biography.ending`
[ ] 传记条目上限 1000 条、每日日志上限 365 条，超出丢弃最旧的
[ ] 15 条养成指令全部注册，且未与框架的「帮助」冲突
[ ] `plugins/lkpet` 可被机器人完整加载（导入即完成注册，无 `ImportError` / `ValueError`）
[ ] `handlers` 在 `__init__.py` 末尾导入，`matchers` 能拿到已定义的 `plugin` 与 `config`
[ ] 每个会改动宠物的处理器都 `await pet_core.save_pet(pet)` 落库
[ ] 日结算实际接上 `daily_log_writer`，轮回实际接上 `biography_hook`
[ ] 新宠创建时写入 `adopted_at` / `cycle_started_at` / 随机资质 / 属性 / `exp_next`
[ ] 用户可见文案全部来自 `texts.yaml`，缺键或占位符对不上时有默认值兜底
[ ] 新手引导进度存在 `pet.stats`，不依赖并不存在的玩家表
[ ] 轮回等级门槛只拦「提前主动轮回」，寿命已尽或满级即使等级不足也放行
[ ] 旧聊天宠物功能并入 `core/chat.py` 与 `handlers/events.py`，无独立旧模块
[ ] `__init__.py` 只保留服务注册与插件设置，全部指令与处理器在 `handlers/`
[ ] 旧指令（`/宠物` `/宠物.好感度` `/宠物.改名` `/宠物.设定` `/宠物.列表` `/宠物.全部`）全部并入并可用
[ ] 合并前的 `/pet.xxx` 写法已删除，命令表以 §11 为唯一准
[ ] 旧指令一律读 `database.py` 的 `PETDATA`，不再有内存缓存
[ ] `handlers/events.py` 的监听器是同步函数（`BaseEvents` 拒绝协程），用同步查询取数据
[ ] `/宠物` 聊天连续两条消息不再因历史渲染而崩溃
[ ] `/宠物.列表` 分页按 `PAGE_SIZE` 切片，不重复输出
[ ] 迁移后的旧行首次读取时补全 `ATTRIBUTES` / `QUALIFICATION` 并落库（HP 不为 0，升级有成长）
[ ] 旧宠物（从未启动引导）不会被弹「欢迎来到宠物世界」，`tutorial.current_step` 返回 None
[ ] 全部指令挂在主命令 `宠物` 之下（`/宠物.xxx`），通用词不占顶层
[ ] 需要别名的指令用 `on_command((main, sub), aliases=…)`，因为 `cmd_as_group` 会丢弃 aliases
[ ] `/宠物.状态` 等带点写法解析为自身，不带点的 `/宠物状态` 落入聊天（最长前缀匹配）
[ ] `/宠物.创建` 的性格接受中文名与英文键，落库存英文键、展示用中文名
[ ] 新手引导按天推进
[ ] 无种族系统
[ ] 无进化系统
[ ] 所有数值外置 YAML（res/data/lkpet/config/）
[ ] 插件设置 `plugins/lkpet/config.py` 所有字段带默认值，并已用 `plugin.add_plugin_config()` 注册
[ ] 游戏数值不在 `config.py` 与 YAML 两处重复定义
[ ] 静态系统配置统一在 res/data/lkpet/config/ 下
[ ] 可放置于 ATRI-LK plugins/ 目录直接运行
[ ] （§12 肉鸽）run 状态持久化于 PETROGUE，退出/重启后可「继续」
[ ] （§12 肉鸽）局内生命与增益不落盘到养成状态列；写回只发生在结算时刻
[ ] （§12 肉鸽）战败/撤退/通关均按到达层数结算成就点，Boss 层首通解锁专属装备/技能/称号
[ ] （§12 肉鸽）不推进年龄、不触发日结算衰减、不改好感
[ ] （§12 肉鸽）局内金币不持久化，run 结束清零，不引入第二持久货币
[ ] （§13 跑团）六维与技能存 PETTRPG，与养成五维互不覆盖、互不换算
[ ] （§13 跑团）d100 检定含 大成功/极难/困难/常规/失败/大失败 六档，大失败规则按技能值 <50 时 96–100
[ ] （§13 跑团）单人剧情 / 多人组队（2–5 人、DEX 轮序）/ 短团本 三形式均可用
[ ] （§13 跑团）成长检定只提升跑团技能，不触碰养成数值
[ ] （§13 跑团）关键抉择与通关记忆写入传记（key_choice / memories）
[ ] （§12/§13）两套新指令均注册在 `宠物` 命名空间下，不占顶层
[ ] （§12/§13）数值全部外置 roguelike.yaml / trpg.yaml，文案外置 texts.yaml
[ ] （§12/§13）新增 PETROGUE / PETTRPG 两表由 database.py 唯一注册，不自行 get_table
[ ] （§12 肉鸽）5 区域 × 20 层 = 100 层，区域间基础 HP 相邻 ≥2 倍
[ ] （§12 肉鸽）每区域中点固定遗物三选一；商店货架 = 普通1+稀有1+商店专属1，前 5 层不刷出
[ ] （§12 肉鸽）行动点仅入场消耗（rogue_step = 0），推进不扣点
[ ] （§12 肉鸽）ATB 攻击条（速度决定出击）+ 技能 cooldown + 计时条每轮 +10 / 每满 100 冷却 -1
[ ] （§12 肉鸽）遗物 95 件分层（35/15/20/10/15）仅本局生效，PETROGUE.RELICS 只作图鉴
[ ] （§13 跑团）每章 ≥8 场景 / ≥3 结局 / ≥4 多选分支；场景图无死内容；「回复 1」链 ≤6 步通关
[ ] （§13 跑团）混合 GM：模板优先，未命中自由行动由 LLM 裁定，失败回退模板（LLM 挂了不阻断）
[ ] （§13 跑团）终局带 outcome 标记；坏结局不发成就点，成长与传记照常
```

**用途**：作为 ATRI-LK 宠物插件的规范与验收标准  
**编写日期**：2025年  
**维护说明**：全部系统已实现，改行为前先阅读 §10 索引指向的源码；改 PETDATA 表结构须提升 `PETDATA_VERSION`（PETROGUE/PETTRPG 为独立新表，不涉迁移）；改数值只动 YAML，改文案只动 texts.yaml
