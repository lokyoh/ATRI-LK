import hashlib
import inspect
import json
import os
import time
from pathlib import Path
from typing import ClassVar

import anyio
from jinja2 import Environment, FileSystemLoader
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from PIL import Image

from ATRI import IMG_DIR, RES_DIR, __sub_version__, __version__, conf
from ATRI.bot import BotUtils
from ATRI.exceptions import ServiceNotFoundError
from ATRI.log import log
from ATRI.message import MessageBuilder, img_msg, img_msg_from_path
from ATRI.permission import MASTER_LIST
from ATRI.service import Service, ServiceTools
from ATRI.system.htmlrender import html_to_pic
from ATRI.utils.img_editor import IMGEditor

from . import help_config

PLUGIN_PATH = Path(".") / "data" / "plugins" / "help"
PLUGIN_PATH.mkdir(parents=True, exist_ok=True)
SERVICES_PATH = PLUGIN_PATH / "services.json"
SERVICES_IMG_PATH = PLUGIN_PATH / "help.jpg"
CMD_PREFIX = BotUtils.get_command_start()
_SERVICE_INFO_FORMAT = (
    MessageBuilder("服务名：{service}")
    .text("说明：{docs}")
    .text("可用命令：\n{cmd_list}")
    .text("是否全局启用：{enabled}")
    .text("Tip: {cmd_prefix}帮助 (服务) (命令) 以查看对应命令详细信息")
    .done()
)
_COMMAND_INFO_FORMAT = (
    MessageBuilder("命令：{cmd}")
    .text("类型：{cmd_type}")
    .text("说明：{docs}")
    .text("更多触发方式：{aliases}")
    .done()
)

_CACHE_TTL_SECONDS = 7 * 24 * 3600  # 缓存有效期：7 天
_CLEANUP_INTERVAL = 100  # 每写 100 次缓存触发一次清理
_CACHE_ROOT_FILES = {"services.json", "help.jpg"}  # 顶层受保护文件
_TYPED_REFRESH_TTL = 1.0  # 秒，连续请求在此窗口内只扫描一次

_MAX_LINES_PER_COLUMN = 30  # 每列最大行数（含分类标题）
_TITLE_AREA_HEIGHT = 50  # 顶部标题区高度
_BLOCK_GAP = 5  # 块间距 / 圆角
_COLUMN_WIDTH = 320  # 每列宽度
_ROW_HEIGHT = 25  # 每行高度

_FONT_SIZE_HEADER = 30  # 标题字号
_FONT_SIZE_ITEM = 20  # 分类名 / 服务名 / 页脚字号

_BLOCK_ALPHA = 192  # 半透明块透明度
_BLOCK_RADIUS = 5  # 块圆角
_FOOTER_COLOR = "red"  # 页脚文字颜色

help_type = {}
_template_hash_cache: dict[str, tuple[float, int, str]] = {}


def _get_template_hash() -> str:
    """计算 help.html 的内容 hash，按 mtime + size 缓存，模板变更时失效。"""
    template_path = RES_DIR / "html" / "help" / "help.html"
    key = str(template_path)
    try:
        stat = template_path.stat()
    except OSError as e:
        log.warning(f"读取帮助模板失败，缓存将始终视为失效: {e}")
        return "unknown"
    cached = _template_hash_cache.get(key)
    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]
    try:
        content = template_path.read_bytes()
    except OSError as e:
        log.warning(f"读取帮助模板失败，缓存将始终视为失效: {e}")
        return "unknown"
    digest = hashlib.sha256(content).hexdigest()
    _template_hash_cache[key] = (stat.st_mtime, stat.st_size, digest)
    return digest


_JINJA_ENV = Environment(
    loader=FileSystemLoader(RES_DIR / "html" / "help"),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


class Helper:
    service_dict: ClassVar[dict[str, list]] = {}
    _service_snapshot: ClassVar[
        dict[str, tuple[bool, "Service.ServiceType | None", object]]
    ] = {}
    _img_lock: anyio.Lock = anyio.Lock()
    _cleanup_counter: int = 0
    _last_typed_refresh: float = -float("inf")

    @staticmethod
    def menu() -> str:
        return (
            MessageBuilder("哦呀？~需要帮助？")
            .text(f"{CMD_PREFIX}关于 -查看bot基本信息")
            .text(f"{CMD_PREFIX}服务列表 -查看所有可用服务")
            .text(f"{CMD_PREFIX}帮助 （服务） -查看对应服务帮助")
            .done()
        )

    @staticmethod
    def about() -> str:
        raw_nickname = conf.BotConfig.nickname
        if raw_nickname is None:
            nicknames = "ATRI"
        elif isinstance(raw_nickname, str):
            nicknames = raw_nickname
        else:
            try:
                nicknames = "、".join(str(n) for n in raw_nickname)
            except TypeError:
                nicknames = str(raw_nickname)
        return (
            MessageBuilder("吾乃 ATRI！")
            .text(f"可以称呼：{nicknames}")
            .text(f"型号是：{__version__} {__sub_version__}")
            .text("想进一步了解:")
            .text("项目地址:https://github.com/lokyoh/ATRI-LK")
            .text("原项目地址:https://github.com/Kyomotoi/ATRI")
            .done()
        )

    @classmethod
    def save_service_dict(cls):
        tmp_path = SERVICES_PATH.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(cls.service_dict, f, ensure_ascii=False, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, SERVICES_PATH)
            return True
        except (OSError, TypeError, ValueError) as e:
            log.error(f"保存 services.json 失败: {e}")
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            return False

    @classmethod
    def get_typed_services(cls, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and (now - cls._last_typed_refresh) < _TYPED_REFRESH_TTL:
            return False
        cls._last_typed_refresh = now
        service_list = ServiceTools.service_list
        # 确保所有类型键存在
        for _type in Service.ServiceType:
            if _type.name not in cls.service_dict:
                cls.service_dict[_type.name] = []
        # 一次性扫描：为每个服务建立 (enabled, service_type) 快照
        snapshot: dict[str, tuple[bool, Service.ServiceType | None, object]] = {}
        for s in service_list:
            service = ServiceTools(s)
            try:
                sc = service.load_service_config()
                enabled = sc.enabled
                type_str = service.load_service().type
                service_type = Service.ServiceType(type_str)
            except Exception:
                enabled = False
                service_type = None
                sc = None
            snapshot[s] = (enabled, service_type, sc)
        cls._service_snapshot = snapshot
        refresh = False
        # 1. 清理不存在的服务 / 类型不匹配的服务
        for _type in Service.ServiceType:
            valid_services = []
            for s in cls.service_dict[_type.name]:
                if s not in snapshot:
                    refresh = True
                    continue
                _, service_type, _ = snapshot[s]
                if _type != Service.ServiceType.CLOSED and (
                    service_type is None or service_type != _type
                ):
                    refresh = True
                    continue
                valid_services.append(s)
            if valid_services != cls.service_dict[_type.name]:
                cls.service_dict[_type.name] = valid_services
                refresh = True
        # 2. 根据快照重新分类
        closed_name = Service.ServiceType.CLOSED.name
        for s in service_list:
            enabled, service_type, _ = snapshot[s]
            if service_type is None:
                if s not in cls.service_dict[closed_name]:
                    cls.service_dict[closed_name].append(s)
                    refresh = True
                continue
            if not enabled:
                for _type in Service.ServiceType:
                    if _type == Service.ServiceType.CLOSED:
                        continue
                    if s in cls.service_dict[_type.name]:
                        cls.service_dict[_type.name].remove(s)
                        refresh = True
                if s not in cls.service_dict[closed_name]:
                    cls.service_dict[closed_name].append(s)
                    refresh = True
            else:
                if s in cls.service_dict[closed_name]:
                    cls.service_dict[closed_name].remove(s)
                    refresh = True
                for _type in Service.ServiceType:
                    if _type == service_type:
                        continue
                    if s in cls.service_dict[_type.name]:
                        cls.service_dict[_type.name].remove(s)
                        refresh = True
                if s not in cls.service_dict[service_type.name]:
                    cls.service_dict[service_type.name].append(s)
                    refresh = True
        if refresh:
            cls.save_service_dict()
        return refresh

    @classmethod
    def init_services(cls) -> None:
        if SERVICES_PATH.exists():
            try:
                with open(SERVICES_PATH, "r", encoding="utf-8") as f:
                    content = f.read()
                data = json.loads(content)
                if not isinstance(data, dict):
                    raise TypeError("services.json 顶层不是 dict")
                for _type in Service.ServiceType:
                    if _type.name not in data or not isinstance(data[_type.name], list):
                        data[_type.name] = []
                cls.service_dict = data
            except (
                json.JSONDecodeError,
                OSError,
                ValueError,
                UnicodeDecodeError,
                TypeError,
            ) as e:
                log.warning(f"读取 services.json 失败，将重建: {e}")
                cls.service_dict = {}
        else:
            cls.service_dict = {}
        try:
            cls._cleanup_cache()
        except Exception as e:
            log.warning(f"启动清理帮助缓存失败: {e}")

    @classmethod
    async def get_image_list(cls, event=None) -> MessageSegment:
        async with cls._img_lock:
            refresh = cls.get_typed_services()
            if not SERVICES_IMG_PATH.exists() or refresh:
                try:
                    await anyio.to_thread.run_sync(cls.get_services_img)
                except Exception as e:
                    log.error(f"生成服务列表图片失败，回退到文字版: {e}")
                    return cls.get_text_list(event)
            return img_msg_from_path(SERVICES_IMG_PATH)

    @classmethod
    def get_services_img(cls):
        """生成服务列表图片并保存到 SERVICES_IMG_PATH。"""
        visible_types = [
            _type
            for _type in Service.ServiceType
            if _type != Service.ServiceType.HIDDEN and cls.service_dict[_type.name]
        ]

        columns = cls._layout_columns(visible_types)
        total_columns = len(columns)
        max_count, max_line = cls._measure_columns(columns)

        canvas_width = (_BLOCK_GAP + _COLUMN_WIDTH) * total_columns + _BLOCK_GAP * 2
        canvas_height = (
            _TITLE_AREA_HEIGHT
            + (_MAX_LINES_PER_COLUMN + 1) * _ROW_HEIGHT
            + (_MAX_LINES_PER_COLUMN + 1) * _BLOCK_GAP
        )
        info = IMGEditor(
            Image.new("RGBA", (canvas_width, canvas_height), (255, 255, 255, 0))
        )

        for col_index, column in enumerate(columns):
            cls._draw_column(info, col_index, column)

        cls._draw_header(info, total_columns)
        cls._draw_footer(info, total_columns, max_count, max_line)
        cls._compose_background(info, total_columns, max_count, max_line)

    @classmethod
    def _layout_columns(cls, visible_types: list) -> list[list]:
        """按 _MAX_LINES_PER_COLUMN 把分类切成若干列，返回每列的分类列表。"""
        columns: list[list] = []
        current: list = []
        line = 0
        for _type in visible_types:
            needed = 1 + len(cls.service_dict[_type.name])
            if current and line + needed > _MAX_LINES_PER_COLUMN:
                columns.append(current)
                current = []
                line = 0
            current.append(_type)
            line += needed
        if current:
            columns.append(current)
        return columns

    @classmethod
    def _measure_columns(cls, columns: list[list]) -> tuple[int, int]:
        """返回 (max_count, max_line)，用于页脚定位。"""
        max_count = 0
        max_line = 0
        for column in columns:
            line = sum(1 + len(cls.service_dict[_type.name]) for _type in column)
            max_count = max(max_count, len(column))
            max_line = max(max_line, line)
        return max_count, max_line

    @classmethod
    def _draw_column(cls, info: IMGEditor, col_index: int, column: list) -> None:
        """在指定列索引处绘制一列分类块。"""
        x_base = (_BLOCK_GAP + _COLUMN_WIDTH) * col_index
        y = _TITLE_AREA_HEIGHT
        for _type in column:
            services = cls.service_dict[_type.name]
            info.add_rectangle(
                x_base + _BLOCK_GAP,
                y,
                _COLUMN_WIDTH,
                (len(services) + 1) * _ROW_HEIGHT,
                _BLOCK_ALPHA,
                _BLOCK_RADIUS,
            )
            info.add_text(
                x_base + _BLOCK_GAP * 2,
                y,
                f"{_type.value}:",
                _FONT_SIZE_ITEM,
            )
            y += _ROW_HEIGHT
            for service_name in services:
                info.add_text(
                    x_base + _BLOCK_GAP * 3,
                    y,
                    f"· {service_name}",
                    _FONT_SIZE_ITEM,
                )
                y += _ROW_HEIGHT
            y += _BLOCK_GAP

    @classmethod
    def _draw_header(cls, info: IMGEditor, total_columns: int) -> None:
        width = (_BLOCK_GAP + _COLUMN_WIDTH) * total_columns - _BLOCK_GAP
        info.add_rectangle(
            _BLOCK_GAP,
            _BLOCK_GAP,
            width,
            _TITLE_AREA_HEIGHT - _BLOCK_GAP * 2,
            _BLOCK_ALPHA,
            _BLOCK_RADIUS,
        )
        info.add_text(_BLOCK_GAP, _BLOCK_GAP, "咱搭载了以下服务~", _FONT_SIZE_HEADER)

    @classmethod
    def _draw_footer(
        cls, info: IMGEditor, total_columns: int, max_count: int, max_line: int
    ) -> None:
        width = (_BLOCK_GAP + _COLUMN_WIDTH) * total_columns - _BLOCK_GAP
        y = _TITLE_AREA_HEIGHT + max_count * _BLOCK_GAP + max_line * _ROW_HEIGHT
        info.add_rectangle(
            _BLOCK_GAP, y, width, _ROW_HEIGHT, _BLOCK_ALPHA, _BLOCK_RADIUS
        )
        info.add_text(
            _BLOCK_GAP * 2,
            y,
            f"{CMD_PREFIX}帮助 (服务) -以查看对应服务帮助",
            _FONT_SIZE_ITEM,
            color=_FOOTER_COLOR,
        )

    @classmethod
    def _compose_background(
        cls, info: IMGEditor, total_columns: int, max_count: int, max_line: int
    ) -> None:
        width = (_BLOCK_GAP + _COLUMN_WIDTH) * total_columns + _BLOCK_GAP
        height = (
            _TITLE_AREA_HEIGHT
            + (max_count + 1) * _BLOCK_GAP
            + (max_line + 1) * _ROW_HEIGHT
        )
        background_path = IMG_DIR / "help" / "background.jpg"
        if background_path.exists():
            with Image.open(background_path) as bg:
                bg_rgb = bg.convert("RGB")
        else:
            log.warning(f"帮助背景图不存在: {background_path}，使用纯白背景")
            bg_rgb = Image.new("RGB", (width, height), (255, 255, 255))
        background = IMGEditor(bg_rgb).resize(width, height)
        background.img.paste(info.get_image(), (0, 0), info.get_image())
        tmp_path = SERVICES_IMG_PATH.with_suffix(".tmp")
        try:
            background.save_rgb(tmp_path)
            os.replace(tmp_path, SERVICES_IMG_PATH)
        except OSError as e:
            log.error(f"保存服务列表图片失败: {e}")
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            raise

    @classmethod
    def get_text_list(cls, event=None):
        cls.get_typed_services()
        services_info = ""
        for _type in Service.ServiceType:
            if _type == Service.ServiceType.HIDDEN:
                continue
            if len(cls.service_dict[_type.name]) == 0:
                continue
            services_info += f"->{_type.value}<-:\n"
            for j in cls.service_dict[_type.name]:
                services_info += f"· {j}\n"
        return f"咱搭载了以下服务~\n{services_info}{CMD_PREFIX}帮助 (服务) -以查看对应服务帮助"

    @classmethod
    async def get_html_help(cls, event):
        # 走统一 TTL 节流，保证 service_dict 在一个刷新窗口内是冻结的
        cls.get_typed_services()
        level = 0
        user_id = str(event.user_id)
        if user_id in MASTER_LIST:
            level = 2
        elif isinstance(event, GroupMessageEvent) and event.sender.role in [
            "admin",
            "owner",
        ]:
            level = 1
        group_id = str(event.group_id) if isinstance(event, GroupMessageEvent) else ""
        # 轻量缓存键：不依赖完整服务数据
        signature_data = json.dumps(
            {
                "user_id": user_id,
                "group_id": group_id,
                "level": level,
                "cmd_prefix": CMD_PREFIX,
                "template_hash": _get_template_hash(),
                "service_dict_hash": cls._get_service_dict_hash(),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        cache_key = hashlib.sha256(signature_data.encode("utf-8")).hexdigest()
        if group_id:
            (PLUGIN_PATH / group_id).mkdir(parents=True, exist_ok=True)
            img_path = PLUGIN_PATH / group_id / f"{user_id}.png"
            json_path = PLUGIN_PATH / group_id / f"{user_id}.json"
        else:
            (PLUGIN_PATH / "user").mkdir(parents=True, exist_ok=True)
            img_path = PLUGIN_PATH / "user" / f"{user_id}.png"
            json_path = PLUGIN_PATH / "user" / f"{user_id}.json"
        # 先查缓存：命中则完全跳过服务遍历
        if json_path.exists() and img_path.exists():
            try:
                async with await anyio.open_file(
                    json_path, "r", encoding="utf-8"
                ) as f:
                    content = await f.read()
                cache_data = json.loads(content)
            except (json.JSONDecodeError, OSError, ValueError):
                cache_data = {}
            if cache_data.get("cache_key") == cache_key:
                return img_msg_from_path(img_path)
        # 缓存未命中：构造 services 并渲染
        services = {}
        for _type in Service.ServiceType:
            services[_type.value] = []
        s_l = list(ServiceTools.service_list.keys())
        s_l.sort()
        for s in s_l:
            _s: Service = ServiceTools.service_list[s]
            info = _s.get_info()
            _type = info.type
            if _type == Service.ServiceType.HIDDEN.value:
                continue
            if info.permission == "Master":
                if level < 2:
                    continue
            elif info.permission == "Admin" and level < 1:
                continue
            usable = False
            snapshot_entry = cls._service_snapshot.get(s)
            if snapshot_entry is not None:
                sc = snapshot_entry[2]
            else:
                # 快照缺失时兜底，正常不会走到这里
                try:
                    sc = ServiceTools(s).load_service_config()
                except Exception:
                    sc = None
            if sc is not None and sc.enabled:
                usable = True
                if user_id in sc.disable_user or (
                    isinstance(event, GroupMessageEvent)
                    and group_id in sc.disable_group
                ):
                    usable = False
            si = _s.get_info().model_dump()
            si["usable"] = usable
            services[_type].append(si)
        services = {
            k: v
            for k, v in services.items()
            if not (isinstance(v, list) and len(v) == 0)
        }
        if group_id:
            log.info(f"开始为群 {group_id} 中的用户 {user_id} 生成新的帮助")
        else:
            log.info(f"开始为用户 {user_id} 生成新的帮助")
        template = _JINJA_ENV.get_template("help.html")
        html_output = template.render(categories=services, cmd_str=CMD_PREFIX)
        img_data = await html_to_pic(
            html_output,
            viewport={"width": 800, "height": 600},
            device_scale_factor=1,
        )
        async with await anyio.open_file(img_path, "wb") as f:
            await f.write(img_data)
        json_content = json.dumps(
            {"cache_key": cache_key},
            indent=4,
            ensure_ascii=False,
            default=str,
        )
        async with await anyio.open_file(json_path, "w", encoding="utf-8") as f:
            await f.write(json_content)
        cls._cleanup_counter += 1
        if cls._cleanup_counter >= _CLEANUP_INTERVAL:
            cls._cleanup_counter = 0
            await anyio.to_thread.run_sync(cls._cleanup_cache)
        return img_msg(img_data)

    @staticmethod
    def service_info(service: str) -> str:
        try:
            data = ServiceTools(service).load_service()
            s_conf = ServiceTools(service).load_service_config()
        except ServiceNotFoundError:
            return f"请检查是否输入错误呢...{CMD_PREFIX}帮助 (服务)"

        service_name = data.service
        service_docs = data.docs
        service_enabled = s_conf.enabled

        service_cmd_list = "\n".join(
            f"{CMD_PREFIX}{name}" if cmd.get("type") == "command" else name
            for name, cmd in data.cmd_list.items()
        )

        repo = _SERVICE_INFO_FORMAT.format(
            service=service_name,
            docs=service_docs,
            cmd_list=service_cmd_list,
            enabled=service_enabled,
            cmd_prefix=CMD_PREFIX,
        )
        return repo

    @staticmethod
    def cmd_info(service: str, cmd: str) -> str:
        try:
            data = ServiceTools(service).load_service()
        except ServiceNotFoundError:
            return f"请检查是否输入错误...{CMD_PREFIX}帮助 (服务) (命令)"

        cmd_list: dict = data.cmd_list
        cmd_info = cmd_list.get(cmd, {})
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
        handler = help_type.get(help_config.help_type)
        if handler is None:
            log.warning(
                f"未知的 help_type 配置: {help_config.help_type!r}，"
                f"可用值: {list(help_type.keys())}"
            )
            handler = help_type["text"]
        result = handler(event)
        if inspect.isawaitable(result):
            result = await result
        return result

    @classmethod
    def _cleanup_cache(cls) -> None:
        """删除超过 TTL 的用户/群帮助缓存，并清理空目录。"""
        if not PLUGIN_PATH.exists():
            return
        cutoff = time.time() - _CACHE_TTL_SECONDS
        removed_files = 0
        removed_dirs = 0
        for entry in PLUGIN_PATH.iterdir():
            try:
                if entry.is_file():
                    # 跳过顶层受保护文件
                    if entry.name in _CACHE_ROOT_FILES:
                        continue
                    if entry.stat().st_mtime < cutoff:
                        entry.unlink()
                        removed_files += 1
                elif entry.is_dir():
                    for f in entry.iterdir():
                        if not f.is_file():
                            continue
                        try:
                            if f.stat().st_mtime < cutoff:
                                f.unlink()
                                removed_files += 1
                        except OSError:
                            continue
                    # 目录空了就删掉
                    try:
                        if not any(entry.iterdir()):
                            entry.rmdir()
                            removed_dirs += 1
                    except OSError:
                        pass
            except OSError:
                continue
        if removed_files or removed_dirs:
            log.info(
                f"帮助缓存清理完成: 删除 {removed_files} 个文件, "
                f"{removed_dirs} 个空目录"
            )

    @classmethod
    def _get_service_dict_hash(cls) -> str:
        """对 cls.service_dict 做 SHA-256，作为 HTML 缓存的轻量签名。"""
        normalized = {
            k: sorted(v) if isinstance(v, list) else v
            for k, v in cls.service_dict.items()
        }
        return hashlib.sha256(
            json.dumps(
                normalized,
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            ).encode("utf-8")
        ).hexdigest()


help_type["text"] = Helper.get_text_list
help_type["image"] = Helper.get_image_list
help_type["html"] = Helper.get_html_help
