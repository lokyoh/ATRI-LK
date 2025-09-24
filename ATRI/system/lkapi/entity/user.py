from ATRI.system.lkbot.data.user import users as user_manager, UserData, BackPack, UserNameChangedEvent

user_name_changed_events = user_manager.user_name_changed_events


def get_user_data(user_id: str | int) -> UserData:
    return user_manager.get_user_data(user_id)


def sign(user: str | UserData) -> tuple[bool, list]:
    if type(user) is UserData:
        return user_manager.sign_func(user)
    return user_manager.sign(str(user))
