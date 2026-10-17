from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.system.agent.agent.skills import skill_manager

from ....base_model import Result
from ....utils import authentication

# 前缀由所属的 agent 包级 router（prefix="/agent"）提供：
# FastAPI 的 include_router 会拼接前缀，子模块再写 prefix="/agent" 会变成 /agent/agent。
router = APIRouter()


class SkillInfo(BaseModel):
    name: str
    """技能名（= 文件名主干）"""
    description: str
    """技能说明"""
    source: str
    """builtin / user / modified"""
    enabled: bool
    """启用开关：false 时既不入提示词也不可查询"""
    embedded: bool
    """内嵌开关：true 时正文直接写入提示词，且不可被 agent 查询"""
    deletable: bool
    """是否可删除（仅内置技能不可删除）"""
    content_length: int
    """正文长度（字符数）：内嵌技能会直接膨胀提示词，UI 据此提示用户"""


class SkillDetail(SkillInfo):
    content: str
    """Markdown 正文"""


class SaveSkillModel(BaseModel):
    name: str
    description: str = ""
    content: str = ""


class SetSkillStateModel(BaseModel):
    name: str
    enabled: bool | None = None
    embedded: bool | None = None


def _to_detail(skill) -> SkillDetail:
    return SkillDetail(
        name=skill.name,
        description=skill.description,
        source=str(skill.source),
        enabled=skill.enabled,
        embedded=skill.embedded,
        deletable=skill.deletable,
        content_length=len(skill.content),
        content=skill.content,
    )


def _to_info(skill) -> SkillInfo:
    return SkillInfo(
        name=skill.name,
        description=skill.description,
        source=str(skill.source),
        enabled=skill.enabled,
        embedded=skill.embedded,
        deletable=skill.deletable,
        content_length=len(skill.content),
    )


@router.get(
    "/get_skills",
    dependencies=[authentication()],
    response_model=Result[list[SkillInfo]],
    response_class=JSONResponse,
    description="获取技能列表（内置与用户配置的合并视图）",
)
async def _() -> Result[list[SkillInfo]]:
    try:
        return Result.ok([_to_info(s) for s in skill_manager.list_all()], "拿到信息啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_skills 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_skill",
    dependencies=[authentication()],
    response_model=Result[SkillDetail],
    response_class=JSONResponse,
    description="获取单个技能详情",
)
async def _(name: str) -> Result[SkillDetail]:
    try:
        skill = skill_manager.get(name)
        if skill is None:
            return Result.fail(f"技能 {name} 不存在", 404)
        return Result.ok(_to_detail(skill), "拿到信息啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_skill 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/save_skill",
    dependencies=[authentication()],
    response_model=Result[SkillInfo],
    response_class=JSONResponse,
    description="新建或编辑用户技能（同名内置会被覆盖为「内置（已修改）」）",
)
async def _(payload: SaveSkillModel) -> Result[SkillInfo]:
    try:
        skill = skill_manager.save_user(
            payload.name, payload.description, payload.content
        )
        return Result.ok(_to_info(skill), "保存成功!")
    except ValueError as e:
        return Result.fail(str(e), 400)
    except Exception as e:
        log.error(f"{router.prefix}/save_skill 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/delete_skill",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="删除用户技能（删除内置覆盖即恢复内置）",
)
async def _(name: str) -> Result:
    try:
        target = skill_manager.get(name)
        if target is None:
            return Result.fail(f"技能 {name} 不存在", 404)
        if not target.deletable:
            return Result.fail("内置技能不可删除", 400)
        skill_manager.delete_user(name)
        return Result.ok(info="删除成功!")
    except ValueError as e:
        return Result.fail(str(e), 400)
    except Exception as e:
        log.error(f"{router.prefix}/delete_skill 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/set_skill_state",
    dependencies=[authentication()],
    response_model=Result[SkillInfo],
    response_class=JSONResponse,
    description="设置技能的启用 / 内嵌开关",
)
async def _(payload: SetSkillStateModel) -> Result[SkillInfo]:
    try:
        if payload.enabled is None and payload.embedded is None:
            return Result.fail("未提供任何开关状态", 400)
        if payload.enabled is not None:
            skill_manager.set_enabled(payload.name, payload.enabled)
        if payload.embedded is not None:
            skill_manager.set_embedded(payload.name, payload.embedded)
        skill = skill_manager.get(payload.name)
        if skill is None:
            return Result.fail(f"技能 {payload.name} 不存在", 404)
        return Result.ok(_to_info(skill), "设置成功!")
    except ValueError as e:
        return Result.fail(str(e), 404)
    except Exception as e:
        log.error(f"{router.prefix}/set_skill_state 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")
