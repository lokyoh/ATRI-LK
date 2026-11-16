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

#: 家模型改一节的产出上限，避免越写越长挤占提示词。
MAX_SECTION_CHARS = 800
MAX_ROOMS = 12
MAX_CONTAINERS = 12

#: 家模型改写的最多尝试次数（含首次）；每次都把上一次的问题回传给它。
MAX_HOME_ATTEMPTS = 3

#: 摘要每一项的上限；超预算时按条目边界截断，不切半个词。
MAX_LOCATION_CHARS = 100
MAX_NEARBY_IN_SUMMARY = 3
MAX_HABITS_IN_SUMMARY = 8
_MIN_TAIL = 12
_ELLIPSIS = "…"

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


def _clip(text: str, limit: int) -> str:
    """截断并补省略号，避免出现半句话。"""
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + _ELLIPSIS


def _pack(prefix: str, items: list[str], budget: int) -> str:
    """把条目拼进一行，放不下就在条目边界停下。"""
    body = ""
    for item in items:
        piece = ("、" + item) if body else item
        if len(prefix) + len(body) + len(piece) > budget:
            if not body:
                room = budget - len(prefix) - 1
                if room < _MIN_TAIL:
                    return ""
                body = _clip(item, room)
            else:
                body += _ELLIPSIS
            break
        body += piece
    return f"{prefix}{body}" if body else ""


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
        """旧格式只看旧章节名：缺节由 write() 拦，不该反过来覆盖整个家。"""
        return any(name in cls._top_titles(text) for name in LEGACY_SECTIONS)

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
            title
            for title in titles
            if title in LEGACY_SECTIONS or title not in FIXED_SECTIONS
        ]
        if unknown:
            allowed = "、".join(FIXED_SECTIONS)
            return f"章节「{unknown[0]}」不在固定章节里（可用：{allowed}）"
        if SECTION_LOCATION not in titles:
            return f"缺少 `## {SECTION_LOCATION}`"
        if SECTION_ROOMS not in titles:
            return f"缺少 `## {SECTION_ROOMS}`"
        room = next(item for item in sections if item[1] == SECTION_ROOMS)
        if not any(item[0] == 3 and room[2] < item[2] < room[3] for item in sections):
            return f"`## {SECTION_ROOMS}` 下至少要有一个 `###` 房间"
        return HomeStore._container_problem(sections)

    @staticmethod
    def _container_problem(sections: list[tuple[int, str, int, int]]) -> str:
        """容器层额外两条：名字不能重、不能再用 #### 分组（会丢物品）。"""
        holder = next(
            (item for item in sections if item[1] == SECTION_CONTAINERS), None
        )
        if holder is None:
            return ""
        _, _, start, end = holder
        names: list[str] = []
        current = ""
        for level, title, item_start, _ in sections:
            if not start < item_start < end:
                continue
            if level == 3:
                if title in names:
                    return (
                        f"`## {SECTION_CONTAINERS}` 下有重名容器「{title}」，会互相覆盖"
                    )
                names.append(title)
                current = title
            elif level > 3:
                return f"容器「{current}」里不要再分「{title}」：物品请写成「- 名称：说明」"
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
            name: _strip_heading(text[spans[name][0] : spans[name][1]])
            if name in spans
            else ""
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
        found = next((item for item in sections if item[1] == SECTION_CONTAINERS), None)
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

    async def rewrite(self, section: str, intent: str, *, append: bool = False) -> str:
        """让家模型按意图重写某一节；她只给意图，格式由模型保证。"""
        from ...agent.role import ATRI
        from ...llm import llm_manager
        from .system import extract_json

        title = str(section or "").strip()
        hint = str(intent or "").strip()
        if not title or not hint:
            return "需要 section 与想要怎么改的说明"
        if not llm_manager.has_slot("home"):
            return "未配置家模型，暂时不能改家的内容"
        found = self._find(self.read(), title)
        if found is None:
            names = "、".join(t for _, t, _, _ in _split_sections(self.read()))
            return f"家里没有「{title}」这一节。现有章节：{names}"
        current = _strip_heading(self.read()[found[2] : found[3]])
        mode = "在现有内容后追加，不要删掉已有的" if append else "按意图改写这一节"
        base = (
            f"你在改写「亚托莉的家」中的「{found[1]}」这一节。\n"
            f"要做什么：{hint}\n"
            f"写法：{mode}；房间写成「### 房间名」+ 一行描述，"
            "容器写成「### 容器名」+「- 名称：说明」；容器名不能重复，容器内不再分 ####。\n"
            f"整节不超过 {MAX_SECTION_CHARS} 字，不要输出节标题，也不要输出其它章节。\n"
            '只输出 JSON：{"body": "..."}\n\n'
            f"角色：{ATRI.get_role_prompt()}\n\n"
            f"现有内容：\n{current or '（空）'}"
        )

        # 每次失败都把具体问题回传给模型，最多试 MAX_HOME_ATTEMPTS 次
        feedback = ""
        problem = ""
        for attempt in range(MAX_HOME_ATTEMPTS):
            prompt = base + feedback
            try:
                response = await llm_manager.call_agent(
                    "home", prompt, "Agent-家改写", app_id="home"
                )
                payload = extract_json(response.content)
                body = str(payload.get("body") or "").strip()
            except Exception as exc:
                problem = f"输出不是可解析的 JSON（{exc}）"
            else:
                if not body:
                    problem = "body 为空"
                else:
                    problem = self._budget_problem(
                        found[1], body
                    ) or self._apply_problem(found[1], body)
                if not problem:
                    return self.replace(found[1], body)
            log.warning(f"家模型第 {attempt + 1} 次改写未通过（{found[1]}）：{problem}")
            feedback = (
                f"\n\n上一次的输出有问题：{problem}。"
                "请修正后重新输出，只给 JSON，不要解释。"
            )
        return f"家没有改：{problem}（已尝试 {MAX_HOME_ATTEMPTS} 次，内容保持原样）"

    @staticmethod
    def _budget_problem(section: str, body: str) -> str:
        """单节字数与条目数上限：防止反复追加把家写胖。"""
        text = str(body or "").strip()
        if len(text) > MAX_SECTION_CHARS:
            return f"「{section}」超过 {MAX_SECTION_CHARS} 字，请精简后再写"
        if section == SECTION_ROOMS and text.count("### ") > MAX_ROOMS:
            return f"房间超过 {MAX_ROOMS} 个"
        if section == SECTION_CONTAINERS and text.count("### ") > MAX_CONTAINERS:
            return f"容器超过 {MAX_CONTAINERS} 个"
        return ""

    def _apply_problem(self, section: str, body: str) -> str:
        """预演这次写入能不能过章节校验，不改文件。"""
        text = self.read()
        found = self._find(text, section)
        if found is None:
            return f"家里没有「{section}」这一节"
        level, title, start, end = found
        draft = text[:start] + f"{'#' * level} {title}\n{body}\n\n" + text[end:]
        return self._section_problem(draft)

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
        note = ""
        if title == SECTION_CONTAINERS and not self.containers():
            note = "；注意：容器已清空，生成穿搭时没有衣物可参考"
        return f"已更新「{title}」{note}"

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

        location = _clip(
            " ".join(body_of(SECTION_LOCATION).split()), MAX_LOCATION_CHARS
        )
        room_span = spans.get(SECTION_ROOMS)
        rooms = [
            title
            for level, title, start, _ in sections
            if level == 3 and room_span and room_span[0] < start < room_span[1]
        ]
        nearby_all = _bullets(body_of(SECTION_NEARBY))
        habits_all = _bullets(body_of(SECTION_HABITS))
        nearby = nearby_all[:MAX_NEARBY_IN_SUMMARY]
        habits = habits_all[:MAX_HABITS_IN_SUMMARY]

        lines = [f"位置：{location or '未记录'}"]
        used = len(lines[0])
        # 条目被上限截掉时补省略号，否则看不出还有没写下的内容
        for prefix, items, dropped in (
            ("房间：", rooms, False),
            ("周边：", nearby, len(nearby_all) > len(nearby)),
            ("习惯：", habits, len(habits_all) > len(habits)),
        ):
            room = HOME_SUMMARY_BUDGET - used - 1
            line = _pack(prefix, items, room)
            if line and dropped and len(line) < room - 1:
                line += _ELLIPSIS
            if not line:
                continue
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)


__all__ = [
    "FIXED_SECTIONS",
    "HOME_TEMPLATE",
    "HomeStore",
    "read_json",
]
