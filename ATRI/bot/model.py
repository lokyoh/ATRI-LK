from enum import Enum
from typing import Optional

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
    age: Optional[int] = None
    """生日_日"""
    birthday_day: Optional[int] = None
    """生日_月"""
    birthday_month: Optional[int] = None
    """生日_年"""
    birthday_year: Optional[int] = None
    """分组ID"""
    user_category_id: Optional[int] = None
    """分组ID"""
    category_id: Optional[int] = None
    """分组名称"""
    category_name: Optional[str] = None
    """邮箱"""
    email: Optional[str] = None
    """等级"""
    level: Optional[int] = None
    """登录天数"""
    login_days: Optional[int] = None
    """电话号码"""
    phone_num: Optional[str] = None
    """QID"""
    qid: Optional[str] = None
    """备注"""
    remark: Optional[str] = None
    """性别"""
    sex: Optional[str] = None


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
