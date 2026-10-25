import yaml

from ATRI.dir import RES_DATA_DIR
from ATRI.log import log

from .model import (
    MAX_SKILL_DESCRIPTION_LENGTH,
    Skill,
    SkillSource,
)

BUILTIN_SKILLS_DIR = RES_DATA_DIR / "agent" / "skills"
"""内置技能目录：随仓库分发，只读，程序永不写入"""

FUNCTION_PLUGIN_SKILLS_DIR = RES_DATA_DIR / "agent" / "function_plugins"

USER_SKILLS_SUBDIR = "skills"
"""用户技能目录相对插件数据目录的子路径"""

SUFFIX = ".md"


def parse_skill_text(text: str) -> tuple[dict, str]:
    """切分可选 YAML frontmatter 与 Markdown 正文。

    首行必须为 `---`；找不到结束 `---` 则整体视为正文，不报错。
    frontmatter 解析失败同样回退为"无 frontmatter"。
    """
    if not text.startswith("---"):
        return {}, text
    # 只把 `---\n` 与 `---\r\n` 当作分隔符，避免 `----` 之类的误判
    lines = text.splitlines(keepends=True)
    if lines[0].strip() != "---":
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() != "---":
            continue
        raw_meta = "".join(lines[1:index])
        body = "".join(lines[index + 1 :])
        try:
            meta = yaml.safe_load(raw_meta)
        except yaml.YAMLError as e:
            log.warning(f"技能 frontmatter 解析失败，按纯正文处理:{e}")
            return {}, text
        if not isinstance(meta, dict):
            meta = {}
        return meta, body.lstrip("\r\n")
    return {}, text


def extract_description(meta: dict, content: str) -> str:
    description = str(meta.get("description") or "").strip()
    if description:
        return description
    for line in content.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:MAX_SKILL_DESCRIPTION_LENGTH]
    return ""


def load_skill_file(path, source: SkillSource) -> Skill | None:
    name = path.stem
    declared = None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        log.warning(f"技能文件 {path} 读取失败:{e}")
        return None
    meta, content = parse_skill_text(text)
    declared = meta.get("name")
    if declared is not None and str(declared).strip() != name:
        # 名称以文件名为准，避免覆盖匹配错乱
        log.warning(
            f"技能文件 {path} 声明的 name={declared!r} 与文件名不一致，已忽略声明名"
        )
    return Skill(
        name=name,
        description=extract_description(meta, content),
        content=content,
        source=source,
    )


def _scan(directory, source: SkillSource) -> dict[str, Skill]:
    skills: dict[str, Skill] = {}
    if not directory.is_dir():
        return skills
    for path in sorted(directory.iterdir(), key=lambda p: p.name):
        if not path.is_file() or path.suffix.lower() != SUFFIX:
            continue
        if path.name.startswith("."):
            continue
        skill = load_skill_file(path, source)
        if skill is not None:
            skills[skill.name] = skill
    return skills


def scan_merged(user_dir) -> dict[str, Skill]:
    """双目录扫描并合并：用户同名文件覆盖内置，标记为「内置（已修改）」。"""
    builtin = _scan(BUILTIN_SKILLS_DIR, SkillSource.BUILTIN)
    user = _scan(user_dir, SkillSource.USER)

    merged: dict[str, Skill] = {}
    for name, skill in builtin.items():
        if name in user:
            replacement = user[name].model_copy(deep=True)
            replacement.source = SkillSource.MODIFIED
            merged[name] = replacement
        else:
            merged[name] = skill
    for name, skill in user.items():
        if name not in merged:
            merged[name] = skill
    return dict(sorted(merged.items(), key=lambda item: item[0]))
