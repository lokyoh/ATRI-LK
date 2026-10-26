import os

import yaml

from ATRI.configs import Field, PluginConfig
from ATRI.dir import SYS_CONFIG_DIR
from ATRI.log import log

from ...exceptions import str_traceback
from .llm.provider import LLMProvider, ProviderManager, default_provider
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
    options: list[dict] = [{"label": default_label, "value": ""}]
    for m in models:
        label = m.name if m.name == m.model else f"{m.name}（{m.model}）"
        options.append({"label": label, "value": m.name})
    return options


class AgentConfig(PluginConfig):
    # ---- 模型选择（最常改：决定用哪个模型跑哪类任务）----
    model_chat = Field(
        str,
        "",
        "聊天模型",
        "指定用于思考与回复的模型。留空 = 按默认分配：同类型注册了多个时随机挑一个",
        options_provider=lambda: _model_options("chat"),
    )
    model_tool = Field(
        str,
        "",
        "工具模型",
        "指定用于记忆总结、主动回复判断等辅助任务的模型。留空 = 按默认分配",
        options_provider=lambda: _model_options("tool"),
    )
    model_image = Field(
        str,
        "",
        "图片模型",
        "指定用于图片识别的模型。留空 = 按默认分配",
        options_provider=lambda: _model_options("image"),
    )
    model_tts = Field(
        str,
        "",
        "语音模型",
        "指定用于把回复转成语音的 tts 模型。留空 = 按默认分配。"
        "只有发送语音的通道（OneBot）会用到它；未配置 tts 模型时回复只发文字",
        options_provider=lambda: _model_options("tts"),
    )
    model_embedding = Field(
        str,
        "",
        "向量模型",
        "指定用于记忆检索的 embedding 模型。留空 = 取该类型下注册的第一个。"
        "必须固定：向量只在同一模型的语义空间内可比，换成另一个模型会让"
        "已有记忆的向量全部失效（检索不到，且不会报错）",
        options_provider=lambda: _model_options("embedding"),
    )
    # ---- 上下文与思考（影响体验和 token 成本）----
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
    # ---- 主动行为（她主动插话的频率）----
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
    # ---- 联网搜索 ----
    search = Field(
        dict,
        "搜索API配置",
        children={
            "enable": Field(bool, False, "启用联网搜索"),
            "provider": Field(str, "tavily", "搜索服务"),
            "api_key": Field(str, "", "搜索 API 密钥"),
        },
    )
    # ---- 能力开关（只记例外：列表里是禁用的）----
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
    # ---- 行为数据收集（一次性开关）----
    telemetry_enabled = Field(
        bool,
        True,
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


MODEL_PREFERENCE_FIELDS: tuple[tuple[str, str], ...] = (
    ("model_chat", "chat"),
    ("model_image", "image"),
    ("model_tool", "tool"),
    ("model_tts", "tts"),
    ("model_embedding", "embedding"),
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
                    "该类型注册的第一个" if type_key == "embedding" else "按默认分配"
                )
                log.warning(
                    f"配置项 {field} 指定的模型 '{wanted}' 不存在或未注册为 "
                    f"{type_key} 类型，已忽略；{type_key} 当前仍使用 {effective}"
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
