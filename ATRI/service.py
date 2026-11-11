import asyncio
import inspect
import json
import re
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar, Literal

from nonebot import get_bot
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Message
from nonebot.dependencies import Dependent
from nonebot.matcher import Matcher, matchers
from nonebot.rule import (
    TRIE_VALUE,
    CommandRule,
    Rule,
    TrieRule,
    command,
    keyword,
    regex,
)
from nonebot.typing import (
    T_Handler,
    T_PermissionChecker,
    T_RuleChecker,
    T_State,
)
from pydantic import Field

from ATRI import driver
from ATRI.auth import ban as auth_ban
from ATRI.auth.reason import BlockReason
from ATRI.configs import PluginConfig
from ATRI.dir import CONFIG_DIR, PLUGIN_DATA_DIR
from ATRI.exceptions import (
    ReadFileError,
    ServiceNotFoundError,
    ServiceRegisterError,
    WriteFileError,
)
from ATRI.log import log
from ATRI.permission import Permission, get_master_ids, is_master_user
from ATRI.scheduler import SchedulerController
from ATRI.utils.model import BaseModel


class ServiceInfo(BaseModel):
    service: str
    docs: str
    version: str
    type: str
    author: str | None
    permission: str | None | list
    cmd_list: dict | None
    allow_switch: bool


class ServiceConfig(BaseModel):
    enabled: bool = True
    disable_user: list[str] = Field(default_factory=list)
    disable_group: list[str] = Field(default_factory=list)
    white_list_mode: bool = False
    white_list: list[str] = Field(default_factory=list)

    # ---- 运行时策略（阶段 2）----
    cd: float = 0.0
    """冷却时间（秒），0 表示不限制"""
    max_count: int = 0
    """统计窗口内允许的调用次数，0 表示不限制"""
    limit_window: float = 0.0
    """统计窗口长度（秒）"""
    limit_scope: Literal["user", "group", "both"] = "user"
    """计次 / 冷却的作用范围"""
    block_type: list[str] = Field(default_factory=list)
    """禁用的会话类型：private / group；空列表表示都允许"""
    min_level: int = 0
    """最低权限等级，0 表示不限制；仅群聊生效，主人不受限"""
    parent: str | None = None
    """父服务名；父服务关闭时本服务一并关闭"""
    ignore_statistics: bool = False
    """是否跳过调用统计"""
    ignore_prompt: bool = False
    """是否跳过拒绝提示（阶段 4 使用）"""
    withdraw_delay: float = 0.0
    """回复自动撤回延迟（秒），0 表示不撤回"""
    concurrency: int = 0
    """同一目标允许的并发处理数，0 表示不限制"""


_service_config_cache: dict[str, tuple[float, ServiceConfig]] = {}
"""服务配置缓存。{服务名: (文件 mtime, 配置对象)}"""


def _invalidate_service_config(service: str) -> None:
    """使指定服务的配置缓存失效。"""
    _service_config_cache.pop(service, None)


class CommandInfo(BaseModel):
    type: str
    docs: str
    aliases: list


class Service:
    """
    ATRI的服务。
    """

    driver_started = False

    class ServiceType(Enum):
        SYSTEM = "系统服务"
        LKPLUGIN = "LK扩展服务"
        FUNCTION = "功能性服务"
        ENTERTAINMENT = "娱乐服务"
        GAME = "游戏服务"
        SUBSCRIBE = "订阅服务"
        OTHER = "其他服务"
        CLOSED = "已关闭的服务"
        HIDDEN = "隐藏服务"

        @property
        def key(self) -> str:
            """稳定键（枚举名小写），供 WebUI 等前端作为数量字典的键使用。

            新增服务类型时无需改动前端：``SYSTEM`` -> ``system``，以此类推。
            """
            return self.name.lower()

    def __init__(
        self,
        service: str,
        docs: str = "无介绍",
        version: str = "",
        type_: ServiceType = ServiceType.OTHER,
        author: str | None = None,
    ):
        """
        初始化一个服务。
        :param service: 服务名
        :param docs: 服务文档
        :param version: 服务版本
        :param type_: 服务类型
        """
        if not service:
            raise ServiceRegisterError("未命名服务")
        if service in ServiceTools.service_list or service == "ATRI":
            raise ServiceRegisterError("服务重复注册或服务名违规")
        if type_ is self.ServiceType.CLOSED:
            raise ServiceRegisterError("无法注册`CLOSED`服务类型")
        self.service = service
        frame = inspect.currentframe()
        try:
            caller = frame.f_back if frame else None
            module_name = caller.f_globals.get("__name__") if caller else None
            if module_name and module_name.endswith(".service"):
                module_name = module_name.rsplit(".", 1)[0]
        finally:
            del frame
        if not module_name:
            raise ServiceRegisterError("无法解析运行时模块名")
        self.module_name = module_name
        self._docs = docs
        self._version = version
        self._type = type_
        self._author = author
        self._cmd_list = {}
        self._allow_switch = True
        self._permission = None
        self._priority = 10
        self._main_cmd = ("",)
        self._temp = False
        # 服务级运行时策略的默认值，由链式 API 写入；旧 JSON 缺键时用它补齐。
        # 服务开关 / 黑名单 / 白名单已由鉴权流水线统一处理，这里不再默认挂
        # is_in_service 规则（该函数仍然导出，外部插件可继续使用）。
        self._policy_defaults: dict[str, Any] = {}
        self._rule = Rule()
        self._handlers = None
        self._state = None
        self._path = PLUGIN_DATA_DIR / self.service
        self._scheduler_manager = None
        self._on_unload_handlers = []
        self.__generate_service_conf()
        ServiceTools.service_list[service] = self

    def document(self, context: str) -> "Service":
        """
        设置服务说明。
        :param context: 服务说明
        :return: 服务本身
        """
        self._docs = context
        return self

    def set_type(self, type_: ServiceType) -> "Service":
        """
        设置服务类型。
        :param type_: 服务类型
        :return: 服务本身
        """
        self._type = type_
        return self

    def version(self, version: str) -> "Service":
        """
        设置服务版本。
        :param version: 服务版本
        :return: 服务本身
        """
        self._version = version
        return self

    def author(self, author: str) -> "Service":
        """
        设置服务作者。
        :param author: 服务作者
        :return: 服务本身
        """
        self._author = author
        return self

    def allow_switch(self, _is: bool) -> "Service":
        """
        设置服务是否可开关。
        :param _is: 是否可开关
        :return: 服务本身
        """
        self._allow_switch = _is
        return self

    def rule(self, rule: Rule | T_RuleChecker | None) -> "Service":
        """
        为服务添加触发判定。
        :param rule: 触发判断
        :return: 服务本身
        """
        self._rule = self._rule & rule
        return self

    def cd(self, seconds: float) -> "Service":
        """设置服务冷却时间（秒）。``0`` 表示不限制。"""
        return self._set_policy(cd=max(0.0, float(seconds)))

    def limit(self, count: int, window: float, scope: str = "user") -> "Service":
        """设置统计窗口内允许的调用次数。

        :param count: 窗口内允许的次数，``0`` 表示不限制
        :param window: 窗口长度（秒）
        :param scope: 作用范围，``user`` / ``group`` / ``both``
        """
        if scope not in {"user", "group", "both"}:
            raise ValueError("limit scope 必须是 user / group / both")
        return self._set_policy(
            max_count=max(0, int(count)),
            limit_window=max(0.0, float(window)),
            limit_scope=scope,
        )

    def block_type(self, *types: str) -> "Service":
        """禁用指定会话类型：``private`` / ``group``。"""
        invalid = set(types) - {"private", "group"}
        if invalid:
            raise ValueError(f"未知会话类型：{sorted(invalid)}")
        return self._set_policy(block_type=list(dict.fromkeys(types)))

    def parent(self, service: str) -> "Service":
        """设置父服务；父服务关闭时本服务一并关闭。"""
        return self._set_policy(parent=service or None)

    def withdraw(self, delay: float) -> "Service":
        """设置回复自动撤回延迟（秒）。``0`` 表示不撤回。"""
        return self._set_policy(withdraw_delay=max(0.0, float(delay)))

    def ignore_statistics(self, _is: bool = True) -> "Service":
        """是否跳过调用统计。"""
        return self._set_policy(ignore_statistics=bool(_is))

    def ignore_prompt(self, _is: bool = True) -> "Service":
        """是否跳过拒绝提示（阶段 4 使用）。"""
        return self._set_policy(ignore_prompt=bool(_is))

    def concurrency(self, count: int) -> "Service":
        """设置同一目标的并发处理数。``0`` 表示不限制。"""
        return self._set_policy(concurrency=max(0, int(count)))

    def _set_policy(self, **values: Any) -> "Service":
        """写入策略默认值，并把缺失的键补进已存在的配置文件。

        已有 JSON 里的值优先，这里只补键，不覆盖用户配置。
        """
        self._policy_defaults.update(values)
        path = CONFIG_DIR / f"{self.service}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raw = {}
        except (OSError, json.JSONDecodeError):
            raw = {}
        missing = {k: v for k, v in values.items() if k not in raw}
        if not missing:
            return self
        raw.update(missing)
        try:
            path.write_text(
                json.dumps(raw, indent=4, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as e:
            raise WriteFileError("Write service config failed") from e
        _invalidate_service_config(self.service)
        return self

    def permission(self, perm: Permission) -> "Service":
        """
        为服务设置权限。
        :param perm: 权限
        :return: 服务本身
        """
        self._permission = perm
        return self

    def handlers(self, hand: list[T_Handler] | None) -> "Service":
        """
        为服务设置处理函数。
        :param hand: 处理函数列表
        :return: 服务本身
        """
        self._handlers = hand
        return self

    def temp(self, _is: bool) -> "Service":
        """
        设置是否为一次性服务。
        :param _is: 是否为一次性服务
        :return: 服务本身
        """
        self._temp = _is
        return self

    def priority(self, level: int) -> "Service":
        """
        为服务设置优先级。
        :param level: 优先级
        :return: 服务本身
        """
        self._priority = level
        return self

    def state(self, state: T_State | NotImplementedError) -> "Service":
        """
        为服务设置事件处理状态。
        :param state: 事件处理状态
        :return: 服务本身
        """
        self._state = state
        return self

    def main_cmd(self, cmd: str) -> "Service":
        """
        为服务命令设置前缀。
        :param cmd: 命令前缀
        :return: 服务本身
        """
        self._main_cmd = (cmd,)
        return self

    def is_nonebot_plugin(self) -> "Service":
        """设置插件为nonebot插件"""
        name = "请参考对应插件文档"
        self._cmd_list[name] = CommandInfo(
            type="ignore", docs="", aliases=[]
        ).model_dump()
        return self

    def get_path(self) -> Path:
        """获取服务专属路径"""
        if not self._path.exists():
            self._path.mkdir(parents=True, exist_ok=True)
        return self._path

    def __generate_service_conf(self):
        path = CONFIG_DIR / f"{self.service}.json"
        if path.is_file():
            return
        # 只写基础键：其余字段交给 ``ServiceConfig`` 的默认值，链式 API 调用后
        # 再由 ``_set_policy`` 把缺失的键补进来。这样插件声明的默认值不会被
        # 初始化时写下的 0 值抢先“钉死”。
        data = {"enabled": True, "white_list_mode": False, **self._policy_defaults}
        try:
            path.write_text(
                json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as e:
            raise WriteFileError("Write service config failed") from e
        _invalidate_service_config(self.service)

    def on_message(
        self,
        name: str = "",
        docs: str = "",
        rule: Rule | T_RuleChecker | None = None,
        permission: Permission | T_PermissionChecker | None = None,
        handlers: list[T_Handler | Dependent] | None = None,
        block: bool = True,
        priority: int = 10,
        state: T_State | None = None,
    ) -> type[Matcher]:
        if not rule:
            rule = self._rule
        if not permission:
            permission = self._permission
        if not handlers:
            handlers = self._handlers
        if not state:
            state = self._state

        if name:
            name = name + "-onmsg"
            self._cmd_list[name] = CommandInfo(
                type="message", docs=docs, aliases=[]
            ).model_dump()

        matcher = Matcher.new(
            "message",
            Rule() & rule,
            Permission() | permission,
            module=ModuleType(self.service),
            temp=self._temp,
            priority=priority,
            block=block,
            handlers=handlers,
            default_state=state,
        )
        matcher.service_name = self.service
        return matcher

    def on_notice(
        self,
        name: str,
        docs: str,
        block: bool = True,
        rule: Rule | T_RuleChecker | None = None,
    ) -> type[Matcher]:
        if not rule:
            rule = self._rule

        name = name + "-onntc"
        self._cmd_list[name] = CommandInfo(
            type="notice", docs=docs, aliases=[]
        ).model_dump()

        matcher = Matcher.new(
            "notice",
            Rule() & rule,
            Permission(),
            module=ModuleType(self.service),
            temp=self._temp,
            priority=self._priority,
            block=block,
            handlers=self._handlers,
            default_state=self._state,
        )
        matcher.service_name = self.service
        return matcher

    def on_request(self, name: str, docs: str, block: bool = True) -> type[Matcher]:
        name = name + "-onreq"
        self._cmd_list[name] = CommandInfo(
            type="request", docs=docs, aliases=[]
        ).model_dump()

        matcher = Matcher.new(
            "request",
            Rule() & self._rule,
            Permission(),
            module=ModuleType(self.service),
            temp=self._temp,
            priority=self._priority,
            block=block,
            handlers=self._handlers,
            default_state=self._state,
        )
        matcher.service_name = self.service
        return matcher

    def on_command(
        self,
        cmd: str | tuple[str, ...],
        docs: str,
        rule: Rule | T_RuleChecker | None = None,
        aliases: set[str | tuple[str, ...]] | None = None,
        block: bool = True,
        **kwargs,
    ) -> type[Matcher]:
        if not cmd:
            raise TypeError("cmd is required")
        if not docs:
            docs = "暂无描述"
        if not rule:
            rule = self._rule
        if not aliases:
            aliases = set()

        if isinstance(cmd, tuple):
            cmd = ".".join(map(str, cmd))

        self._cmd_list[cmd] = CommandInfo(
            type="command", docs=docs, aliases=list(aliases)
        ).model_dump()
        commands = {cmd} | (aliases or set())
        return self.on_message(rule=command(*commands) & rule, block=block, **kwargs)

    def on_keyword(
        self,
        keywords: set[str],
        docs: str,
        rule: Rule | T_RuleChecker | None = None,
        **kwargs,
    ) -> type[Matcher]:
        if not rule:
            rule = self._rule

        name = next(iter(keywords)) + "-onkw"
        self._cmd_list[name] = CommandInfo(
            type="keyword", docs=docs, aliases=list(keywords)
        ).model_dump()

        return self.on_message(rule=keyword(*keywords) & rule, **kwargs)

    def on_regex(
        self,
        pattern: str,
        docs: str,
        flags: int | re.RegexFlag = 0,
        rule: Rule | T_RuleChecker | None = None,
        **kwargs,
    ) -> type[Matcher]:
        if not rule:
            rule = self._rule

        self._cmd_list[pattern] = CommandInfo(
            type="regex", docs=docs, aliases=[]
        ).model_dump()

        return self.on_message(rule=regex(pattern, flags) & rule, **kwargs)

    def cmd_as_group(self, cmd: str, docs: str, **kwargs) -> type[Matcher]:
        if not cmd:
            raise TypeError("cmd is required")
        if not docs:
            docs = "暂无描述"
        sub_cmd = (cmd,)
        _cmd = self._main_cmd + sub_cmd

        if "aliases" in kwargs:
            kwargs.pop("aliases", None)

        return self.on_command(_cmd, docs, **kwargs)

    @staticmethod
    async def send_to_master(message: str | Message):
        """
        发送消息给主人。
        :param message: 消息
        """
        bot = get_bot()
        for m in get_master_ids():
            await bot.send_private_msg(user_id=m, message=message)

    def get_info(self) -> ServiceInfo:
        """
        获取该服务信息。
        :return: ServiceInfo对象
        """
        p = self._permission
        if p:
            p = p.name
        return ServiceInfo(
            service=self.service,
            docs=self._docs,
            version=self._version,
            type=str(self._type.value),
            author=self._author,
            permission=p,
            cmd_list=self._cmd_list,
            allow_switch=self._allow_switch,
        )

    def scheduler_jobs(self) -> SchedulerController:
        """该服务的计划任务控制器"""
        if not self._scheduler_manager:
            self._scheduler_manager = SchedulerController(self.service)
        return self._scheduler_manager

    def on_startup(self, func):
        """注册一个启动时执行的函数"""
        if not self.driver_started:
            driver().on_startup(func)
        else:
            if (func.__code__.co_flags & 80) != 0:
                asyncio.run(func())
            else:
                func()

    @staticmethod
    def rebuild_command_trie():
        """根据当前仍然存活的 matcher 规则重建命令前缀 trie。"""
        from itertools import product

        from pygtrie import CharTrie

        TrieRule.prefix = CharTrie()
        try:
            from nonebot import get_driver

            config = get_driver().config
            command_start = config.command_start
            command_sep = config.command_sep
        except Exception:
            return
        for matcher_group in matchers.values():
            for matcher in matcher_group:
                rule = getattr(matcher, "rule", None)
                if rule is None:
                    continue
                for checker in getattr(rule, "checkers", set()):
                    call = getattr(checker, "call", None)
                    if not isinstance(call, CommandRule):
                        continue
                    cmds = getattr(call, "cmds", None)
                    if not cmds:
                        continue
                    for cmd in cmds:
                        if len(cmd) == 1:
                            for start in command_start:
                                TrieRule.add_prefix(
                                    f"{start}{cmd[0]}", TRIE_VALUE(start, cmd)
                                )
                        else:
                            for start, sep in product(command_start, command_sep):
                                TrieRule.add_prefix(
                                    f"{start}{sep.join(cmd)}",
                                    TRIE_VALUE(start, cmd),
                                )

    async def unload(self):
        """服务卸载"""
        module_name = getattr(self, "module_name", None)
        service_name = getattr(self, "service", None)
        # 1. 清理当前服务注册的 Matcher
        for priority, matcher_group in list(matchers.items()):
            matcher_group[:] = [
                matcher
                for matcher in matcher_group
                if getattr(matcher, "service_name", None) != service_name
                and getattr(matcher, "module_name", None)
                not in {module_name, service_name}
            ]
            if not matcher_group:
                del matchers[priority]
        # 2. 重新构建命令前缀 trie，避免重载后残留旧命令前缀
        self.rebuild_command_trie()
        # 3. 清理当前服务注册的定时任务
        jobs = SchedulerController.service_schedulers.get(self.service, {})
        for job_name, job in list(jobs.items()):
            try:
                job.job.remove()
            except Exception as e:
                log.warning(f"移除定时任务 {job_name} 失败: {e}")
            finally:
                jobs.pop(job_name, None)
        SchedulerController.service_schedulers.pop(self.service, None)
        self._scheduler_manager = None
        # 4. 清理配置缓存，避免重载后读到旧对象
        _invalidate_service_config(self.service)
        # 5. 执行卸载回调，保证在清理结束后再释放资源
        hooks = list(getattr(self, "_on_unload_handlers", []))
        if hasattr(self, "_on_unload_handlers"):
            self._on_unload_handlers.clear()
        for func in hooks:
            if not callable(func):
                continue
            result = func()
            if inspect.isawaitable(result):
                await result

    def on_unload(self, func):
        """注册一个服务卸载时执行的函数"""
        self._on_unload_handlers.append(func)
        return func

    def conf(self) -> ServiceConfig:
        """
        获取服务的基础配置。
        :return: ServiceConfig对象
        """
        return ServiceTools(self.service).load_service_config()

    def plugin_config(self) -> PluginConfig | None:
        """
        获取服务的插件设置。
        :return: PluginConfig对象
        """
        return PluginConfig.get_config(self.service)

    def add_plugin_config(self, model: type[PluginConfig]) -> PluginConfig:
        """
        添加服务的插件设置。
        :param model: 插件设置模型
        :return: PluginConfig对象
        """
        return model(self.service)


class ServiceTools:
    """
    针对服务的工具类。
    """

    service_list: ClassVar[dict[str, Service]] = {}
    builtin_plugins: tuple = (
        "agent",
        "帮助",
        "图库",
        "用户",
        "群管",
        "主人",
        "管理",
        "插件商店",
        "更新",
        "调度管理",
        "备份",
        "WebAPI",
        "广播",
        "基础部件",
        "反馈",
        "重启",
        "状态",
        "年终总结",
        "运势",
        "聊天",
        "农场",
        "钓鱼",
        "宠物",
        "rss",
        "签到",
        "投喂",
    )

    def __init__(self, service: str):
        """
        针对服务的工具类。
        :param service: 服务名
        """
        if service not in self.service_list:
            raise ServiceNotFoundError("找不到指定服务")
        self.service = service

    def load_service(self) -> ServiceInfo:
        """
        获取服务信息。
        :return: ServiceInfo对象
        """
        return self.get_service(self.service).get_info()

    def save_service_config(self, service_config: ServiceConfig):
        """
        保存修改后的服务配置。
        :param service_config: 修改后的服务配置
        """
        path = CONFIG_DIR / f"{self.service}.json"
        service_config.write_into_file(path)
        _invalidate_service_config(self.service)

    def _load_service_config_cached(self) -> ServiceConfig:
        """读取服务配置（带 mtime 缓存）。

        读取时先把磁盘上的原始 JSON 与服务声明的 ``_policy_defaults`` 合并
        （只补缺失键，用户配置优先），再交给 ``ServiceConfig`` 校验。这样
        旧 JSON 缺新字段也能正常加载。

        返回的是缓存对象本身，调用方**不要直接修改**；需要修改时用
        :meth:`load_service_config` 取副本。
        """
        path = CONFIG_DIR / f"{self.service}.json"
        try:
            mtime = path.stat().st_mtime
        except OSError as e:
            raise ReadFileError(
                f"无法找到服务 {self.service} 对应的信息文件\n请重新启动"
            ) from e
        cached = _service_config_cache.get(self.service)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ReadFileError(
                f"无法读取服务 {self.service} 对应的信息文件\n请检查文件格式"
            ) from e
        if not isinstance(raw, dict):
            raw = {}
        defaults = getattr(self.get_service(self.service), "_policy_defaults", {})
        config = ServiceConfig.model_validate({**defaults, **raw})
        _service_config_cache[self.service] = (mtime, config)
        return config

    def load_service_config(self) -> ServiceConfig:
        """
        加载服务配置。
        :return: ServiceConfig对象
        """
        return self._load_service_config_cached().model_copy(deep=True)

    def del_service(self):
        """
        删除服务配置。
        """
        c_path = CONFIG_DIR / f"{self.service}.json"
        c_path.unlink(missing_ok=True)
        _invalidate_service_config(self.service)

    def check_service(
        self, user_id: str | None = None, group_id: str | None = None
    ) -> BlockReason | None:
        """当前服务对指定用户或群聊是否可用，返回原因码。

        :param user_id: 用户id
        :param group_id: 群聊id
        :return: 拦截原因；可用返回 ``None``
        """
        data = self._load_service_config_cached()
        if not data.enabled:
            return BlockReason.SERVICE_DISABLED
        parent_reason = self._check_parent(data.parent)
        if parent_reason is not None:
            return parent_reason
        if data.block_type:
            scope = "group" if group_id else "private"
            if scope in data.block_type:
                return BlockReason.SERVICE_BLOCKED_TYPE
        if user_id and user_id in data.disable_user:
            return BlockReason.SERVICE_USER_DISABLED
        if group_id:
            if group_id in data.disable_group:
                return BlockReason.SERVICE_GROUP_DISABLED
            if data.white_list_mode and group_id not in data.white_list:
                return BlockReason.SERVICE_GROUP_NOT_IN_WHITELIST
        if (
            data.min_level > 0
            and user_id
            and group_id
            and not is_master_user(user_id)
            and auth_ban.group_cache.level_of(group_id, user_id) < data.min_level
        ):
            return BlockReason.USER_LEVEL_TOO_LOW
        return None

    @classmethod
    def _check_parent(cls, parent: str | None) -> BlockReason | None:
        """沿父服务链向上检查，父服务被关闭时返回原因码。"""
        seen: set[str] = set()
        while parent and parent not in seen and len(seen) < 10:
            seen.add(parent)
            if parent not in cls.service_list:
                return None
            try:
                config = cls(parent)._load_service_config_cached()
            except ReadFileError:
                return None
            if not config.enabled:
                return BlockReason.SERVICE_PARENT_DISABLED
            parent = config.parent
        return None

    def auth_service(
        self, user_id: str | None = None, group_id: str | None = None
    ) -> bool:
        """
        当前服务对指定用户或群聊是否可用。
        :param user_id: 用户id
        :param group_id: 群聊id
        :return: 服务是否可用
        """
        reason = self.check_service(user_id, group_id)
        if reason is not None:
            log.debug(
                f"Service {self.service} blocked: reason={reason.value} "
                f"user_id={user_id or '-'} group_id={group_id or '-'}"
            )
        return reason is None

    def service_controller(self, is_enabled: bool):
        """
        启用或禁用服务。
        :param is_enabled: 是否启用
        """
        data = self.load_service_config()
        data.enabled = is_enabled
        self.save_service_config(data)

    @classmethod
    def get_service(cls, service) -> Service | None:
        if service in cls.service_list:
            return cls.service_list[service]
        return None

    @classmethod
    def get_typed_service_dict(cls):
        s_d = {}
        for s_t in Service.ServiceType:
            if s_t == Service.ServiceType.CLOSED:
                continue
            s_d[s_t.value] = []
        for s in cls.service_list.values():
            info = s.get_info()
            s_d.setdefault(info.type, []).append(info.service)
        return s_d


def is_in_service(service: str) -> Rule:
    async def _is_in_service(event: Event) -> bool:
        user_id = str(getattr(event, "user_id", ""))
        group_id = str(getattr(event, "group_id", ""))
        return ServiceTools(service).auth_service(user_id, group_id)

    return Rule(_is_in_service)


def driver_startup():
    import sys

    if "ATRI.auth.pipeline" not in sys.modules:
        log.warning("鉴权流水线未加载，服务级策略不会生效")
    log.success("启动函数执行完成")
    Service.driver_started = True
