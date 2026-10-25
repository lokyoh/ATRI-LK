from ATRI.log import log

from ...config import config
from .model import FunctionPlugin, validate_plugin_name


class FunctionPluginManager:
    """功能插件注册表（模块级单例 `function_plugin_manager`）。"""

    def __init__(self):
        self._plugins: dict[str, FunctionPlugin] = {}
        self._sources: dict[str, FunctionPlugin] = {}

    def register(self, plugin: FunctionPlugin) -> FunctionPlugin:
        """注册插件，并替它登记自带的技能正文与功能类。

        任何一步失败都会回滚本次写入，不留下"插件已登记但成员缺失"的半成品。
        返回归一化后的插件（成员只剩名字）。
        """
        error = validate_plugin_name(plugin.name)
        if error:
            raise ValueError(error)
        bundled_functions = plugin.bundled_functions
        bundled_skills = plugin.bundled_skill_objects
        common = {self._name_of(f) for f in plugin.functions}
        rare = {self._name_of(f) for f in plugin.rare_functions}
        overlap = common & rare
        if overlap:
            raise ValueError(
                f"功能 {('、'.join(sorted(overlap)))} 不能同时声明为常用与不常用"
            )
        self._reject_claimed_members(plugin, common | rare)
        from ..function_calling import FunctionCallingManager
        from ..skills import skill_manager

        written_skills: list[str] = []
        registered_functions: list[str] = []
        try:
            for item in bundled_skills:
                skill_manager.put_virtual(item.name, item.description, item.content)
                written_skills.append(item.name)
            for cls in bundled_functions:
                FunctionCallingManager.register(cls)
                registered_functions.append(self._name_of(cls))
            stored = plugin.normalized()
            stored.enabled = self.is_manually_enabled(plugin.name)
            self._plugins[plugin.name] = stored
            self._sources[plugin.name] = plugin.model_copy(deep=True)
        except Exception:
            for func_name in registered_functions:
                FunctionCallingManager.function_calling.pop(func_name, None)
            for skill_name in written_skills:
                skill_manager.drop_virtual(skill_name)
            raise

        log.debug(
            f"功能插件 {plugin.name} 注册完成："
            f"{len(stored.skills)} 个技能，"
            f"{len(stored.functions)} 个功能，"
            f"{len(stored.rare_functions)} 个不常用功能"
        )
        return stored

    def register_many(self, plugins: list[FunctionPlugin]):
        for item in plugins:
            self.register(item)

    def clear_all(self):
        """清空插件登记表；成员本身（功能类 / 技能文件）不动。

        需要连成员一起收走时用 `unload_all()`。
        """
        self._plugins.clear()
        self._sources.clear()

    def unregister(self, name: str) -> bool:
        """只摘掉插件声明，不撤回成员。

        需要"完全撤回"（连技能正文与功能类一起清掉）时用 `unload()`。
        """
        self._sources.pop(name, None)
        return self._plugins.pop(name, None) is not None

    def reload(self, name: str) -> FunctionPlugin | None:
        """重载：先撤回旧成员，再用插件自己保存的原始声明重新注册。

        返回重载后的插件；插件未注册时返回 None。
        """
        if name not in self._plugins:
            return None
        stored = self._plugins[name]
        source = self._sources.get(name) or stored
        self._plugins.pop(name, None)
        try:
            return self.register(source)
        except Exception:
            self._plugins[name] = stored
            raise

    def reload_all(self) -> int:
        """按原声明重新登记所有插件，返回成功重载的数量。

        供 `agent.reload_all()` 使用：`register_function_calling()` 的 `clear_all()`
        会清掉插件登记的功能类（技能正文在内存里也随内存对象一起失效），
        这里按各插件保存的原始声明把它们补回来。单个插件失败不影响其它插件。
        """
        count = 0
        for name in list(self._sources):
            try:
                if self.reload(name) is not None:
                    count += 1
            except Exception as e:
                log.warning(f"重载功能插件 {name} 失败:{e}")
        return count

    def unload(self, name: str, *, remove_skills: bool = True) -> bool:
        """卸载：完全撤回插件登记过的成员。

        - 插件自带的功能类从 `FunctionCallingManager` 摘除；
        - 插件自带的技能正文从磁盘删除（含其开关记录）；
        - 引用的成员（注册时只写名字的）**不动**，它们本来就不属于这个插件。

        `remove_skills=False` 时只撤功能类、保留技能文件（供用户继续使用）。
        返回插件是否存在并被卸载。
        """
        stored = self._plugins.pop(name, None)
        self._sources.pop(name, None)
        if stored is None:
            return False
        self._withdraw_members(stored, remove_skills=remove_skills)
        return True

    def unload_all(self, *, remove_skills: bool = True) -> int:
        """卸载所有插件，返回卸载数量。"""
        names = list(self._plugins)
        for name in names:
            self.unload(name, remove_skills=remove_skills)
        return len(names)

    def _withdraw_members(self, plugin: FunctionPlugin, *, remove_skills: bool):
        """撤回插件自带的成员；引用来的成员一律保留。

        插件自带的技能正文只存在内存里（不落盘），摘除即完全消失，
        不会在 `data/plugins/agent/skills/` 留下任何文件。
        """
        from ..function_calling import FunctionCallingManager
        from ..skills import skill_manager

        for func_name in plugin.bundled_function_names:
            FunctionCallingManager.function_calling.pop(func_name, None)

        if not remove_skills:
            return
        for skill in plugin.bundled_skills:
            skill_manager.drop_virtual(skill.name)

    @staticmethod
    def _name_of(item) -> str:
        if isinstance(item, str):
            return item
        return str(getattr(item, "function_name", "") or type(item).__name__)

    def _reject_claimed_members(self, plugin: FunctionPlugin, func_names: set[str]):
        """成员已属于其它插件时拒绝注册（同插件重复注册视为覆盖）。"""
        for other in self._plugins.values():
            if other.name == plugin.name:
                continue
            taken = func_names & set(other.all_functions)
            if taken:
                raise ValueError(
                    f"功能 {('、'.join(sorted(taken)))} 已属于功能插件 {other.name}"
                )
            skill_taken = set(plugin.skill_names) & set(other.skill_names)
            if skill_taken:
                raise ValueError(
                    f"技能 {('、'.join(sorted(skill_taken)))} 已属于功能插件 {other.name}"
                )

    @staticmethod
    def _read_disabled() -> list[str]:
        return list(config.get("function_plugin_disable") or [])

    def is_manually_enabled(self, name: str) -> bool:
        return name not in self._read_disabled()

    def set_manually_enabled(self, name: str, enabled: bool):
        if name not in self._plugins:
            raise ValueError("功能插件不存在")
        names = self._read_disabled()
        if enabled:
            if name in names:
                names.remove(name)
        elif name not in names:
            names.append(name)
        config.set("function_plugin_disable", names)

    def get(self, name: str) -> FunctionPlugin | None:
        """带开关状态的副本（UI 用）。"""
        plugin = self._plugins.get(name)
        if plugin is None:
            return None
        view = plugin.model_copy(deep=True)
        view.enabled = self.is_manually_enabled(name)
        return view

    def list_all(self) -> list[FunctionPlugin]:
        """保持注册顺序的全量视图（UI 用）。"""
        return [self.get(name) for name in self._plugins]

    def names(self) -> list[str]:
        return list(self._plugins)

    def is_enabled(self, name: str | None) -> bool:
        """插件整体是否启用；name 为空（不属于任何插件）时视为启用。"""
        if not name:
            return True
        return self.is_manually_enabled(name)

    def owner_of_function(self, function_name: str) -> str | None:
        for plugin in self._plugins.values():
            if function_name in plugin.all_functions:
                return plugin.name
        return None

    def owner_of_skill(self, skill_name: str) -> str | None:
        for plugin in self._plugins.values():
            if skill_name in plugin.skill_names:
                return plugin.name
        return None

    def is_rare_function(self, function_name: str) -> bool:
        """是否被某个插件声明为不常用功能（这类功能只能通过 call 调用）。"""
        for plugin in self._plugins.values():
            if function_name in plugin.rare_functions:
                return True
        return False

    def functions_of(self, name: str) -> list[str]:
        plugin = self._plugins.get(name)
        return plugin.all_functions if plugin else []

    def skills_of(self, name: str) -> list[str]:
        plugin = self._plugins.get(name)
        return list(plugin.skill_names) if plugin else []

    def check_members(
        self,
        function_names: set[str],
        skill_names: set[str],
    ) -> list[str]:
        """返回声明与实际不符的提示（只写日志，不阻断加载）。"""
        problems: list[str] = []
        seen_functions: dict[str, str] = {}
        seen_skills: dict[str, str] = {}
        for plugin in self._plugins.values():
            for name in plugin.all_functions:
                if name not in function_names:
                    problems.append(f"功能插件 {plugin.name} 声明的功能 {name} 未注册")
                    continue
                if name in seen_functions:
                    problems.append(
                        f"功能 {name} 同时被 {seen_functions[name]} 与 {plugin.name} 声明"
                    )
                    continue
                seen_functions[name] = plugin.name
            for name in plugin.skill_names:
                if name not in skill_names:
                    problems.append(f"功能插件 {plugin.name} 声明的技能 {name} 不存在")
                    continue
                if name in seen_skills:
                    problems.append(
                        f"技能 {name} 同时被 {seen_skills[name]} 与 {plugin.name} 声明"
                    )
                    continue
                seen_skills[name] = plugin.name
        return problems

    def log_member_problems(self, function_names: set[str], skill_names: set[str]):
        for problem in self.check_members(function_names, skill_names):
            log.warning(problem)


function_plugin_manager = FunctionPluginManager()


def get_function_plugin_manager() -> FunctionPluginManager:
    return function_plugin_manager
