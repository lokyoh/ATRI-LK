from nonebot.params import CommandArg

from ATRI.permission import MASTER
from ATRI.service import Service

from .config import BETA_BRANCH, OFFICIAL_BRANCH

plugin = (
    Service("更新", "ATRI-LK的更新服务", "0.5.0", Service.ServiceType.SYSTEM)
    .allow_switch(False)
    .permission(MASTER)
)

from .config import UpdateConfig  # noqa: E402

update_config: UpdateConfig = plugin.add_plugin_config(UpdateConfig)

from .data_source import Updater  # noqa: E402

check_update = plugin.on_command(cmd="检查更新", docs="检查最新版本", permission=MASTER)


@check_update.handle()
async def _():
    await check_update.finish(await Updater.check())


update = plugin.on_command(
    cmd="更新",
    docs="更新到当前通道的最新提交（仅适用于 git clone 部署）",
    permission=MASTER,
)


@update.handle()
async def _(args=CommandArg()):
    """不带参数按配置通道更新；带分支名可直接指定。"""
    branch = args.extract_plain_text().strip() or None
    if branch is not None and branch not in (OFFICIAL_BRANCH, BETA_BRANCH):
        await update.finish(
            f"未知分支：{branch}（可用：{OFFICIAL_BRANCH} 正式版 / {BETA_BRANCH} 测试版）"
        )
    await update.finish(await Updater.update(branch))


switch_channel = plugin.on_command(
    cmd="切换更新通道",
    docs=f"切换正式版（{OFFICIAL_BRANCH}）与测试版（{BETA_BRANCH}）",
    permission=MASTER,
)


@switch_channel.handle()
async def _(args=CommandArg()):
    channel = args.extract_plain_text().strip()
    if not channel:
        current = update_config.branch
        await switch_channel.finish(
            f"当前通道：{current}\n"
            f"用法：/切换更新通道 {OFFICIAL_BRANCH} 或 {BETA_BRANCH}"
        )
    await switch_channel.finish(await Updater.switch_channel(channel))
