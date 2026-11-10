from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ATRI.exceptions import str_traceback
from ATRI.log import log
from ATRI.system.lkbot.data.user import user_level_manager, users
from ATRI.system.lkbot.util import BaseFunc

from ....base_model import Result
from ....utils import authentication

router = APIRouter(prefix="/lkbot")

#: 列表里一次最多返回多少人。用户表是内存字典，全量遍历不贵，
#: 但一次塞几千行进浏览器毫无意义，前端也有分页/搜索。
MAX_LIST_USERS = 500


class LkUserInfo(BaseModel):
    id: str
    """账号 ID"""
    name: str
    """lkbot 里登记的名称"""
    level: int
    """等级（由经验换算）"""
    exp: int
    """当前总经验"""
    left_exp: int
    """升到下一级还差多少"""
    money: int
    """ATRI 币"""
    love: int
    """好感度"""
    signdays: int
    """累计签到天数"""
    lastsign: str
    """最后一次签到的日期"""
    is_master: bool
    """是否为主人（含绑定到主人的账号解析后的结果）"""
    bound_to: str = ""
    """如果账号绑定到其他账号，这里是最终主账号"""


class LkBindingInfo(BaseModel):
    account_id: str
    """被绑定的账号（小号）"""
    master_id: str
    """解析后的主账号"""
    master_name: str
    """主账号的名称；主账号已被删除时为空"""


class LkBackpackItem(BaseModel):
    name: str
    """物品名"""
    type: str
    """物品种类，如「工具」「道具」（ItemType.value）"""
    num: int
    """数量"""
    extra: dict = {}
    """物品的额外数据（如工具的耐久 damage）"""


class LkUserDetail(LkUserInfo):
    bindings: list[LkBindingInfo] = []
    """这个账号绑定了哪些小号（仅在它本身是主账号时有内容）"""
    backpack: list[LkBackpackItem] = []
    """背包内容"""


class SetBackpackPayload(BaseModel):
    user_id: str
    """要改的账号（小号会先解析到主账号）"""
    name: str
    """物品名。未注册的物品也能写进去，但会在游戏里表现为「未知物品」"""
    num: int
    """要设置的数量。0 表示直接移除这一格"""


class BindPayload(BaseModel):
    account_id: str
    """要绑定的账号"""
    master_id: str
    """目标主账号"""


class UnbindPayload(BaseModel):
    account_id: str
    """要解绑的账号"""


class UpdateUserPayload(BaseModel):
    user_id: str
    """要改的账号（小号会先解析到主账号）"""
    name: str | None = None
    """新名称；不传则不改"""
    money: int | None = None
    """ATRI 币（绝对值，不是增量）；不传则不改"""
    love: int | None = None
    """好感度（绝对值）；不传则不改"""
    exp: int | None = None
    """总经验（绝对值）。等级与升级差额由它派生，会自动重算；不传则不改"""


def _to_info(user_id: str, name: str) -> LkUserInfo:
    data = users.load_user_data(user_id)
    return LkUserInfo(
        id=str(user_id),
        name=str(name),
        level=int(data.lvl),
        exp=int(data.exp),
        left_exp=int(data.left_exp),
        money=int(data.money),
        love=int(data.love),
        signdays=int(data.signdays),
        lastsign=str(data.lastsign),
        is_master=BaseFunc.is_master(user_id),
        bound_to=(
            users.resolve_user_id(user_id) if str(user_id) in users._bindings else ""
        ),
    )


def _backpack_of(user_id: str) -> list[LkBackpackItem]:
    """读这个账号的背包。"""
    data = users.load_user_data(user_id)
    out = []
    for stack in data.backpack.get_item_list():
        meta = stack.meta.meta_to_dict()
        num = int(meta.pop("num", 0) or 0)
        out.append(
            LkBackpackItem(
                name=str(stack.get_name()),
                type=str(stack.get_type().value),
                num=num,
                extra={k: v for k, v in meta.items() if k != "num"},
            )
        )
    return sorted(out, key=lambda i: (i.type, i.name))


def _bindings_of(user_id: str) -> list[LkBindingInfo]:
    """列出绑定到这个主账号的小号。"""
    out = []
    for account_id, master_id in users._bindings.items():
        if str(master_id) != str(user_id):
            continue
        out.append(
            LkBindingInfo(
                account_id=str(account_id),
                master_id=str(master_id),
                master_name=users._ids.get(str(master_id), ""),
            )
        )
    return sorted(out, key=lambda b: b.account_id)


@router.get(
    "/get_users",
    dependencies=[authentication()],
    response_model=Result[list[LkUserInfo]],
    response_class=JSONResponse,
    description="获取 lkbot 用户列表",
)
async def _(limit: int = MAX_LIST_USERS) -> Result[list[LkUserInfo]]:
    try:
        limit = max(1, min(int(limit), MAX_LIST_USERS))
        items = []
        user_entries = list(users._ids.items())
        registered_ids = set(users._ids)
        user_entries.extend(
            (account_id, "已绑定账号")
            for account_id in users._bindings
            if account_id not in registered_ids
        )
        for user_id, name in user_entries[:limit]:
            try:
                items.append(_to_info(user_id, name))
            except Exception as exc:
                log.warning(f"读取 lkbot 用户 {user_id} 失败：{exc}")
                continue
        return Result.ok(items, "拿到信息啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_users 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.get(
    "/get_user",
    dependencies=[authentication()],
    response_model=Result[LkUserDetail],
    response_class=JSONResponse,
    description="获取单个 lkbot 用户详情（含账号绑定）",
)
async def _(user_id: str) -> Result[LkUserDetail]:
    try:
        resolved = users.resolve_user_id(user_id)
        if resolved not in users._ids:
            return Result.fail(f"用户 {user_id} 不存在", 404)
        name = users._ids[resolved]
        base = _to_info(resolved, name)
        bound_to = str(users._bindings.get(str(user_id), "") or "")
        detail = LkUserDetail(
            **base.model_dump(exclude={"bound_to"}),
            bindings=_bindings_of(resolved),
            bound_to=bound_to,
            backpack=_backpack_of(resolved),
        )
        return Result.ok(detail, "拿到信息啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_user 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/update_user",
    dependencies=[authentication()],
    response_model=Result[LkUserDetail],
    response_class=JSONResponse,
    description="修改 lkbot 用户的名称 / ATRI 币 / 好感度 / 经验（签到天数不可改）",
)
async def _(payload: UpdateUserPayload) -> Result[LkUserDetail]:
    try:
        resolved = users.resolve_user_id(str(payload.user_id or "").strip())
        if resolved not in users._ids:
            return Result.fail(f"用户 {payload.user_id} 不存在", 404)
        for label, value in (
            ("ATRI 币", payload.money),
            ("经验", payload.exp),
        ):
            if value is not None and int(value) < 0:
                return Result.fail(f"{label}不能为负", 400)
        new_name = ""
        if payload.name is not None:
            new_name = str(payload.name).strip()
            if not new_name:
                return Result.fail("名称不能为空", 400)
        if new_name:
            old_name = users._ids.get(resolved, "")
            if new_name != old_name and not users.change_name(
                resolved, old_name, new_name
            ):
                return Result.fail(f"名称「{new_name}」已被占用", 400)
        with users.get_user_data(resolved) as data:
            if payload.money is not None:
                data.money = int(payload.money)
            if payload.love is not None:
                data.love = int(payload.love)
            if payload.exp is not None:
                data.exp = int(payload.exp)
                data.lvl = user_level_manager.to_lvl(data.exp)
                data.left_exp = user_level_manager.get_left_exp(data.exp, data.lvl)
        return await _detail_of(resolved)
    except ValueError as e:
        return Result.fail(str(e), 400)
    except Exception as e:
        log.error(f"{router.prefix}/update_user 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


async def _detail_of(resolved: str) -> Result[LkUserDetail]:
    name = users._ids[resolved]
    base = _to_info(resolved, name)
    return Result.ok(
        LkUserDetail(
            **base.model_dump(exclude={"bound_to"}),
            bindings=_bindings_of(resolved),
            bound_to=str(users._bindings.get(str(resolved), "") or ""),
            backpack=_backpack_of(resolved),
        ),
        "已保存!",
    )


@router.post(
    "/set_backpack_item",
    dependencies=[authentication()],
    response_model=Result[LkUserDetail],
    response_class=JSONResponse,
    description="设置 lkbot 用户背包里某个物品的数量（0 表示移除）",
)
async def _(payload: SetBackpackPayload) -> Result[LkUserDetail]:
    try:
        resolved = users.resolve_user_id(str(payload.user_id or "").strip())
        if resolved not in users._ids:
            return Result.fail(f"用户 {payload.user_id} 不存在", 404)
        item_name = str(payload.name or "").strip()
        if not item_name:
            return Result.fail("物品名不能为空", 400)
        num = int(payload.num)
        if num < 0:
            return Result.fail("数量不能为负（要清空就填 0）", 400)
        with users.get_user_data(resolved) as data:
            data.backpack.set_item(item_name, num)
        return await _detail_of(resolved)
    except ValueError as e:
        return Result.fail(str(e), 400)
    except Exception as e:
        log.error(f"{router.prefix}/set_backpack_item 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


class LkItemCatalogEntry(BaseModel):
    name: str
    """物品名"""
    type: str
    """物品种类（ItemType.value），如「工具」「种子」"""


class LkItemCatalog(BaseModel):
    items: list[LkItemCatalogEntry] = []
    """全部已注册物品"""
    types: list[str] = []
    """出现过的种类，按 ItemType 的定义顺序，供前端做分类筛选"""


@router.get(
    "/get_item_catalog",
    dependencies=[authentication()],
    response_model=Result[LkItemCatalog],
    response_class=JSONResponse,
    description="获取已注册物品的完整目录（含分类），供添加物品时筛选",
)
async def _() -> Result[LkItemCatalog]:
    try:
        from ATRI.system.lkbot.data.item import ItemType, items

        entries: list[LkItemCatalogEntry] = []
        types: list[str] = []
        for type_ in ItemType:
            names = [n for n in items.get_item_list_by_type(type_) if n]
            if not names:
                continue
            types.append(type_.value)
            entries.extend(
                LkItemCatalogEntry(name=n, type=type_.value) for n in sorted(names)
            )
        return Result.ok(LkItemCatalog(items=entries, types=types), "拿到信息啦!")
    except Exception as e:
        log.error(f"{router.prefix}/get_item_catalog 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/bind_account",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="把一个账号绑定到指定主账号",
)
async def _(payload: BindPayload) -> Result:
    try:
        account_id = str(payload.account_id or "").strip()
        master_id = str(payload.master_id or "").strip()
        if not account_id or not master_id:
            return Result.fail("账号和主账号都要填", 400)
        if account_id == master_id:
            return Result.fail("不能绑定到自己", 400)
        if master_id not in users._ids:
            return Result.fail(f"主账号 {master_id} 不存在", 404)
        msg = users.force_bind_account(account_id, master_id)
        if "成功" not in msg:
            return Result.fail(msg, 400)
        return Result.ok(info=msg)
    except ValueError as e:
        return Result.fail(f"绑定关系会形成循环：{e}", 400)
    except Exception as e:
        log.error(f"{router.prefix}/bind_account 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")


@router.post(
    "/unbind_account",
    dependencies=[authentication()],
    response_model=Result,
    response_class=JSONResponse,
    description="解除一个账号的绑定",
)
async def _(payload: UnbindPayload) -> Result:
    try:
        account_id = str(payload.account_id or "").strip()
        if not account_id:
            return Result.fail("要填账号", 400)
        msg = users.force_unbind_account(account_id)
        return Result.ok(info=msg)
    except Exception as e:
        log.error(f"{router.prefix}/unbind_account 调用错误:{str_traceback(e)}")
        return Result.fail(f"发生了一点错误捏 {type(e)}: {e}")
