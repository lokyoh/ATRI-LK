"""家：`home.md` 的模板、摘要、分节读写与备份。"""

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
海边小镇，坡道上的一栋旧式二层小屋，门口有一棵歪脖子树。

## 外观
白墙、深蓝色屋顶，二楼阳台能看到海。

## 房间
### 客厅
旧沙发、木茶几、一台唱机。
### 卧室
一张铺着米色被单的床，床头堆着几本书。
### 厨房
小灶台、冰箱、挂着两只杯子。
### 浴室
浴缸、洗手台，窗子能开。
### 阳台
两把椅子，晾衣杆，傍晚能看海。

## 周边
- 图书馆：步行 15 分钟
- 海边：步行 10 分钟
- 商店街：步行 20 分钟

## 物品
- 唱机：放在客厅，偶尔会放旧唱片。
- 鱼竿：靠在玄关。

## 衣柜
- 白色连衣裙、深蓝色外套、居家服、睡衣、帆布鞋、乐福鞋。

## 冰箱
- 鸡蛋、牛奶、昨天剩下的炖菜。
"""

MAX_HISTORY_FILES = 20
HOME_SUMMARY_BUDGET = 240
MAX_SECTION_LEVEL = 3

_SECTION_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")


def _split_sections(text: str) -> list[tuple[int, str, int, int]]:
    """返回 (级别, 标题, 起始, 结束)；二级章节包含其三级子章节。"""
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


class HomeStore:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "home.md"
        self.history_dir = root / "home_history"

    def ensure(self) -> None:
        """首次启动时从模板复制；只读路径不应依赖它。"""
        if self.path.is_file():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        template = HOME_TEMPLATE
        if HOME_TEMPLATE_FILE.is_file():
            try:
                template = HOME_TEMPLATE_FILE.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                log.warning(
                    f"家模板 {HOME_TEMPLATE_FILE} 读取失败，使用内置模板：{exc}"
                )
        self.path.write_text(template, encoding="utf-8")

    # 兼容旧调用名
    _ensure_exists = ensure

    def read(self) -> str:
        """读 home.md；文件还没落盘时返回模板（不写盘）。"""
        if not self.path.is_file():
            return HOME_TEMPLATE
        try:
            return self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            log.warning(f"读取 home.md 失败，返回空内容：{exc}")
            return ""

    def write(self, text: str) -> bool:
        """备份 → 写入 → 校验关键章节；校验失败回滚并拒绝。"""
        text = str(text or "").strip()
        if not self._has_required_sections(text):
            log.warning("拒绝写入 home.md：缺少 `## 位置` / `## 房间` / `###` 房间")
            return False
        self.ensure()
        self._backup()
        self.path.write_text(text + "\n", encoding="utf-8")
        return True

    @staticmethod
    def _has_required_sections(text: str) -> bool:
        """``## 位置`` 与 ``## 房间`` 必须存在，且房间下至少有一个 ``###``。

        只查两个二级标题不够：把 ``### 客厅/卧室/厨房`` 全删掉后仍然过检，
        于是「写坏了的家」会被静默保存。
        """
        if not re.search(r"^##\s+位置\s*$", text, re.MULTILINE):
            return False
        room = re.search(r"^##\s+房间\s*$", text, re.MULTILINE)
        if room is None:
            return False
        return bool(re.search(r"^###\s+\S+", text[room.end() :], re.MULTILINE))

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

    @staticmethod
    def _subsections(text: str, found: tuple[int, str, int, int]):
        """找到某章节内部的三级子章节（用于阻止误删子章节）。"""
        level, _, start, end = found
        return [
            item
            for item in _split_sections(text)
            if item[0] > level and start < item[2] < end
        ]

    def edit(self, section: str, content: str) -> str:
        """替换指定章节正文，保留标题。

        父章节（如 ``## 房间``）内含 ``###`` 子章节时**拒绝整体替换**：
        替换范围到下一个同级标题为止，会把客厅/卧室/厨房一起删掉，
        而 ``write()`` 只校验 ``## 位置`` / ``## 房间`` 存在，检查不出来。
        """
        text = self.read()
        sections = _split_sections(text)
        found = None
        key = str(section or "").strip().lower()
        for item in sections:
            if item[1].lower() == key:
                found = item
                break
        if found is None:
            for item in sections:
                if key and key in item[1].lower():
                    found = item
                    break
        if found is None:
            names = "、".join(title for _, title, _, _ in sections)
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
        if not self.write(new_text):
            return "写入失败：修改后的家缺少 `## 位置` / `## 房间` / `###` 房间"
        return f"已更新「{title}」"

    def add(self, section: str, content: str) -> str:
        """在指定章节末尾追加内容。"""
        text = self.read()
        sections = _split_sections(text)
        target = None
        key = str(section or "").strip().lower()
        for item in sections:
            if item[1].lower() == key:
                target = item
                break
        if target is None:
            for item in sections:
                if key and key in item[1].lower():
                    target = item
                    break
        if target is None:
            names = "、".join(title for _, title, _, _ in sections)
            return f"家里没有「{section}」这一节。现有章节：{names}"
        _, title, start, end = target
        chunk = "\n" + str(content or "").strip() + "\n"
        new_text = text[:end].rstrip("\n") + "\n" + chunk + "\n" + text[end:]
        if not self.write(new_text):
            return "写入失败：修改后的家缺少 `## 位置` / `## 房间` / `###` 房间"
        return f"已向「{title}」追加内容"

    def summary(self) -> str:
        """给提示词的家摘要：位置 + 房间名 + 周边前 3 条。"""
        text = self.read()
        sections = _split_sections(text)
        by_title: dict[str, tuple[int, str, int, int]] = {
            title: (level, title, start, end) for level, title, start, end in sections
        }

        def body_of(name: str) -> str:
            item = by_title.get(name)
            if item is None:
                return ""
            _, title, start, end = item
            body = text[start:end]
            body = re.sub(r"^#{2,3}\s+.+?\n", "", body, count=1)
            return body.strip()

        location = body_of("位置").replace("\n", " ")[:100]
        rooms = [
            title
            for level, title, _, _ in sections
            if level == 3 and by_title.get("房间")
        ]
        nearby = [
            line.lstrip("- ").strip()
            for line in body_of("周边").splitlines()
            if line.strip().startswith("-")
        ][:3]
        parts = [f"位置：{location or '未记录'}"]
        if rooms:
            parts.append("房间：" + "、".join(rooms))
        if nearby:
            parts.append("周边：" + "、".join(nearby))
        return "\n".join(parts)[:HOME_SUMMARY_BUDGET]

    async def generate(self) -> tuple[bool, str]:
        """用 `ATRI_HOME` 扩写；缺模型时返回降级提示。"""
        from ...agent.role import ATRI
        from ...llm import ModelType, llm_manager

        if not llm_manager.has_type(ModelType.ATRI_HOME):
            return False, "未配置家模型，已使用预设"
        prompt = (
            "根据下面的模板与角色设定，扩写亚托莉的家，保留每个二级标题，"
            "内容更具体但仍是她会住的地方。只输出 Markdown 正文。\n\n"
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
        if not self._has_required_sections(text):
            return (
                False,
                "家模型输出缺少 `## 位置` / `## 房间` / `###` 房间，保留当前内容",
            )
        if not self.write(text):
            return False, "写入失败：修改后的家缺少 `## 位置` / `## 房间` / `###` 房间"
        return True, "家已更新"


__all__ = ["HOME_TEMPLATE", "HomeStore", "read_json"]
