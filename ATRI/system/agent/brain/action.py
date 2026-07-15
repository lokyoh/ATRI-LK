from ATRI.exceptions import str_traceback
from ATRI.log import log

from ..agent.function_calling import FunctionCallingData, FunctionCallingManager


class ActionModel:
    @staticmethod
    async def do_action(functions_data: list, user_id, group_id) -> tuple[bool, list]:
        continue_chat = False
        calling_back = []
        for func in functions_data:
            if not isinstance(func, dict):
                continue
            func_name = func.get("function", None)
            if not func_name:
                continue
            if func_name not in FunctionCallingManager.calling:
                log.debug(f"未知的调用功能:{func_name}，已跳过")
                continue
            data = func.get("data", {})
            try:
                func_name: str
                # 使用 FunctionCallingManager 统一处理功能调用
                calling_data = FunctionCallingData(user_id, group_id, data)
                log.debug(f"调用功能 {func_name}")
                result = await FunctionCallingManager.call(func_name, calling_data)
                if result is not None:
                    if FunctionCallingManager.is_continue_calling(func_name):
                        calling_back.append(
                            f"你调用了{func_name}并得到响应：\n{result}"
                        )
                        continue_chat = True
                    else:
                        calling_back.append(f"你调用了{func_name}并得到响应:\n{result}")
                else:
                    calling_back.append(f"你调用了{func_name}")
            except Exception as e:
                log.warning(
                    f"用户 {user_id} 功能调用失败 {func_name}:\n{str_traceback(e)}"
                )
        return continue_chat, calling_back
