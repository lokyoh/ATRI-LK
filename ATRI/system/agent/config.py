import os
import re
import secrets
import string

import yaml

from ATRI.configs import Field, PluginConfig, Section
from ATRI.dir import SYS_CONFIG_DIR
from ATRI.exceptions import str_traceback
from ATRI.log import log

from .llm.model import DEVICE_ID_MAX, SLOT_SPECS
from .llm.provider import LLMProvider, ProviderManager, default_provider
from .permission_defaults import (
    DEFAULT_PERMISSION_OVERRIDES,
    DEFAULT_USER_PERMISSION_ITEM,
)
from .service import plugin

PROVIDER_CONFIG_FILE = SYS_CONFIG_DIR / "provider.yml"

_MODEL_OPTION_DEFAULT = "（默认分配）"

_MODEL_OPTION_DEFAULT_EMBEDDING = "（默认：取第一个）"


def _model_options(slot: str) -> list[dict]:
    """把该使用点**能用的**模型列成下拉选项。

    可选范围由 ``SLOT_SPECS`` 里的能力列表决定（例如图片可接受图片模型或
    通用模型）。清单来自 ``provider.yml``，类定义时还不知道，所以运行时生成。

    第一项固定是「留空 = 默认」，让用户有明确的「不指定」选项。
    """
    from .llm import SLOTS, llm_manager

    spec = SLOTS.get(slot)
    capabilities = spec.capabilities if spec else ()
    default_label = "（未配置，将告警）"
    models = []
    for capability in capabilities:
        try:
            models.extend(llm_manager.get_models(capability))
        except Exception:
            continue
    if slot == "embedding" and not models:
        default_label = _MODEL_OPTION_DEFAULT_EMBEDDING
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
    section_models = Section(
        "models",
        "模型选择",
        "每个使用点单独选一个模型。模型只按能力分类：通用 / 图片 / 语音 / 向量",
    )
    model_think = Field(
        str,
        "",
        "思考模型",
        "必填。负责决策、选工具、产出说话意图。缺失时 Agent 无法工作。",
        options_provider=lambda: _model_options("think"),
        required=True,
    )
    model_chat = Field(
        str,
        "",
        "聊天模型",
        "负责把意图变成自然聊天文本。留空时链路仍可运行，但 say/md 会降级为"
        "意图直发、face 跳过；建议配置。",
        options_provider=lambda: _model_options("chat"),
    )
    model_judge = Field(
        str,
        "",
        "判断模型",
        "主动聊天时必填。负责判断这条消息要不要接。",
        options_provider=lambda: _model_options("judge"),
    )
    model_memory = Field(
        str,
        "",
        "记忆模型",
        "记忆总结 / 抽取时必填。留空则记忆写入工具隐藏。",
        options_provider=lambda: _model_options("memory"),
    )
    model_profile = Field(
        str,
        "",
        "画像模型",
        "用户画像生成 / 修改。留空则只能读画像，不能改。",
        options_provider=lambda: _model_options("profile"),
    )
    model_schedule = Field(
        str,
        "",
        "日程模型",
        "填入式日程。留空则 plan 的填入不可用；固定作息不受影响。",
        options_provider=lambda: _model_options("schedule"),
    )
    model_outfit = Field(
        str,
        "",
        "着装模型",
        "白天 / 晚上两套着装。留空则提示词显示未决定。",
        options_provider=lambda: _model_options("outfit"),
    )
    model_home = Field(
        str,
        "",
        "家模型",
        "首次生成 / 扩写家的内容。留空则使用预设模板。",
        options_provider=lambda: _model_options("home"),
    )
    model_summary = Field(
        str,
        "",
        "总结模型",
        "转发消息 / 长文总结。留空则总结功能不可用。",
        options_provider=lambda: _model_options("summary"),
    )
    model_image = Field(
        str,
        "",
        "图片模型",
        "图片理解。可选图片模型或通用模型；留空则图片只记 [图片]。",
        options_provider=lambda: _model_options("vision"),
    )
    model_embedding = Field(
        str,
        "",
        "向量模型",
        "记忆检索。必须固定：换模型会让历史向量失效。",
        options_provider=lambda: _model_options("embedding"),
    )
    model_tts = Field(
        str,
        "",
        "语音模型",
        "语音回复。留空只发文字。",
        options_provider=lambda: _model_options("tts"),
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
        "「主动聊天白名单」在 proactively_chat 打开时计入。"
        "留空 = 不额外开启，仅依赖聊天插件的名单",
        item_type=str,
    )
    memory_prefetch_enabled = Field(
        bool,
        False,
        "自动回忆",
        "每轮按当前消息自动检索一次长期记忆，取最相关的 3 条注入提示词；"
        "没有向量模型时走关键词检索。默认关闭——开启后每轮多一次嵌入调用。",
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
        "主动开口总开关",
        "关闭时任何群都不会主动开新话题，下面的按群配置一律不生效。"
        "默认关闭：这比被动回复打扰得多，确认需要再开",
    )
    proactive_start = Field(
        dict,
        {},
        "主动开口（按群）",
        "按「机器人 ID → 群号」两级分组，每个群单独设置是否开口、节流与早间计划。"
        "两级都按 ID 精确匹配，不做通配；留空 = 任何群都不会主动开口。"
        "与「聊天」插件的主动聊天是两回事：那个让她在有人说话时接话，"
        "这个让她在没人说话时开新话题——打扰程度差一个数量级",
        ui_type="category-collection",
        category_label="label",
        key_label="机器人 ID",
        allow_add=True,
        allow_remove=True,
        item_field=Field(
            dict,
            {},
            "机器人",
            children={
                "label": Field(str, "", "备注", "只用于界面显示，不影响行为"),
                "chats": Field(
                    dict,
                    {},
                    "群配置",
                    "群号与「聊天白名单」一致；不在其中的群不会主动开口",
                    ui_type="category-collection",
                    key_label="群号",
                    allow_add=True,
                    allow_remove=True,
                    item_field=Field(
                        dict,
                        {},
                        "群",
                        children={
                            "enabled": Field(
                                bool,
                                True,
                                "启用主动开口",
                                "关闭后这个群只在被叫到时才回话",
                            ),
                            "interval": Field(
                                int,
                                120,
                                "最小间隔（分钟）",
                                "同一个群两次主动开口之间至少隔多久，建议 60-240",
                                min=10,
                            ),
                            "hour_start": Field(
                                int,
                                8,
                                "起始小时",
                                "每天从几点开始允许主动说话（0-23）",
                                min=0,
                                max=23,
                            ),
                            "hour_end": Field(
                                int,
                                24,
                                "结束小时",
                                "每天到几点为止允许主动说话（1-24，不含本小时）",
                                min=1,
                                max=24,
                            ),
                            "morning_plan": Field(
                                bool,
                                False,
                                "早上自己安排一天",
                                "每天在「早间计划小时」让她自己安排今天，"
                                "并决定要不要说早安。默认关闭",
                            ),
                            "morning_hour": Field(
                                int,
                                6,
                                "早间计划小时",
                                "每天几点（整点）执行早间计划，0-23",
                                min=0,
                                max=23,
                            ),
                        },
                    ),
                ),
            },
        ),
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
    section_advanced = Section(
        "advanced",
        "高级",
        "基本不用改的运行时标识；改动会影响缓存识别",
    )
    device_id = Field(
        str,
        "",
        "设备标识",
        "缓存标识的中间段，首次自动生成（英文数字，最多 8 字符）。改动会让已有缓存失效。",
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

    try:
        from .agent.model_health import log_health

        log_health()
    except Exception as e:
        log.warning(f"检查 Agent 模型健康状态失败：{e}")


MODEL_PREFERENCE_FIELDS: tuple[tuple[str, str], ...] = tuple(
    (spec.config_field, spec.key) for spec in SLOT_SPECS
)

DEVICE_ID_RE = re.compile(r"[A-Za-z0-9]+")


def _device_id() -> str:
    """配置里的设备标识；缺失或非法时生成并写回。"""
    current = str(config.get("device_id") or "").strip()
    if DEVICE_ID_RE.fullmatch(current) and len(current) <= DEVICE_ID_MAX:
        return current
    generated = "".join(
        secrets.choice(string.ascii_letters + string.digits)
        for _ in range(DEVICE_ID_MAX)
    )
    try:
        config.update({"device_id": generated})
    except Exception as e:
        log.warning(f"写入设备标识失败（将不发送缓存标识）：{e}")
    return generated


def _apply_model_preferences() -> None:
    """把「指定模型」同步给 LLMManager；必须在模型注册之后调用。"""
    try:
        from .llm import llm_manager

        llm_manager.device_id = _device_id()
        for field, slot in MODEL_PREFERENCE_FIELDS:
            wanted = str(config.get(field) or "").strip()
            kept = llm_manager.get_preferred(slot)
            ok = llm_manager.set_preferred(slot, wanted)
            if wanted and not ok:
                effective = kept or "按注册顺序第一个"
                log.warning(
                    f"配置项 {field} 指定的模型 '{wanted}' 不可用于「{slot}」，"
                    f"已忽略；当前仍使用 {effective}"
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
