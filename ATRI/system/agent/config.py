import os

import yaml

from ATRI.configs import Field, PluginConfig, Section
from ATRI.dir import SYS_CONFIG_DIR
from ATRI.exceptions import str_traceback
from ATRI.log import log

from .llm.provider import LLMProvider, ProviderManager, default_provider
from .permission_defaults import (
    DEFAULT_PERMISSION_OVERRIDES,
    DEFAULT_USER_PERMISSION_ITEM,
)
from .service import plugin

PROVIDER_CONFIG_FILE = SYS_CONFIG_DIR / "provider.yml"

_MODEL_OPTION_DEFAULT = "（默认分配）"

_MODEL_OPTION_DEFAULT_EMBEDDING = "（默认：取第一个）"


def _model_options(model_type: str) -> list[dict]:
    """把某类型下**已注册**的模型列成下拉选项。

    之所以要在运行时生成：模型清单来自 ``provider.yml``，类定义时还不知道。
    前端只要看到 ``options`` 非空就渲染成 ``el-select``，不需要改前端。

    第一项固定是「留空 = 默认」，这样用户有明确的「不指定」选项，
    而不是只能从具体模型里挑——否则一旦选过就再也回不到默认行为。
    """
    from .llm import llm_manager

    try:
        models = llm_manager.get_models(model_type)
    except Exception:
        models = []

    default_label = (
        _MODEL_OPTION_DEFAULT_EMBEDDING
        if model_type == "embedding"
        else _MODEL_OPTION_DEFAULT
    )
    if model_type.startswith("atri-"):
        # Agent 类型不允许「留空 = 随机分配」：留空意味着缺失，必须显式告警。
        default_label = "（未配置，将告警）"
    options: list[dict] = [{"label": default_label, "value": ""}]
    for m in models:
        label = m.name if m.name == m.model else f"{m.name}（{m.model}）"
        options.append({"label": label, "value": m.name})
    return options


def _validate_permission_users(value) -> str | None:
    """个人权限表的结构校验。

    值本身是 ``category-collection``，分类键是用户 ID；这里只检查每个
    条目内部的字段，分类键是否合法由分类集合统一处理。
    """
    if not isinstance(value, dict):
        return "必须是对象"
    for user_id, item in value.items():
        if not str(user_id).strip():
            return "用户 ID 不能为空"
        if not isinstance(item, dict):
            return f"{user_id}: 必须是对象"
        downgrade = str(item.get("downgrade_to") or "").strip().lower()
        if downgrade and downgrade not in {"admin", "user"}:
            return f"{user_id}: downgrade_to 只能是 admin 或 user"
        for key in ("allow", "deny"):
            patterns = item.get(key, [])
            if not isinstance(patterns, list) or not all(
                isinstance(pattern, str) for pattern in patterns
            ):
                return f"{user_id}: {key} 必须是字符串列表"
    return None


class AgentConfig(PluginConfig):
    _schema_version = "2.0"
    """配置版本；P0~P5 引入 atri-* 类型、三级权限、生活系统后升级到 2.0。"""

    def _migrate_saved(self, saved: dict) -> dict:
        """把 1.x 的配置迁移成 2.0 的形状。

        1.x 的 ``permission`` 是一张「按等级列出的功能清单」：``admin.allow``
        是 ``["*"]``、``user.allow`` 是一串旧名、``admin.deny`` 是一串混合了
        旧名与新名的条目。2.0 改成「默认由功能自己声明作用域、配置只记覆盖」，
        这张旧清单会让**群管理莫名失去**默认允许的生活/关系功能（例如
        ``go`` / ``love`` / ``rel_set``），与新模型正好相反。

        所以这里只保留 ``label``，把旧的 ``allow`` / ``deny`` 清空；同时删掉
        1.x 遗留、2.0 已不存在的等级键（旧版本曾有过 ``guest`` 之类的分类，
        留着会让整份配置每次保存都因「不允许新增分类」被拒）。
        迁移后的差异会被 ``PluginConfig.load`` 认出来并立即落盘，同时写一条
        说明日志。``model_tool`` 保留原值——它已不再生效，但要在
        ``_apply_model_preferences`` 里据此给出「请改配 atri-*」的告警。

        **只在真正的旧配置上执行一次**：用配置内的 ``schema_version`` 做标记。
        没有标记 = 1.x，执行迁移并写入标记；已经是 2.0 就原样返回，
        否则用户此后在 WebUI 填的每条覆盖都会在下次启动时被清空。
        """
        if not isinstance(saved, dict):
            return saved
        current = AgentConfig._schema_version
        version = str(saved.get("schema_version") or "").strip()
        if version == current:
            return saved

        changed = False
        permission = saved.get("permission")
        if isinstance(permission, dict):
            for key in list(permission):
                if key not in {"master", "admin", "user"}:
                    permission.pop(key, None)
                    changed = True
            for item in permission.values():
                if not isinstance(item, dict):
                    continue
                for key in ("allow", "deny"):
                    if item.get(key):
                        item[key] = []
                        changed = True
        saved["schema_version"] = current
        if changed:
            log.info(
                "Agent 配置已迁移到 v2：旧版按等级列出的权限清单已清空，"
                "默认权限改由每个功能自己声明的作用域决定；"
                "如需额外收紧或放宽，请到 WebUI 重新填写覆盖项"
            )
        return saved

    section_models = Section(
        "models",
        "模型选择",
        "决定用哪个模型跑哪类任务。Agent 只认 atri-* 类型，不回退到通用类型",
    )
    model_think = Field(
        str,
        "",
        "思考模型（atri-think）",
        "必填。负责决策、选工具、产出说话意图。缺失时 Agent 无法工作。",
        options_provider=lambda: _model_options("atri-think"),
        required=True,
    )
    model_chat = Field(
        str,
        "",
        "聊天模型（atri-chat）",
        "负责把意图变成自然聊天文本。留空时链路仍可运行，但 say/md 会降级为"
        "意图直发、face 跳过；建议配置。",
        options_provider=lambda: _model_options("atri-chat"),
    )
    model_judge = Field(
        str,
        "",
        "判断模型（atri-judge）",
        "主动聊天时必填。负责判断这条消息要不要接。",
        options_provider=lambda: _model_options("atri-judge"),
    )
    model_memory = Field(
        str,
        "",
        "记忆模型（atri-memory）",
        "记忆总结 / 抽取时必填。留空则记忆写入工具隐藏。",
        options_provider=lambda: _model_options("atri-memory"),
    )
    model_profile = Field(
        str,
        "",
        "画像模型（atri-profile）",
        "用户画像生成 / 修改。留空则只能读画像，不能改。",
        options_provider=lambda: _model_options("atri-profile"),
    )
    model_schedule = Field(
        str,
        "",
        "日程模型（atri-schedule）",
        "填入式日程。留空则 plan 的填入不可用；固定作息不受影响。",
        options_provider=lambda: _model_options("atri-schedule"),
    )
    model_outfit = Field(
        str,
        "",
        "着装模型（atri-outfit）",
        "白天 / 晚上两套着装。留空则提示词显示未决定。",
        options_provider=lambda: _model_options("atri-outfit"),
    )
    model_home = Field(
        str,
        "",
        "家模型（atri-home）",
        "首次生成 / 扩写家的内容。留空则使用预设模板。",
        options_provider=lambda: _model_options("atri-home"),
    )
    model_summary = Field(
        str,
        "",
        "总结模型（atri-summary）",
        "转发消息 / 长文总结。留空则总结功能不可用。",
        options_provider=lambda: _model_options("atri-summary"),
    )
    model_image = Field(
        str,
        "",
        "图片模型（atri-vision）",
        "图片理解。留空则图片只记 [图片]。",
        options_provider=lambda: _model_options("atri-vision"),
    )
    model_embedding = Field(
        str,
        "",
        "向量模型（atri-embedding）",
        "记忆检索。必须固定：换模型会让历史向量失效。",
        options_provider=lambda: _model_options("atri-embedding"),
    )
    model_tts = Field(
        str,
        "",
        "语音模型（atri-tts）",
        "语音回复。留空只发文字。",
        options_provider=lambda: _model_options("atri-tts"),
    )
    section_context = Section(
        "context",
        "上下文与思考",
        "影响体验和 token 成本：历史长度、思考轮数、输出节奏",
    )
    max_history = Field(
        int,
        30,
        "最大历史消息数",
        "包含Agent自己发送的消息,提高此值可使Agent获取更多历史聊天,但也会消耗更多Token,默认值30",
        min=10,
    )
    max_thinking_times = Field(
        int,
        20,
        "最大思考轮数",
        "允许Agent最高思考次数,当遇到需要大量思考情况可提高此值,默认值20",
        min=5,
    )
    max_side_effects_per_turn = Field(
        int,
        5,
        "单轮副作用上限",
        "一轮思考里最多执行多少次副作用调用（发消息、改好感、记记忆等）。"
        "超出部分会被忽略，防止模型一轮刷屏，默认值5",
        min=1,
    )
    skill_prompt_budget = Field(
        int,
        10000,
        "技能提示词预算",
        "技能目录 + 内嵌技能正文在提示词里的总字符预算。"
        "超出时先保证技能目录，再截断内嵌正文，默认值10000",
        min=1000,
    )
    outfit_night_hour = Field(
        int,
        20,
        "晚上穿搭切换点",
        "这个钟点之后显示晚上那套居家服/睡衣；凌晨 0-6 点同样按晚上算",
        min=0,
        max=23,
    )
    life_morning_plan = Field(
        bool,
        False,
        "早上自己安排一天",
        "开启后，每天早上 06:10 让她用 thinking 决定今天做什么、要不要说早安。"
        "只在主动发起白名单群生效。默认关闭",
    )
    schedule_summary_budget = Field(
        int,
        800,
        "日程提示词预算",
        "日程摘要进入提示词的最大字符数；超出时只保留当前与未来几段",
        min=100,
    )
    reply_max_chars = Field(
        int,
        300,
        "单条最大字数",
        "聊天输出单条上限；超出时按句子边界截断，默认值300",
        min=50,
        max=1000,
    )
    reply_inter_message_delay = Field(
        float,
        0.4,
        "条间延迟（秒）",
        "一次 say 分多条时，每条之间的自然停顿，避免像机器人连发；0-2 秒",
        min=0.0,
        max=2.0,
    )
    chat_parallel_render = Field(
        bool,
        False,
        "并行渲染",
        "默认关闭。开启后 outbox 的 worker 会把队列里的多条 say 同时交给"
        "聊天模型渲染，发送仍严格保序；渲染仍需调用模型，建议只在小群里开",
    )
    max_parallel_render = Field(
        int,
        2,
        "并行渲染上限",
        "并行渲染同时进行的条数上限（1-3）。关闭并行渲染时该值不生效",
        min=1,
        max=3,
    )
    agent_chat_whitelist = Field(
        list,
        [],
        "聊天白名单",
        "允许 Agent 聊天、并记录聊天历史的群号列表。"
        "实际生效范围是本列表与「聊天」插件白名单的**并集**："
        "聊天插件的「聊天白名单」在 chat_switch 打开时计入，"
        "其「主动聊天」里启用了的群也计入。"
        "留空 = 不额外开启，仅依赖聊天插件的名单",
        item_type=str,
    )
    section_proactive = Section(
        "proactive",
        "主动行为",
        "她主动插话与主动开口的频率、时段与目标群",
    )
    proactive_max_per_hour = Field(
        int,
        3,
        "主动发言上限",
        "每个群每小时最多主动发言几次。仅约束她主动插话，被@或叫到不受限。"
        "设太大会刷屏，建议 2-5",
        min=1,
    )
    proactive_cooldown = Field(
        int,
        300,
        "主动发言冷却",
        "两次主动发言之间至少间隔多少秒，防止连续插话",
        min=0,
    )
    proactive_start_enabled = Field(
        bool,
        False,
        "允许她主动开口",
        "开启后，她会时不时自己说一句话（不需要有人@或叫她）。"
        "只在「主动发起白名单」里的群生效，且仅当她想说一件有情绪的经历时才会开口。"
        "默认关闭：这比被动回复打扰得多，确认需要再开",
    )
    proactive_start_whitelist = Field(
        list,
        [],
        "主动发起白名单",
        "允许她**自己开口**的群号列表。与聊天插件的「主动聊天白名单」是两回事："
        "那个允许她在有人说话时接话，这个允许她在没人说话时开新话题——"
        "打扰程度差一个数量级，所以分开管。留空 = 任何群都不会主动开口",
        item_type=str,
    )
    proactive_start_interval = Field(
        int,
        120,
        "主动开口最小间隔",
        "同一个群两次主动开口之间至少隔多少分钟。设太小会变成刷屏，建议 60-240",
        min=10,
    )
    proactive_start_bot = Field(
        str,
        "",
        "主动开口使用的机器人",
        "填写机器人的 self_id（QQ 号）。机器人在多个账号下运行时，"
        "必须指定**真正在这些群里**的那个实例，否则会发不出去。"
        "留空 = 用第一个已连接的 OneBot 实例（单账号部署下没问题）",
    )
    proactive_start_hour_start = Field(
        int,
        8,
        "主动开口起始小时",
        "每天从几点开始允许她主动说话（0-23）。默认 8 点："
        "她 6 点起床，但刚醒就找人搭话很突兀。"
        "留 0 表示从零点就允许（不建议，深夜打扰）",
        min=0,
        max=23,
    )
    proactive_start_hour_end = Field(
        int,
        24,
        "主动开口结束小时",
        "每天到几点为止允许她主动说话（1-24，不含本小时）。默认 24 = 午夜前都行。"
        "注意 02:00-06:00 是她的睡眠时段，即使把范围设成 0-24 也不会在那时开口",
        min=1,
        max=24,
    )
    section_search = Section(
        "search",
        "联网搜索",
        "让她能查实时信息的搜索服务配置",
    )
    search = Field(
        dict,
        "搜索API配置",
        children={
            "enable": Field(bool, False, "启用联网搜索"),
            "provider": Field(str, "tavily", "搜索服务"),
            "api_key": Field(str, "", "搜索 API 密钥"),
        },
    )
    section_capabilities = Section(
        "capabilities",
        "能力开关",
        "只记例外：列表里是被禁用的功能/插件/技能",
    )
    function_calling_disable = Field(
        list,
        [],
        "禁用的功能调用",
        "列表中的功能不会出现在智能体功能列表中（不在列表内即启用）",
        item_type=str,
    )
    function_plugin_disable = Field(
        list,
        [],
        "禁用的功能插件",
        "列表中的功能插件整体关闭，其技能与功能调用同时失效（不在列表内即启用）",
        item_type=str,
    )
    skills_disable = Field(
        list,
        [],
        "禁用的技能",
        "列表中的技能不进入提示词、也不可被 agent 查询（不在列表内即启用）",
        item_type=str,
    )
    skills_embed = Field(
        list,
        [],
        "内嵌提示词的技能",
        "列表中的技能正文直接嵌入思考提示词，且不可被 agent 查询",
        item_type=str,
    )
    section_permission = Section(
        "permission",
        "三级权限",
        "默认由每个功能自己声明作用域，这里只记例外",
    )
    permission = Field(
        dict,
        DEFAULT_PERMISSION_OVERRIDES,
        "权限覆盖",
        "三级权限的**例外**配置。留空表示按每个功能自己声明的作用域执行；"
        "只有需要收紧或放宽时才在这里填功能名。能力上限由代码固定："
        "主人 bot / 管理 group / 用户 chat。",
        ui_type="category-collection",
        category_label="label",
        item_field=Field(
            dict,
            {},
            "等级覆盖",
            children={
                "label": Field(str, "", "显示名"),
                "allow": Field(
                    list,
                    [],
                    "额外允许",
                    "默认权限由功能自己声明；这里只填需要额外放行的功能名或插件名",
                    item_type=str,
                ),
                "deny": Field(
                    list,
                    [],
                    "额外禁止",
                    "优先于 allow；功能名或插件名；支持 * 通配与 插件名.*",
                    item_type=str,
                ),
            },
        ),
        fixed_keys=["master", "admin", "user"],
        validation_hint="默认权限由功能自己声明；这里只记录例外，能力上限不可配置",
    )
    permission_users = Field(
        dict,
        {},
        "个人权限覆盖",
        "按用户 ID 覆盖默认行为。留空表示不覆盖；只能在上限内收紧或放宽，"
        "不能提权；downgrade_to 只能向下（admin / user）。",
        ui_type="category-collection",
        item_field=Field(
            dict,
            DEFAULT_USER_PERMISSION_ITEM,
            "个人覆盖",
            children={
                "downgrade_to": Field(
                    str,
                    "",
                    "降级为",
                    "留空 = 不降级；只能填 admin 或 user",
                ),
                "allow": Field(
                    list,
                    [],
                    "额外允许",
                    "默认权限由功能自己声明；这里只填需要额外放行的功能名或插件名",
                    item_type=str,
                ),
                "deny": Field(list, [], "额外禁止", "优先于 allow", item_type=str),
            },
        ),
        key_label="用户 ID",
        validator=_validate_permission_users,
        validation_hint="分类键就是用户 ID；主人身份始终以运行时解析为准",
    )
    section_telemetry = Section(
        "telemetry",
        "行为数据收集",
        "记录指标用于评估改动效果，默认不落正文",
    )
    telemetry_enabled = Field(
        bool,
        False,
        "行为数据收集",
        "记录思考轮数/工具耗时/主动发言判断等指标到插件数据目录下的 telemetry/，"
        "用于评估改进效果。默认不记录消息正文",
    )
    telemetry_include_content = Field(
        bool,
        False,
        "数据收集包含正文",
        "开启后会在数据文件里记录消息正文片段（截断 120 字）。"
        "涉及聊天隐私，非排查问题不建议开启",
    )


config: AgentConfig = plugin.add_plugin_config(AgentConfig)


def apply_runtime_config() -> None:
    """把配置同步给运行时组件。

    配置改了不生效是最难排查的一类问题（用户以为改好了，行为却没变），
    所以凡是「配置 → 单例参数」的连线都集中在这里，启动和重载各调一次。
    """
    try:
        from .agent.initiative import configure

        configure(
            int(config.get("proactive_max_per_hour")),
            float(config.get("proactive_cooldown")),
        )
    except Exception as e:
        log.warning(f"应用主动发言配置失败，沿用默认值：{e}")

    try:
        from .agent.telemetry import telemetry

        telemetry.configure(
            enabled=bool(config.get("telemetry_enabled")),
            include_content=bool(config.get("telemetry_include_content")),
        )
    except Exception as e:
        log.warning(f"应用数据收集配置失败，沿用默认值：{e}")

    _apply_model_preferences()

    _report_schema_upgrade()

    try:
        from .agent.model_health import log_health

        log_health()
    except Exception as e:
        log.warning(f"检查 Agent 模型健康状态失败：{e}")


def _report_schema_upgrade() -> None:
    """配置版本升级提示。

    `PluginConfig` 的合并逻辑会保留旧键、新增字段用默认值；这里只负责把
    「升级后该检查什么」说清楚，避免用户以为 Agent 坏了。
    """
    try:
        from .service import plugin

        version_file = plugin.get_path() / "config_schema_version.txt"
        seen = ""
        if version_file.is_file():
            seen = version_file.read_text(encoding="utf-8").strip()
        current = AgentConfig._schema_version
        if seen == current:
            return
        plugin.get_path().mkdir(parents=True, exist_ok=True)
        version_file.write_text(current, encoding="utf-8")
        if seen:
            log.info(
                f"Agent 配置已从 v{seen} 升级到 v{current}；"
                "请检查模型设置（atri-* 类型）、三级权限与生活数据"
            )
    except Exception as e:
        log.debug(f"写入 Agent 配置版本提示失败（忽略）：{e}")


MODEL_PREFERENCE_FIELDS: tuple[tuple[str, str], ...] = (
    ("model_think", "atri-think"),
    ("model_chat", "atri-chat"),
    ("model_judge", "atri-judge"),
    ("model_memory", "atri-memory"),
    ("model_profile", "atri-profile"),
    ("model_schedule", "atri-schedule"),
    ("model_outfit", "atri-outfit"),
    ("model_home", "atri-home"),
    ("model_summary", "atri-summary"),
    ("model_image", "atri-vision"),
    ("model_embedding", "atri-embedding"),
    ("model_tts", "atri-tts"),
)


def _apply_model_preferences() -> None:
    """把「指定模型」配置同步给 LLMManager。

    必须在模型注册之后调用，否则查不到名字。启动流程里
    ``load_models_from_config()`` 在前、``apply_runtime_config()`` 在后，满足这一点。
    """
    try:
        from .llm import llm_manager
        from .llm.model import ModelType

        valid = {mt.value for mt in ModelType}
        for field, type_key in MODEL_PREFERENCE_FIELDS:
            if type_key not in valid:
                continue
            wanted = str(config.get(field) or "").strip()
            kept = llm_manager.get_preferred(type_key)
            ok = llm_manager.set_preferred(type_key, wanted)
            if wanted and not ok:
                effective = kept or (
                    "该类型注册的第一个"
                    if type_key.endswith("embedding")
                    else "按注册顺序第一个"
                )
                log.warning(
                    f"配置项 {field} 指定的模型 '{wanted}' 不存在或未注册为 "
                    f"{type_key} 类型，已忽略；{type_key} 当前仍使用 {effective}"
                )
        legacy_tool = str(config.get("model_tool") or "").strip()
        if legacy_tool and not any(
            str(config.get(f) or "").strip()
            for f in (
                "model_judge",
                "model_memory",
                "model_profile",
                "model_schedule",
                "model_summary",
            )
        ):
            log.warning(
                f"旧配置 model_tool='{legacy_tool}' 已不再生效；"
                "请分别配置 atri-judge / atri-memory / atri-profile / "
                "atri-schedule / atri-summary"
            )
    except Exception as e:
        log.warning(f"应用模型指定配置失败，沿用默认分配：{e}")


def reload_config():
    config.load()
    apply_runtime_config()


def load_provider_from_config():
    ProviderManager.clear_all()
    if not os.path.exists(PROVIDER_CONFIG_FILE):
        os.makedirs(os.path.dirname(PROVIDER_CONFIG_FILE), exist_ok=True)
        with open(PROVIDER_CONFIG_FILE, "w", encoding="utf-8") as file:
            yaml.dump(
                {default_provider.provider_name: default_provider.model_dump()},
                file,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
        return
    provider_data: dict = yaml.safe_load(PROVIDER_CONFIG_FILE.read_bytes())
    for _provider, value in provider_data.items():
        try:
            ProviderManager.register(LLMProvider(**value))
        except Exception as e:
            log.error(f"{_provider}无效配置:\n{str_traceback(e)}")
