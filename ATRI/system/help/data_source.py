import os
import json
from typing import Dict
from PIL import Image

from nonebot.adapters.onebot.v11 import MessageSegment

from ATRI import __version__, conf, IMG_DIR, service_list
from ATRI.message import MessageBuilder
from ATRI.service import SERVICES_DIR, ServiceTools, Service
from ATRI.utils.img_editor import IMGEditor
from ATRI.exceptions import ServiceNotFoundError

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


class Helper:
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
            MessageBuilder("吾乃 ATRI-LK版！")
            .text(f"可以称呼：{nickname}")
            .text(f"型号是：{__version__}")
            .text("想进一步了解:")
            .text("项目地址:https://github.com/lokyoh/ATRI-LK")
            .text("原项目地址:https://github.com/Kyomotoi/ATRI")
            .done()
        )

    @staticmethod
    def get_service_list() -> MessageSegment:
        services: Dict[Service.ServiceType, list] = dict()
        for _type in Service.ServiceType:
            services[_type] = list()
        for prefix in service_list:
            f = os.path.join(SERVICES_DIR, f"{prefix}.json")
            with open(f, "r", encoding="utf-8") as r:
                service = json.load(r)
                if not ServiceTools(prefix).load_service_config().enabled:
                    services[Service.ServiceType.CLOSED].append(prefix)
                    continue
                if service["only_admin"]:
                    services[Service.ServiceType.ADMIN].append(prefix)
                    continue
                _type = Service.ServiceType(service["type"])
                services[_type].append(prefix)
        n = int((len(service_list) + len(services)) / 15) + 1
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
            if len(services[_type]) == 0:
                continue
            if line + len(services[_type]) + 1 > 30:
                max_count = max(max_count, count)
                max_line = max(max_line, line)
                all_count += 1
                count = 0
                line = 0
            info.add_rectangle((border + width) * all_count + border, top + (count * border) + (line * height),
                               width, (len(services[_type]) + 1) * height, 192, 5)
            info.add_text((border + width) * all_count + border * 2, top + (count * border) + (line * height),
                          f'{_type.value}:', 20)
            line += 1
            for j in range(len(services[_type])):
                info.add_text((border + width) * all_count + border * 3, top + (count * border) + (line * height),
                              f'· {services[_type][j]}', 20)
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
        return MessageSegment.image(background.to_bytes())

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
            return "请检查是否输入错误..."

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
