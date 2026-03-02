import asyncio
import re
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import List, Set, Tuple, Type, Union, Optional

from nonebot import get_bot
from nonebot.matcher import Matcher
from nonebot.dependencies import Dependent
from nonebot.typing import (
    T_State,
    T_Handler,
    T_RuleChecker,
    T_PermissionChecker,
)
from nonebot.rule import Rule, command, keyword, regex
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Message

from ATRI import service_list, driver
from ATRI.dir import CONFIG_DIR, PLUGIN_DATA_DIR
from ATRI.log import log
from ATRI.permission import Permission, MASTER_LIST
from ATRI.exceptions import ReadFileError, WriteFileError, ServiceNotFoundError, ServiceRegisterError
from ATRI.utils.model import BaseModel
from ATRI.utils.apscheduler import SchedulerController
from ATRI.configs import PluginConfig


class ServiceInfo(BaseModel):
    service: str
    docs: str
    version: str
    type: str
    permission: str | None | list
    cmd_list: dict | None


class ServiceConfig(BaseModel):
    enabled: bool = True
    disable_user: list = []
    disable_group: list = []
    white_list_mode: bool = False
    white_list: list = []


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

    def __init__(
            self,
            service: str,
            docs: str = "无介绍",
            version: str = str(),
            type_: ServiceType = ServiceType.OTHER
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
        if service in service_list or service == "master":
            raise ServiceRegisterError("服务重复注册或服务名违规")
        self.service = service
        self._docs = docs
        self._version = version
        self._type = type_
        self._cmd_list = {}
        self._permission = None
        self._priority = 10
        self._main_cmd = (str(),)
        self._temp = False
        self._rule = is_in_service(service)
        self._handlers = None
        self._state = None
        self._path = PLUGIN_DATA_DIR / self.service
        self._scheduler_manager = None
        self.__generate_service_conf()
        service_list[service] = self

    def document(self, context: str) -> "Service":
        """
        设置服务说明。
        :param context: 服务说明
        :return: 服务本身
        """
        self._docs = context
        return self

    def type(self, type_: ServiceType) -> "Service":
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

    def rule(self, rule: Optional[Union[Rule, T_RuleChecker]]) -> "Service":
        """
        为服务添加触发判定。
        :param rule: 触发判断
        :return: 服务本身
        """
        self._rule = self._rule & rule
        return self

    def permission(self, perm: Permission) -> "Service":
        """
        为服务设置权限。
        :param perm: 权限
        :return: 服务本身
        """
        self._permission = perm
        return self

    def handlers(self, hand: Optional[List[T_Handler]]) -> "Service":
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

    def state(self, state: Optional[T_State]) -> "Service":
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
        self._cmd_list[name] = CommandInfo(type="ignore", docs=str(), aliases=list()).model_dump()
        return self

    def get_path(self) -> Path:
        """获取服务专属路径"""
        return self._path

    def __generate_service_conf(self):
        path = CONFIG_DIR / f"{self.service}.json"
        if path.is_file():
            return
        data = ServiceConfig(
            enabled=True,
            disable_user=list(),
            disable_group=list(),
            white_list_mode=False,
            white_list=list(),
        )
        try:
            data.write_into_file(path)
        except Exception:
            raise WriteFileError("Write service config failed")

    def on_message(
            self,
            name: str = str(),
            docs: str = str(),
            rule: Optional[Union[Rule, T_RuleChecker]] = None,
            permission: Optional[Union[Permission, T_PermissionChecker]] = None,
            handlers: Optional[List[Union[T_Handler, Dependent]]] = None,
            block: bool = True,
            priority: int = 10,
            state: Optional[T_State] = None,
    ) -> Type[Matcher]:
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
                type="message", docs=docs, aliases=list()
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
        return matcher

    def on_notice(self, name: str, docs: str, block: bool = True) -> Type[Matcher]:
        name = name + "-onntc"
        self._cmd_list[name] = CommandInfo(type="notice", docs=docs, aliases=list()).model_dump()

        matcher = Matcher.new(
            "notice",
            Rule() & self._rule,
            Permission(),
            module=ModuleType(self.service),
            temp=self._temp,
            priority=self._priority,
            block=block,
            handlers=self._handlers,
            default_state=self._state,
        )
        return matcher

    def on_request(self, name: str, docs: str, block: bool = True) -> Type[Matcher]:
        name = name + "-onreq"
        self._cmd_list[name] = CommandInfo(type="request", docs=docs, aliases=list()).model_dump()

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
        return matcher

    def on_command(
            self,
            cmd: Union[str, Tuple[str, ...]],
            docs: str,
            rule: Optional[Union[Rule, T_RuleChecker]] = None,
            aliases: Optional[Set[Union[str, Tuple[str, ...]]]] = None,
            block: bool = True,
            **kwargs,
    ) -> Type[Matcher]:
        if not cmd:
            raise TypeError("cmd is required")
        if not docs:
            docs = '暂无描述'
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
            keywords: Set[str],
            docs: str,
            rule: Optional[Union[Rule, T_RuleChecker]] = None,
            **kwargs,
    ) -> Type[Matcher]:
        if not rule:
            rule = self._rule

        name = list(keywords)[0] + "-onkw"
        self._cmd_list[name] = CommandInfo(type="keyword", docs=docs, aliases=list(keywords)).model_dump()

        return self.on_message(rule=keyword(*keywords) & rule, **kwargs)

    def on_regex(
            self,
            pattern: str,
            docs: str,
            flags: Union[int, re.RegexFlag] = 0,
            rule: Optional[Union[Rule, T_RuleChecker]] = None,
            **kwargs,
    ) -> Type[Matcher]:
        if not rule:
            rule = self._rule

        self._cmd_list[pattern] = CommandInfo(type="regex", docs=docs, aliases=list()).model_dump()

        return self.on_message(rule=regex(pattern, flags) & rule, **kwargs)

    def cmd_as_group(self, cmd: str, docs: str, **kwargs) -> Type[Matcher]:
        if not cmd:
            raise TypeError("cmd is required")
        if not docs:
            docs = '暂无描述'
        sub_cmd = (cmd,) if isinstance(cmd, str) else cmd
        _cmd = self._main_cmd + sub_cmd

        if "aliases" in kwargs:
            del kwargs["aliases"]

        return self.on_command(_cmd, docs, **kwargs)

    @staticmethod
    async def send_to_master(message: Union[str, Message]):
        """
        发送消息给主人。
        :param message: 消息
        """
        bot = get_bot()
        for m in MASTER_LIST:
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
            type=self._type.value,
            permission=p,
            cmd_list=self._cmd_list
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

    def conf(self) -> ServiceConfig:
        """
        获取服务的配置。
        :return: ServiceConfig对象
        """
        return ServiceTools(self.service).load_service_config()

    def plugin_config(self) -> PluginConfig:
        """
        获取服务的插件设置。
        :return: PluginConfig对象
        """
        return PluginConfig.get(self.service)

    def add_plugin_config(self, model: Type[BaseModel]) -> PluginConfig:
        """
        添加服务的插件设置。
        :param model: 插件设置模型
        :return: PluginConfig对象
        """
        return PluginConfig(self.service, model)


class ServiceTools:
    """
    针对服务的工具类。
    """

    def __init__(self, service: str):
        """
        针对服务的工具类。
        :param service: 服务名
        """
        if not service in service_list:
            raise ServiceNotFoundError("找不到指定服务")
        self.service = service

    def load_service(self) -> ServiceInfo:
        """
        获取服务信息。
        :return: ServiceInfo对象
        """
        return service_list[self.service].get_info()

    def save_service_config(self, service_config: ServiceConfig):
        """
        保存修改后的服务配置。
        :param service_config: 修改后的服务配置
        """
        path = CONFIG_DIR / f"{self.service}.json"
        service_config.write_into_file(path)

    def load_service_config(self) -> ServiceConfig:
        """
        加载服务配置。
        :return: ServiceConfig对象
        """
        path = CONFIG_DIR / f"{self.service}.json"
        if not path.is_file():
            raise ReadFileError(
                f"无法找到服务 {self.service} 对应的信息文件\n"
                f"请重新启动"
            )
        return ServiceConfig.read_from_file(path)

    def del_service(self):
        """
        删除服务配置。
        """
        c_path = CONFIG_DIR / f"{self.service}.json"
        c_path.unlink()

    def auth_service(self, user_id: str = None, group_id: str = None) -> bool:
        """
        当前服务对指定用户或群聊是否可用。
        :param user_id: 用户id
        :param group_id: 群聊id
        :return: 服务是否可用
        """
        data = self.load_service_config()
        auth_global = data.enabled
        if not auth_global:
            return False
        auth_user = data.disable_user
        if user_id and user_id in auth_user:
            return False
        auth_group = data.disable_group
        if group_id:
            if group_id in auth_group:
                return False
            if data.white_list_mode and not group_id in data.white_list:
                return False
        return True

    def service_controller(self, is_enabled: bool):
        """
        启用或禁用服务。
        :param is_enabled: 是否启用
        """
        data = self.load_service_config()
        data.enabled = is_enabled
        self.save_service_config(data)


def is_in_service(service: str) -> Rule:
    async def _is_in_service(event: Event) -> bool:
        user_id = str(getattr(event, "user_id", ""))
        group_id = str(getattr(event, "group_id", ""))
        return ServiceTools(service).auth_service(user_id, group_id)

    return Rule(_is_in_service)


def driver_startup():
    log.success("启动函数执行完成")
    Service.driver_started = True
