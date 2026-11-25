from enum import Enum

from pydantic import BaseModel


class UserSex(Enum):
    """性别

    UserSex，性别
    """

    FEMALE = "female"
    MALE = "male"
    UNKNOWN = "unknown"


class User(BaseModel):
    """User"""

    """昵称"""
    nickname: str
    """用户ID"""
    user_id: int
    """年龄"""
    age: int | None = None
    """生日_日"""
    birthday_day: int | None = None
    """生日_月"""
    birthday_month: int | None = None
    """生日_年"""
    birthday_year: int | None = None
    """分组ID"""
    user_category_id: int | None = None
    """分组ID"""
    category_id: int | None = None
    """分组名称"""
    category_name: str | None = None
    """邮箱"""
    email: str | None = None
    """等级"""
    level: int | None = None
    """登录天数"""
    login_days: int | None = None
    """电话号码"""
    phone_num: str | None = None
    """QID"""
    qid: str | None = None
    """备注"""
    remark: str | None = None
    """性别"""
    sex: str | None = None


class Group(BaseModel):
    """Group"""

    """群全员禁言"""
    group_all_shut: int
    """群号"""
    group_id: int
    """群名"""
    group_name: str
    """群备注"""
    group_remark: str
    """最大成员数量"""
    max_member_count: int
    """成员数量"""
    member_count: int


class PanelItem(BaseModel):
    name: str
    desc: str
    type: str | None = None
    only_admin: bool = False
    link: str | None = None


class Panel(BaseModel):
    items: list[PanelItem]
    remark: str | None = None
    version: int | None = None


class NewPanel(BaseModel):
    scope: str
    target_type: str | None = None
    user_openids: list[str] | None = None
    group_openids: list[str] | None = None
    panel: Panel
