import os
import re
import json
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
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import Message, PrivateMessageEvent, GroupMessageEvent

from ATRI import service_list
from ATRI.permission import Permission, MASTER_LIST
from ATRI.exceptions import ReadFileError, WriteFileError, ServiceNotFoundError, ServiceRegisterError
from ATRI.utils.model import BaseModel
from ATRI.log import log

SERVICES_DIR = Path(".") / "data" / "services"
SERVICES_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR = Path(".") / "data" / "config"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)


class ServiceInfo(BaseModel):
    service: str
    docs: str
    version: str
    type: str
    permission: list
    cmd_list: dict


class ServiceConfig(BaseModel):
    enabled: bool
    disable_user: list
    disable_group: list


class CommandInfo(BaseModel):
    type: str
    docs: str
    aliases: list


class Service:
    """
    集成一套服务管理, 对功能信息持久化
    服务文件结构:
    {
        "service": "Service name",
        "docs": "Main helps and commands",
        "version": ""
        "type": "",
        "permission": ["Master", ...],
        "cmd_list": {
            "/cmd0": {
                "type": "Command type",
                "docs": "Command help",
                "aliases": ["More trigger ways."],
            }
        },
        "enabled": True,
        "only_admin": False,
        "disable_user": [],
        "disable_group": [],
    }
    """

    class ServiceType(Enum):
        SYSTEM = "系统服务"
        LKPLUGIN = "LK服务by l_o_o_k"
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
            type: ServiceType = ServiceType.OTHER
    ):
        """初始化一个服务"""

        super().__init__()
        if not service:
            return

        if service in service_list or service == "master":
            raise ServiceRegisterError("服务重复注册或服务名违规")
        self.service = service
        self._docs = docs
        self._version = version
        self._type = type

        self._permission = None
        self._priority = 10
        self._main_cmd = (str(),)
        self._temp = False
        self._rule = is_in_service(service)
        self._handlers = None
        self._state = None
        self._path = Path(".") / "data" / "plugins" / self.service
        self.__generate_service_conf()
        if version:
            path = SERVICES_DIR / f"{self.service}.json"
            if path.is_file():
                data = json.loads(path.read_bytes())
                if self._version != data.get("version", "none"):
                    os.remove(path)
                    log.info(f"{self.service}信息已更新")
        service_list.append(service)

    def document(self, context: str) -> "Service":
        """为服务添加说明"""
        self._docs = context
        return self

    def type(self, _type: ServiceType) -> "Service":
        """为服务添加类型"""
        self._type = _type
        return self

    def version(self, version: str) -> "Service":
        """设置服务版本号"""
        self._version = version
        path = SERVICES_DIR / f"{self.service}.json"
        if path.is_file():
            data = json.loads(path.read_bytes())
            if self._version != data.get("version", "none"):
                os.remove(path)
                log.info(f"{self.service}信息已更新")
        return self

    def rule(self, rule: Optional[Union[Rule, T_RuleChecker]]) -> "Service":
        """为服务添加触发判定"""

        self._rule = self._rule & rule
        return self

    def permission(self, perm: Permission) -> "Service":
        """为服务添加权限判定"""

        self._permission = perm

        data = self.load_service(self.service)
        if perm.name in data["permission"]:
            pass
        else:
            data["permission"].append(perm.name)  # type: ignore
        self.save_service(data, self.service)
        return self

    def handlers(self, hand: Optional[List[T_Handler]]) -> "Service":
        """为服务设置处理函数"""

        self._handlers = hand
        return self

    def temp(self, _is: bool) -> "Service":
        """设置是否为一次性服务"""

        self._temp = _is
        return self

    def priority(self, level: int) -> "Service":
        """为服务设置优先级等级"""

        self._priority = level
        return self

    def state(self, state: Optional[T_State]) -> "Service":
        """为服务设置处理类型"""

        self._state = state
        return self

    def main_cmd(self, cmd: str) -> "Service":
        """为服务命令设置前缀"""

        self._main_cmd = (cmd,)
        return self

    def is_nonebot_plugin(self) -> "Service":
        cmd_list = self.__load_cmds()
        name = "请参考对应插件文档"
        cmd_list[name] = CommandInfo(type="ignore", docs=str(), aliases=list()).model_dump()
        self.__save_cmds(cmd_list)
        return self

    def get_path(self) -> Path:
        return self._path

    def __generate_service_conf(self):
        path = CONFIG_DIR / f"{self.service}.json"
        if path.is_file():
            return
        data = ServiceConfig(
            enabled=True,
            disable_user=list(),
            disable_group=list(),
        )
        try:
            data.write_into_file(path)
        except Exception:
            raise WriteFileError("Write service config failed")

    def __generate_service_config(self, service: str, docs: str = str(), version: str = str(),
                                  _type: ServiceType = ServiceType.OTHER) -> None:
        path = SERVICES_DIR / f"{service}.json"
        data = ServiceInfo(
            service=service,
            docs=docs,
            version=version,
            type=_type.value,
            permission=list(),
            cmd_list=dict(),
        )
        try:
            data.write_into_file(path)
        except Exception:
            raise WriteFileError("Write service info failed!")

    def save_service(self, service_data: dict, service: str) -> None:
        if not service:
            service = self.service

        path = SERVICES_DIR / f"{service}.json"
        if not path.is_file():
            self.__generate_service_config(service, self._docs, self._version, self._type)

        with open(path, "w", encoding="utf-8") as w:
            w.write(json.dumps(service_data, indent=4, ensure_ascii=False))

    def load_service(self, service: str) -> dict:
        path = SERVICES_DIR / f"{service}.json"
        if not path.is_file():
            self.__generate_service_config(service, self._docs, self._version, self._type)

        try:
            data = json.loads(path.read_bytes())
        except Exception:
            with open(path, "w", encoding="utf-8") as w:
                w.write(json.dumps({}))
            self.__generate_service_config(service, self._docs, self._version, self._type)
            data = json.loads(path.read_bytes())
        return data

    def __save_cmds(self, cmds: dict) -> None:
        data = self.load_service(self.service)
        temp_data: dict = data["cmd_list"]
        temp_data.update(cmds)
        self.save_service(data, self.service)

    def __load_cmds(self) -> dict:
        path = SERVICES_DIR / f"{self.service}.json"
        if not path.is_file():
            self.__generate_service_config(self.service, self._docs, self._version, self._type)
        data = json.loads(path.read_bytes())
        return data["cmd_list"]

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
            cmd_list = self.__load_cmds()

            name = name + "-onmsg"

            cmd_list[name] = CommandInfo(
                type="message", docs=docs, aliases=list()
            ).model_dump()
            self.__save_cmds(cmd_list)

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
        cmd_list = self.__load_cmds()

        name = name + "-onntc"

        cmd_list[name] = CommandInfo(type="notice", docs=docs, aliases=list()).model_dump()
        self.__save_cmds(cmd_list)

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
        cmd_list = self.__load_cmds()

        name = name + "-onreq"

        cmd_list[name] = CommandInfo(type="request", docs=docs, aliases=list()).model_dump()
        self.__save_cmds(cmd_list)

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
        cmd_list = self.__load_cmds()
        if not rule:
            rule = self._rule
        if not aliases:
            aliases = set()

        if isinstance(cmd, tuple):
            cmd = ".".join(map(str, cmd))

        cmd_list[cmd] = CommandInfo(
            type="command", docs=docs, aliases=list(aliases)
        ).model_dump()
        self.__save_cmds(cmd_list)
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

        cmd_list = self.__load_cmds()

        cmd_list[name] = CommandInfo(type="keyword", docs=docs, aliases=list(keywords)).model_dump()
        self.__save_cmds(cmd_list)

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

        cmd_list = self.__load_cmds()
        cmd_list[pattern] = CommandInfo(type="regex", docs=docs, aliases=list()).model_dump()
        self.__save_cmds(cmd_list)

        return self.on_message(rule=regex(pattern, flags) & rule, **kwargs)

    def cmd_as_group(self, cmd: str, docs: str, **kwargs) -> Type[Matcher]:
        sub_cmd = (cmd,) if isinstance(cmd, str) else cmd
        _cmd = self._main_cmd + sub_cmd

        if "aliases" in kwargs:
            del kwargs["aliases"]

        return self.on_command(_cmd, docs, **kwargs)

    @staticmethod
    async def send_to_master(message: Union[str, Message]):
        bot = get_bot()
        for m in MASTER_LIST:
            await bot.send_private_msg(user_id=m, message=message)


class ServiceTools:
    """针对服务的工具类"""

    def __init__(self, service: str):
        if not service in service_list:
            raise ServiceNotFoundError("找不到指定服务")
        self.service = service

    def save_service(self, service_data: ServiceInfo):
        path = SERVICES_DIR / f"{self.service}.json"
        if not path.is_file():
            raise ReadFileError(
                f"无法找到服务 {self.service} 对应的信息文件\n"
                "请删除此目录下的文件: data/service/services\n"
                "接着重新启动"
            )

        service_data.write_into_file(path)

    def load_service(self) -> ServiceInfo:
        path = SERVICES_DIR / f"{self.service}.json"
        if not path.is_file():
            raise ReadFileError(
                f"无法找到服务 {self.service} 对应的信息文件\n"
                "请删除此目录下的文件: data/service/services\n"
                "接着重新启动"
            )

        return ServiceInfo.read_from_file(path)

    def save_service_config(self, service_config: ServiceConfig):
        path = CONFIG_DIR / f"{self.service}.json"
        if not path.is_file():
            raise ReadFileError(
                f"无法找到服务 {self.service} 对应的信息文件\n"
                "请删除此目录下的文件: data/service/services\n"
                "接着重新启动"
            )

        service_config.write_into_file(path)

    def load_service_config(self) -> ServiceConfig:
        path = CONFIG_DIR / f"{self.service}.json"
        if not path.is_file():
            raise ReadFileError(
                f"无法找到服务 {self.service} 对应的信息文件\n"
                "请删除此目录下的文件: data/service/services\n"
                "接着重新启动"
            )

        return ServiceConfig.read_from_file(path)

    def del_service(self):
        path = SERVICES_DIR / f"{self.service}.json"
        path.unlink()
        c_path = CONFIG_DIR / f"{self.service}.json"
        c_path.unlink()

    def auth_service(self, user_id: str = str(), group_id: str = str()) -> bool:
        data = self.load_service_config()

        auth_global = data.enabled
        auth_user = data.disable_user
        auth_group = data.disable_group

        if user_id:
            if user_id in auth_user:
                return False

        if group_id:
            return False if group_id in auth_group else True

        return auth_global

    def service_controller(self, is_enabled: bool):
        data = self.load_service_config()
        data.enabled = is_enabled
        self.save_service_config(data)


def is_in_service(service: str) -> Rule:
    async def _is_in_service(bot: Bot, event: Event) -> bool:
        result = ServiceTools(service).auth_service()
        if not result:
            return False

        if isinstance(event, PrivateMessageEvent):
            user_id = event.get_user_id()
            result = ServiceTools(service).auth_service(user_id)
            return result
        elif isinstance(event, GroupMessageEvent):
            user_id = event.get_user_id()
            group_id = str(event.group_id)
            result = ServiceTools(service).auth_service(user_id, group_id)
            return result
        else:
            return True

    return Rule(_is_in_service)
