"""家：`home.md` 的固定章节、摘要、读写与备份。"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

from ATRI.dir import RES_DATA_DIR
from ATRI.log import log
from ATRI.utils.datetime import now

from .store import read_json

HOME_TEMPLATE_FILE = RES_DATA_DIR / "agent" / "life" / "home.default.md"
HOME_TEMPLATE = """# 亚托莉的家

## 位置
海边小镇，坡道上的一栋旧式二层小屋。门口有一棵歪脖子树，白墙、深蓝色屋顶，
二楼阳台能看到海。

## 房间
### 客厅
旧沙发、木茶几、一台唱机，墙边立着一排旧唱片。

### 卧室
铺着米色被单的床，床头堆着几本书，窗台上有一只小闹钟。

### 厨房
小灶台、一台嗡嗡响的旧冰箱、橱柜上挂着两只杯子。

### 浴室
浴缸、洗手台，窗子能推开，洗完澡能看见海上的雾。

### 阳台
两把椅子、一根晾衣杆，傍晚能看海。

## 周边
- 图书馆：步行 15 分钟
- 海边：步行 10 分钟
- 商店街：步行 20 分钟

## 容器
### 衣柜
- 白色连衣裙：白天外出
- 深蓝色外套：降温或夜里出门
- 米色居家服：在家
- 帆布鞋：白天
- 乐福鞋：想正式一点的时候
- 睡衣：睡觉

### 冰箱
- 鸡蛋：三天前买的
- 牛奶：快见底了
- 炖菜：昨天剩下的

### 抽屉
- 针线包：缝扣子用
- 旧信件：一直没舍得扔

### 相册
- 海边的合影：去年夏天
- 毕业照：压在最后一页

## 习惯
- 进门先换鞋，鞋子摆进玄关
- 睡前把阳台的窗关好
- 泡茶时会把杯子先温一遍"""

#: 固定章节名。改名或新增章节都会被写入校验拒绝。
SECTION_LOCATION = "位置"
SECTION_ROOMS = "房间"
SECTION_NEARBY = "周边"
SECTION_CONTAINERS = "容器"
SECTION_HABITS = "习惯"
FIXED_SECTIONS = (
    SECTION_LOCATION,
    SECTION_ROOMS,
    SECTION_NEARBY,
    SECTION_CONTAINERS,
    SECTION_HABITS,
)

#: 旧版章节名，出现在文件里就说明是旧格式。
LEGACY_SECTIONS = ("外观", "物品", "衣柜", "冰箱")

MAX_HISTORY_FILES = 20
HOME_SUMMARY_BUDGET = 320
MAX_SECTION_LEVEL = 4

_SECTION_RE = re.compile(rf"^(#{{2,{MAX_SECTION_LEVEL}}})\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+")


def _split_sections(text: str) -> list[tuple[int, str, int, int]]:
    """返回 (级别, 标题, 起始, 结束)；上级章节包含其全部子章节。"""
    lines = (text or "").splitlines(keepends=True)
    headings: list[tuple[int, str, int, int]] = []
    offset = 0
    for line in lines:
        match = _SECTION_RE.match(line.rstrip("\r\n"))
        headings.append(
            (
                len(match.group(1)) if match else 0,
                match.group(2) if match else "",
                offset,
                offset + len(line),
            )
        )
        offset += len(line)
    result: list[tuple[int, str, int, int]] = []
    for index, (level, title, start, _) in enumerate(headings):
        if level == 0:
            continue
        end = len(text or "")
        for next_level, _, next_start, _ in headings[index + 1 :]:
            if 0 < next_level <= level:
                end = next_start
                break
        result.append((level, title, start, end))
    return result


_HEADING_LINE_RE = re.compile(rf"^#{{2,{MAX_SECTION_LEVEL}}}\s+")


def _strip_heading(block: str) -> str:
    """去掉章节块的第一行标题，留下正文。"""
    lines = str(block or "").splitlines()
    while lines and _HEADING_LINE_RE.match(lines[0]):
        lines.pop(0)
    return "\n".join(lines).strip()


def _bullets(block: str) -> list[str]:
    """取正文里的清单项，去掉「- 」前缀。"""
    return [
        _BULLET_RE.sub("", line).strip()
        for line in str(block or "").splitlines()
        if _BULLET_RE.match(line)
    ]


class HomeStore:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "home.md"
        self.history_dir = root / "home_history"

    def ensure(self) -> None:
        """首次启动写模板；旧格式的家直接换成模板。"""
        if self.path.is_file() and not self._is_legacy(self.read()):
            return
        self.root.mkdir(parents=True, exist_ok=True)
        if self.path.is_file():
            log.info("家的章节结构已变更，按新模板重建（旧内容已备份）")
            self._backup()
        self.path.write_text(self._template(), encoding="utf-8")

    # 兼容旧调用名
    _ensure_exists = ensure

    @staticmethod
    def _template() -> str:
        if HOME_TEMPLATE_FILE.is_file():
            try:
                return HOME_TEMPLATE_FILE.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                log.warning(
                    f"家模板 {HOME_TEMPLATE_FILE} 读取失败，使用内置模板：{exc}"
                )
        return HOME_TEMPLATE

    @staticmethod
    def _top_titles(text: str) -> set[str]:
        """只看二级章节：三级标题是房间名或容器名，不受固定清单约束。"""
        return {title for level, title, _, _ in _split_sections(text) if level == 2}

    @classmethod
    def _is_legacy(cls, text: str) -> bool:
        """旧格式：含旧章节，或缺少任一固定章节。"""
        titles = cls._top_titles(text)
        if any(name in titles for name in LEGACY_SECTIONS):
            return True
        return any(name not in titles for name in FIXED_SECTIONS)

    def read(self) -> str:
        """读 home.md；文件还没落盘时返回模板（不写盘）。"""
        if not self.path.is_file():
            return self._template()
        try:
            return self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            log.warning(f"读取 home.md 失败，返回空内容：{exc}")
            return ""

    def write(self, text: str) -> bool:
        """备份 → 写入 → 校验章节；校验失败拒绝写入。"""
        text = str(text or "").strip()
        problem = self._section_problem(text)
        if problem:
            log.warning(f"拒绝写入 home.md：{problem}")
            return False
        self.root.mkdir(parents=True, exist_ok=True)
        self._backup()
        self.path.write_text(text + "\n", encoding="utf-8")
        return True

    @staticmethod
    def _section_problem(text: str) -> str:
        """返回不通过的原因，空串表示可以写入。"""
        body = str(text or "")
        sections = _split_sections(body)
        titles = {title for level, title, _, _ in sections if level == 2}
        unknown = [
            title for title in titles if title in LEGACY_SECTIONS or title not in FIXED_SECTIONS
        ]
        if unknown:
            allowed = "、".join(FIXED_SECTIONS)
            return f"章节「{unknown[0]}」不在固定章节里（可用：{allowed}）"
        if SECTION_LOCATION not in titles:
            return f"缺少 `## {SECTION_LOCATION}`"
        if SECTION_ROOMS not in titles:
            return f"缺少 `## {SECTION_ROOMS}`"
        if not any(level == 3 for level, _, _, _ in sections):
            return f"`## {SECTION_ROOMS}` 下至少要有一个 `###` 房间"
        return ""

    def _backup(self) -> None:
        if not self.path.is_file():
            return
        self.history_dir.mkdir(parents=True, exist_ok=True)
        stamp = now().strftime("%Y-%m-%dT%H-%M-%S")
        target = self.history_dir / f"{stamp}.md"
        try:
            target.write_text(self.read(), encoding="utf-8")
        except Exception as exc:
            log.warning(f"家内容备份失败（继续写入）：{exc}")
            return
        backups = sorted(self.history_dir.glob("*.md"))
        for old in backups[:-MAX_HISTORY_FILES]:
            with contextlib.suppress(OSError):
                old.unlink()

    def section(self, query: str) -> tuple[str, str] | None:
        """按标题或标题片段找章节，返回 (标题, 正文含标题)。"""
        text = self.read()
        target = str(query or "").strip()
        if not target:
            return None
        key = target.lower()
        sections = _split_sections(text)
        for _level, title, start, end in sections:
            if title.lower() == key:
                return title, text[start:end].strip()
        for _level, title, start, end in sections:
            if key in title.lower():
                return title, text[start:end].strip()
        return None

    def show(self, section: str = "") -> str:
        text = self.read()
        if not section:
            return text.strip()
        found = self.section(section)
        if found is None:
            names = "、".join(title for _, title, _, _ in _split_sections(text))
            return f"家里没有「{section}」这一节。现有章节：{names}"
        return found[1]

    def section_bodies(self) -> dict[str, str]:
        """固定章节 → 正文（不含标题行），供 WebUI 按节渲染。"""
        text = self.read()
        spans = {title: (start, end) for _, title, start, end in _split_sections(text)}
        return {
            name: _strip_heading(text[spans[name][0] : spans[name][1]]) if name in spans else ""
            for name in FIXED_SECTIONS
        }

    def rooms(self) -> list[tuple[str, str]]:
        """`## 房间` 下的房间名与描述。"""
        text = self.read()
        sections = _split_sections(text)
        room = next((item for item in sections if item[1] == SECTION_ROOMS), None)
        if room is None:
            return []
        level, _, start, end = room
        return [
            (title, _strip_heading(text[item_start:item_end]))
            for item_level, title, item_start, item_end in sections
            if item_level == level + 1 and start < item_start < end
        ]

    def containers(self) -> dict[str, list[str]]:
        """`## 容器` 下的每个容器与它的清单。"""
        text = self.read()
        sections = _split_sections(text)
        found = next(
            (item for item in sections if item[1] == SECTION_CONTAINERS), None
        )
        if found is None:
            return {}
        level, _, start, end = found
        result: dict[str, list[str]] = {}
        for item_level, title, item_start, item_end in sections:
            if item_level != 3 or not (start < item_start < end):
                continue
            result[title] = _bullets(_strip_heading(text[item_start:item_end]))
        return result

    def wardrobe(self) -> str:
        """衣柜清单，供生成穿搭。"""
        items = self.containers().get("衣柜")
        return "、".join(items) if items else ""

    @staticmethod
    def _subsections(text: str, found: tuple[int, str, int, int]):
        """找到某章节内部的子章节（用于阻止误删子章节）。"""
        level, _, start, end = found
        return [
            item
            for item in _split_sections(text)
            if item[0] > level and start < item[2] < end
        ]

    def edit(self, section: str, content: str) -> str:
        """替换指定章节正文，保留标题。

        父章节（如 `## 房间`）内含 `###` 子章节时拒绝整体替换：
        替换范围到下一个同级标题为止，会把客厅/卧室/厨房一起删掉。
        """
        text = self.read()
        found = self._find(text, section)
        if found is None:
            names = "、".join(title for _, title, _, _ in _split_sections(text))
            return f"家里没有「{section}」这一节。现有章节：{names}"
        children = self._subsections(text, found)
        if children:
            names = "、".join(item[1] for item in children)
            return (
                f"「{found[1]}」下面还有子章节：{names}。"
                "整体替换会连子章节一起删掉，请指定要改的子章节，"
                "或用 add 往这一节末尾追加。"
            )
        level, title, start, end = found
        heading = f"{'#' * level} {title}\n"
        new_text = (
            text[:start] + heading + str(content or "").strip() + "\n\n" + text[end:]
        )
        problem = self._section_problem(new_text)
        if problem:
            return f"写入失败：{problem}"
        if not self.write(new_text):
            return "写入失败：章节校验未通过"
        return f"已更新「{title}」"

    def replace(self, section: str, content: str) -> str:
        """整块替换，含子章节。`edit` 拒绝父章节，WebUI 编辑房间/容器时用这个。"""
        text = self.read()
        found = self._find(text, section)
        if found is None:
            names = "、".join(title for _, title, _, _ in _split_sections(text))
            return f"家里没有「{section}」这一节。现有章节：{names}"
        level, title, start, end = found
        heading = f"{'#' * level} {title}\n"
        body = str(content or "").strip()
        new_text = text[:start] + heading + body + "\n\n" + text[end:]
        problem = self._section_problem(new_text)
        if problem:
            return f"写入失败：{problem}"
        if not self.write(new_text):
            return "写入失败：章节校验未通过"
        return f"已更新「{title}」"

    def _find(self, text: str, section: str) -> tuple[int, str, int, int] | None:
        """先精确匹配标题，再退回标题包含关键字。"""
        key = str(section or "").strip().lower()
        if not key:
            return None
        sections = _split_sections(text)
        for item in sections:
            if item[1].lower() == key:
                return item
        for item in sections:
            if key in item[1].lower():
                return item
        return None

    def add(self, section: str, content: str) -> str:
        """在指定章节末尾追加内容。"""
        text = self.read()
        target = self._find(text, section)
        if target is None:
            names = "、".join(title for _, title, _, _ in _split_sections(text))
            return f"家里没有「{section}」这一节。现有章节：{names}"
        _, title, start, end = target
        chunk = "\n" + str(content or "").strip() + "\n"
        new_text = text[:end].rstrip("\n") + "\n" + chunk + "\n" + text[end:]
        problem = self._section_problem(new_text)
        if problem:
            return f"写入失败：{problem}"
        if not self.write(new_text):
            return "写入失败：章节校验未通过"
        return f"已向「{title}」追加内容"

    def summary(self) -> str:
        """提示词摘要：位置 + 房间名 + 周边 + 习惯。容器不进摘要。"""
        text = self.read()
        sections = _split_sections(text)
        spans = {title: (start, end) for _, title, start, end in sections}

        def body_of(name: str) -> str:
            span = spans.get(name)
            return _strip_heading(text[span[0] : span[1]]) if span else ""

        location = " ".join(body_of(SECTION_LOCATION).split())[:100]
        room_span = spans.get(SECTION_ROOMS)
        rooms = [
            title
            for level, title, start, _ in sections
            if level == 3 and room_span and room_span[0] < start < room_span[1]
        ]
        parts = [f"位置：{location or '未记录'}"]
        if rooms:
            parts.append("房间：" + "、".join(rooms))
        nearby = _bullets(body_of(SECTION_NEARBY))[:3]
        if nearby:
            parts.append("周边：" + "、".join(nearby))
        habits = _bullets(body_of(SECTION_HABITS))
        if habits:
            parts.append("习惯：" + "、".join(habits))
        return "\n".join(parts)[:HOME_SUMMARY_BUDGET]

    async def generate(self) -> tuple[bool, str]:
        """用 `ATRI_HOME` 扩写；缺模型时返回降级提示。"""
        from ...agent.role import ATRI
        from ...llm import ModelType, llm_manager

        if not llm_manager.has_type(ModelType.ATRI_HOME):
            return False, "未配置家模型，已使用预设"
        prompt = (
            "根据下面的模板与角色设定，扩写亚托莉的家。\n"
            f"章节固定为 {'、'.join(FIXED_SECTIONS)}，不要改名也不要新增；\n"
            f"`## {SECTION_ROOMS}` 用 `###` 罗列房间，`## {SECTION_CONTAINERS}` "
            "用 `###` 罗列容器（衣柜、冰箱、相册这类能装东西的实体或虚拟物件），",
            "容器下列「- 名称：说明」；`## 习惯` 列她的居家小规矩。\n"
            "只输出 Markdown 正文。\n\n"
            f"角色：{ATRI.get_role_prompt()}\n\n模板：\n{self.read()}"
        )
        try:
            response = await llm_manager.call_agent(
                ModelType.ATRI_HOME, prompt, "Agent-家设定"
            )
        except Exception as exc:
            log.warning(f"家生成失败，保留当前内容：{exc}")
            return False, "家生成失败，保留当前内容"
        text = str(response.content or "").strip()
        problem = self._section_problem(text)
        if problem:
            return False, f"家模型输出有问题：{problem}，保留当前内容"
        if not self.write(text):
            return False, "写入失败：章节校验未通过"
        return True, "家已更新"


__all__ = [
    "FIXED_SECTIONS",
    "HOME_TEMPLATE",
    "HomeStore",
    "read_json",
]