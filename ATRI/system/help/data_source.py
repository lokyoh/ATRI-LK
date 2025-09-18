import inspect
import json
import os.path
from pathlib import Path
from typing import Dict
from PIL import Image
from jinja2 import Environment, FileSystemLoader

from nonebot.adapters.onebot.v11 import MessageSegment, GroupMessageEvent

from ATRI import __version__, conf, IMG_DIR, service_list, __sub_version__, RES_DIR
from ATRI.message import MessageBuilder, img_msg, img_msg_from_path
from ATRI.service import ServiceTools, Service
from ATRI.utils.img_editor import IMGEditor
from ATRI.exceptions import ServiceNotFoundError
from ATRI.log import log
from ATRI.permission import MASTER_LIST
from ATRI.system.htmlrender import html_to_pic

from . import help_config

_SERVICE_INFO_FORMAT = (
    MessageBuilder("服务名：{service}")
    .text("说明：{docs}")
    .text("可用命令：\n{cmd_list}")
    .text("是否全局启用：{enabled}")
    .text("Tip: /帮助 (服务) (命令) 以查看对应命令详细信息")
    .done()
)
_COMMAND_INFO_FORMAT = (
    MessageBuilder("命令：{cmd}")
    .text("类型：{cmd_type}")
    .text("说明：{docs}")
    .text("更多触发方式：{aliases}")
    .done()
)
PLUGIN_PATH = Path('.') / 'data' / 'plugins' / 'help'
PLUGIN_PATH.mkdir(parents=True, exist_ok=True)
SERVICES_PATH = PLUGIN_PATH / 'services.json'
SERVICES_IMG_PATH = PLUGIN_PATH / 'help.jpg'
help_type = {}


class Helper:
    service_dict: Dict[str, list] = dict()

    @staticmethod
    def menu() -> str:
        return (
            MessageBuilder("哦呀？~需要帮助？")
            .text("/关于 -查看bot基本信息")
            .text("/服务列表 -查看所有可用服务")
            .text("/帮助 （服务） -查看对应服务帮助")
            .done()
        )

    @staticmethod
    def about() -> str:
        temp_list = list()
        for i in conf.BotConfig.nickname:
            temp_list.append(i)
        nickname = "、".join(map(str, temp_list))
        return (
            MessageBuilder("吾乃 ATRI！")
            .text(f"可以称呼：{nickname}")
            .text(f"型号是：{__version__} {__sub_version__}")
            .text("想进一步了解:")
            .text("项目地址:https://github.com/lokyoh/ATRI-LK")
            .text("原项目地址:https://github.com/Kyomotoi/ATRI")
            .done()
        )

    @classmethod
    def save_service_dict(cls):
        with open(SERVICES_PATH, 'w', encoding='utf-8') as f:
            json.dump(cls.service_dict, f, ensure_ascii=False, indent=4)

    @classmethod
    def get_typed_services(cls) -> bool:
        for _type in Service.ServiceType:
            if _type.name not in cls.service_dict:
                cls.service_dict[_type.name] = list()
        refresh = False
        for _type in Service.ServiceType:
            for s in cls.service_dict[_type.name]:
                if s not in service_list or (
                        _type != Service.ServiceType.CLOSED and service_list[s].get_info().type != _type.value):
                    cls.service_dict[_type.name].remove(s)
                    refresh = True
        for s in service_list:
            service = ServiceTools(s)
            if not service.load_service_config().enabled:
                if s in cls.service_dict[Service.ServiceType.CLOSED.name]:
                    continue
                _type = Service.ServiceType(service.load_service().type).name
                if s in cls.service_dict[_type]:
                    cls.service_dict[_type].remove(s)
                cls.service_dict[Service.ServiceType.CLOSED.name].append(s)
                refresh = True
                continue
            _type = Service.ServiceType(service.load_service().type)
            if s in cls.service_dict[_type.name]:
                continue
            cls.service_dict[_type.name].append(s)
            refresh = True
        if refresh:
            cls.save_service_dict()
        return refresh

    @classmethod
    def init_services(cls) -> None:
        if os.path.exists(SERVICES_PATH):
            with open(SERVICES_PATH, 'r', encoding='utf-8') as f:
                cls.service_dict = json.load(f)
        if help_config.help_type == 'image':
            cls.get_image_list()

    @classmethod
    def get_image_list(cls, event=None) -> MessageSegment:
        refresh = cls.get_typed_services()
        if not SERVICES_IMG_PATH.exists() or refresh:
            cls.get_services_img()
        return img_msg_from_path(SERVICES_IMG_PATH)

    @classmethod
    def get_services_img(cls):
        n = int((len(service_list) + len(cls.service_dict)) / 15) + 1
        top = 50
        border = 5
        width = 320
        height = 25
        all_count = 0
        count = 0
        line = 0
        max_count = 0
        max_line = 0
        info = IMGEditor(Image.new("RGBA", ((border + width) * n + border * 2,
                                            (31 * height) + top + 31 * border), (255, 255, 255, 0)))
        i = 0
        for _type in Service.ServiceType:
            if _type == Service.ServiceType.HIDDEN:
                continue
            if len(cls.service_dict[_type.name]) == 0:
                continue
            if line + len(cls.service_dict[_type.name]) + 1 > 30:
                max_count = max(max_count, count)
                max_line = max(max_line, line)
                all_count += 1
                count = 0
                line = 0
            info.add_rectangle((border + width) * all_count + border, top + (count * border) + (line * height),
                               width, (len(cls.service_dict[_type.name]) + 1) * height, 192, 5)
            info.add_text((border + width) * all_count + border * 2, top + (count * border) + (line * height),
                          f'{_type.value}:', 20)
            line += 1
            for j in cls.service_dict[_type.name]:
                info.add_text((border + width) * all_count + border * 3, top + (count * border) + (line * height),
                              f'· {j}', 20)
                line += 1
            count += 1
            i += 1
        max_count = max(max_count, count)
        max_line = max(max_line, line)
        info.add_rectangle(5, 5, (border + width) * (all_count + 1) - border, 40, 192, 5)
        info.add_text(5, 5, "咱搭载了以下服务~", 30)
        info.add_rectangle(5, top + max_count * border + (max_line * height),
                           (border + width) * (all_count + 1) - border, height, 192, 5)
        info.add_text(10, top + max_count * border + (max_line * height), "/帮助 (服务) -以查看对应服务帮助",
                      20, color='red')
        background_path = IMG_DIR / 'help' / 'background.jpg'
        background = (IMGEditor(Image.open(background_path).convert("RGB"))
                      .resize((border + width) * (all_count + 1) + border,
                              top + (max_count + 1) * border + (max_line + 1) * height)
                      )
        background.img.paste(info.get_image(), (0, 0), info.get_image())
        background.save_rgb(SERVICES_IMG_PATH)

    @classmethod
    def get_text_list(cls, event=None):
        log.info("发送服务列表图片失败，使用用文字方式发送")
        services_info = ""
        for _type in Service.ServiceType:
            if _type == Service.ServiceType.HIDDEN:
                continue
            if len(cls.service_dict[_type.name]) == 0:
                continue
            services_info += f'->{_type.value}<-:\n'
            for j in cls.service_dict[_type.name]:
                services_info += f'· {j}\n'
        return f'咱搭载了以下服务~\n{services_info}/帮助 (服务) -以查看对应服务帮助'

    @classmethod
    async def get_html_help(cls, event):
        level = 0
        user_id = str(event.user_id)
        if user_id in MASTER_LIST:
            level = 2
        elif isinstance(event, GroupMessageEvent) and event.sender.role in ["admin", "owner"]:
            level = 1
        group_id = str(event.group_id) if isinstance(event, GroupMessageEvent) else str()
        services = {}
        for _type in Service.ServiceType:
            services[_type.value] = []
        s_l = list(service_list.keys())
        s_l.sort()
        length = len(s_l)
        for s in s_l:
            _s: Service = service_list[s]
            info = _s.get_info()
            _type = info.type
            if _type == Service.ServiceType.HIDDEN.value:
                continue
            if info.permission == "Master":
                if level < 2:
                    continue
            elif info.permission == "Admin":
                if level < 1:
                    continue
            usable = False
            sc = ServiceTools(s).load_service_config()
            if sc.enabled:
                usable = True
                if user_id in sc.disable_user:
                    usable = False
                elif isinstance(event, GroupMessageEvent) and group_id in sc.disable_group:
                    usable = False
            si = _s.get_info().model_dump()
            si["usable"] = usable
            services[_type].append(si)
        services = {k: v for k, v in services.items() if not (isinstance(v, list) and len(v) == 0)}
        simp_s = {}
        for key in services:
            k_l = services[key]
            simp_s[key] = list(
                map(lambda se: {'service': se['service'], 'version': se['version'], 'usable': se['usable']}, k_l))
        if group_id:
            (PLUGIN_PATH / group_id).mkdir(parents=True, exist_ok=True)
            img_path = PLUGIN_PATH / group_id / f"{user_id}.png"
            json_path = PLUGIN_PATH / group_id / f"{user_id}.json"
        else:
            (PLUGIN_PATH / 'user').mkdir(parents=True, exist_ok=True)
            img_path = PLUGIN_PATH / "user" / f"{user_id}.png"
            json_path = PLUGIN_PATH / "user" / f"{user_id}.json"
        if json_path.exists() and img_path.exists():
            data = json.load(open(json_path))
            if data.get('length', 0) == length:
                if data.get('services', []) == simp_s:
                    return img_msg_from_path(img_path)
        log.info(f'开始为{f'{group_id}中的{user_id}' if group_id else f'{user_id}'}生成新的帮助')
        json.dump({'length': length, 'services': simp_s}, open(json_path, 'w'), indent=4, ensure_ascii=False)
        env = Environment(loader=FileSystemLoader(RES_DIR / 'html' / 'help'))
        template = env.get_template("help.html")
        html_output = template.render(categories=services)
        data = await html_to_pic(html_output, viewport={"width": 800, "height": 600}, device_scale_factor=1)
        with open(img_path, "wb") as f:
            f.write(data)
        return img_msg(data)

    @staticmethod
    def service_info(service: str) -> str:
        try:
            data = ServiceTools(service).load_service()
            s_conf = ServiceTools(service).load_service_config()
        except ServiceNotFoundError:
            return "请检查是否输入错误呢.../帮助 (服务)"

        service_name = data.service
        service_docs = data.docs
        service_enabled = s_conf.enabled

        _service_cmd_list = list(data.cmd_list)
        service_cmd_list = "\n".join(map(str, _service_cmd_list))

        repo = _SERVICE_INFO_FORMAT.format(
            service=service_name,
            docs=service_docs,
            cmd_list=service_cmd_list,
            enabled=service_enabled,
        )
        return repo

    @staticmethod
    def cmd_info(service: str, cmd: str) -> str:
        try:
            data = ServiceTools(service).load_service()
        except ServiceNotFoundError:
            return "请检查是否输入错误.../帮助 (服务) (命令)"

        cmd_list: dict = data.cmd_list
        cmd_info = cmd_list.get(cmd, dict())
        if not cmd_info:
            return "请检查命令是否输入错误..."
        cmd_type = cmd_info.get("type", "ignore")
        docs = cmd_info.get("docs", "ignore")
        aliases = cmd_info.get("aliases", "ignore")

        repo = _COMMAND_INFO_FORMAT.format(
            cmd=cmd, cmd_type=cmd_type, docs=docs, aliases=aliases
        )
        return repo

    @classmethod
    async def get_service_list(cls, event):
        func = help_type[help_config.help_type]
        if inspect.iscoroutinefunction(func):
            return await func(event)
        return func(event)


help_type['text'] = Helper.get_text_list
help_type['image'] = Helper.get_image_list
help_type['html'] = Helper.get_html_help
