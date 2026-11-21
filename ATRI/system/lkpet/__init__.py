"""lkpet · 赛博宠物养成插件。

本文件只负责服务注册与插件设置。**全部指令与事件处理器都在 ``handlers/`` 里**，
指令 matcher 在 ``matchers.py`` 注册。
"""

from contextlib import suppress

from ATRI.service import Service

from .config import LKPetConfig

plugin = Service(
    "宠物",
    "赛博宠物插件demo",
    "0.5.0",
    Service.ServiceType.GAME,
).main_cmd("宠物")
config: LKPetConfig = plugin.add_plugin_config(LKPetConfig)


def apply_runtime_config() -> None:
    """把配置同步给运行时组件。

    配置改了不生效是最难排查的一类问题，所以「配置 → 单例参数」的连线
    集中在这里，启动与重载各调一次。
    """
    try:
        from .core.endless.telemetry import configure

        configure(
            enabled=bool(config.get("telemetry_enabled")),
            include_content=bool(config.get("telemetry_include_content")),
        )
    except Exception:  # 埋点配置失败不应拦住插件加载
        pass


def reload_all() -> None:
    """WebUI 改配置后重载：重新读配置再同步给运行时组件。

    ``config.load()`` 是必须的——只调 apply 会把旧值再推一遍。
    """
    with suppress(Exception):
        config.load()
    apply_runtime_config()


@plugin.on_unload
def _on_unload() -> None:
    """卸载时断开埋点单例，避免热重载后旧实例继续写文件。"""
    try:
        from .core.endless.telemetry import configure

        configure(enabled=False, include_content=False)
    except Exception:
        pass


apply_runtime_config()

from . import handlers as handlers  # noqa: E402
