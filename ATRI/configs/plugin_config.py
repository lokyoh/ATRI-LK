from __future__ import annotations

import contextlib
import copy
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from ATRI.dir import PLUGIN_CONFIG_DIR

plugin_config = {}
"""插件设置数据"""


class ConfigValidationError(ValueError):
    """配置校验失败。``errors`` 保留逐条原因，供 WebAPI 原样返回。"""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("；".join(self.errors))


class Field:
    def __init__(
        self,
        type_: type,
        default: Any = None,
        label: str = "",
        description: str = "",
        ui_type: str | None = None,  # 覆盖自动推断
        options: list[dict] | None = None,
        min: float | None = None,
        max: float | None = None,
        editable: bool = True,
        required: bool = False,
        children: dict[str, Field] | None = None,
        item_type: type | Field | None = None,
        item_field=None,
        key_field: Field | None = None,
        options_provider=None,
        # ---- 分类配置集合（ui_type="category-collection"）----
        category_label: str = "",
        fixed_keys: list[str] | None = None,
        allow_add: bool | None = None,
        allow_remove: bool | None = None,
        key_label: str = "分类键",
        max_items: int | None = None,
        validator: Callable[[Any], str | None] | None = None,
        validation_hint: str = "",
    ):
        self.type_ = type_
        self.default = default
        self.label = label
        self.description = description
        self.ui_type = ui_type
        self.options = options
        #: 动态选项回调：``() -> list[dict]``，每次导出 schema 时调用。
        self.options_provider = options_provider
        self.min = min
        self.max = max
        self.editable = editable
        self.required = required
        self.children = children or {}
        self.item_type = item_type
        self.item_field = item_field
        self.key_field = key_field
        self.category_label = category_label
        self.fixed_keys = list(fixed_keys) if fixed_keys is not None else None
        self.allow_add = (self.fixed_keys is None) if allow_add is None else allow_add
        self.allow_remove = (
            (self.fixed_keys is None) if allow_remove is None else allow_remove
        )
        self.key_label = key_label
        self.max_items = max_items
        self.validator = validator
        self.validation_hint = validation_hint

        if ui_type == "category-collection":
            if type_ is not dict:
                raise ValueError("category-collection 的 type_ 必须是 dict")
            if not isinstance(item_field, Field) or not item_field.children:
                raise ValueError(
                    "category-collection 的 item_field 必须是带 children 的 Field"
                )
            if self.fixed_keys is not None and self.allow_add:
                raise ValueError("fixed_keys 与 allow_add=True 互斥")
            if self.fixed_keys is not None and self.allow_remove:
                raise ValueError("fixed_keys 与 allow_remove=True 互斥")
            if category_label and category_label not in item_field.children:
                raise ValueError(
                    f"category_label='{category_label}' 不在 item_field.children 中"
                )

    def infer_ui_type(self) -> str:
        if self.ui_type:
            return self.ui_type
        if self.children:
            return "group"
        if (
            self.type_ is dict
            and isinstance(self.key_field, Field)
            and isinstance(self.item_type, Field)
        ):
            return "map-list"
        if self.item_field:
            return "object-list"
        if self.item_type is not None:
            return "list"
        t = self.type_
        if t is bool:
            return "boolean"
        if t is int or t is float:
            return "number"
        if t is str:
            return "string"
        if t is list:
            return "list"
        if t is dict:
            return "json"
        return "string"


class PluginConfig:
    _store_dir: ClassVar[Path] = PLUGIN_CONFIG_DIR
    _schema_version: ClassVar[str] = "1.0"

    @classmethod
    def configure(
        cls, store_dir: Path = PLUGIN_CONFIG_DIR, schema_version: str = "1.0"
    ):
        cls._store_dir = store_dir
        cls._store_dir.mkdir(parents=True, exist_ok=True)
        cls._schema_version = schema_version

    def __init_subclass__(cls, **kwargs):
        """子类定义时，收集所有 Field 属性"""
        super().__init_subclass__(**kwargs)
        cls._fields: dict[str, Field] = {}
        for base in cls.__mro__:
            for name, value in vars(base).items():
                if isinstance(value, Field):
                    cls._fields[name] = value

    def __init__(self, service: str, auto_save: bool = True):
        self.service = service
        self.path = self._store_dir / f"{service}_config.json"
        self.auto_save = auto_save
        self._data: dict[str, Any] = {}
        #: 最近一次 ``update()`` 自动补齐的固定分类路径，供 WebAPI 回显。
        self.last_filled_keys: list[str] = []
        self._apply_defaults(self._data, type(self)._fields)
        self.load()
        plugin_config[service] = self

    @classmethod
    def get_config(cls, service: str) -> PluginConfig | None:
        return plugin_config.get(service)

    @staticmethod
    def _is_category(field: Field) -> bool:
        return field.infer_ui_type() == "category-collection"

    @staticmethod
    def _category_children(field: Field) -> dict[str, Field]:
        item = field.item_field
        return item.children if isinstance(item, Field) else {}

    def _apply_defaults(self, target: dict, fields: dict[str, Field]):
        for name, field in fields.items():
            target[name] = self._normalize_value(
                field, copy.deepcopy(field.default), name
            )

    def _normalize_value(self, field: Field, value: Any, path: str) -> Any:
        """按字段定义补齐默认值，并保留未知键。

        分类集合是普通嵌套字典，未知键**不静默删除**：加载时保留并告警，
        保存时再由 ``allow_add`` / ``allow_remove`` 决定接受还是拒绝。
        """
        if self._is_category(field):
            return self._normalize_category(field, value, path=path)
        if field.children:
            node: dict[str, Any] = {}
            self._apply_defaults(node, field.children)
            if isinstance(value, dict):
                self._merge(node, value)
            for key, child in field.children.items():
                node[key] = self._normalize_value(child, node.get(key), f"{path}.{key}")
            return node
        return copy.deepcopy(value)

    def _normalize_category(
        self, field: Field, value: Any, *, path: str, warn: bool = True
    ) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}
        if value is not None and not isinstance(value, dict) and warn:
            from ATRI.log import log

            log.warning(f"[{self.service}] 配置 {path} 应为对象，已重置为默认值")

        normalized: dict[str, Any] = {}
        fixed = field.fixed_keys or []
        for key in fixed:
            normalized[key] = self._normalize_item(field, raw.get(key), f"{path}.{key}")
        for key, item in raw.items():
            key = str(key)
            if key in normalized:
                continue
            if warn and field.fixed_keys is not None and not field.allow_add:
                from ATRI.log import log

                log.warning(
                    f"[{self.service}] 配置 {path} 出现未知分类 {key}"
                    "（不允许新增），已保留；保存时会被拒绝"
                )
            normalized[key] = self._normalize_item(field, item, f"{path}.{key}")
        return normalized

    def _normalize_item(self, field: Field, value: Any, path: str) -> dict[str, Any]:
        children = self._category_children(field)
        raw = value if isinstance(value, dict) else {}
        item: dict[str, Any] = {}
        self._apply_defaults(item, children)
        for key, child in children.items():
            item[key] = self._normalize_value(
                child, raw.get(key, item.get(key)), f"{path}.{key}"
            )
        for key, val in raw.items():
            if key not in item:
                item[key] = copy.deepcopy(val)
        return item

    def _normalize_all(self) -> None:
        for name, field in type(self)._fields.items():
            self._data[name] = self._normalize_value(field, self._data.get(name), name)

    def _migrate_saved(self, saved: dict) -> dict:
        """子类可覆盖：把旧格式的已保存配置迁移成新格式。

        返回值会被重新 merge 进当前数据；与入参不同则立即落盘。
        """
        return saved

    def load(self) -> PluginConfig:
        if not self.path.exists():
            if self.auto_save:
                self.save()
            return self
        try:
            with open(self.path, encoding="utf-8") as f:
                saved = json.load(f)
            if not isinstance(saved, dict):
                raise ValueError("顶层必须是对象")
            original = copy.deepcopy(saved)
            saved = self._migrate_saved(saved)
            if not isinstance(saved, dict):
                raise ValueError("迁移后的配置顶层必须是对象")
            migrated = saved != original
            self._merge(self._data, saved)
            self._normalize_all()
            if migrated and self.auto_save:
                self.save()
            return self
        except Exception as e:
            self._backup_corrupt_file()
            from ATRI.log import log

            log.warning(f"[{self.service}] 配置损坏: {e}，使用默认值")
            return self

    def _backup_corrupt_file(self):
        import shutil
        from datetime import datetime

        from ATRI.log import log

        backup = self.path.with_suffix(f".corrupt.{datetime.now():%Y%m%d_%H%M%S}.json")
        try:
            shutil.copy2(self.path, backup)
            log.info(f"损坏文件已备份: {backup}")
        except OSError:
            pass

    def save(self) -> PluginConfig:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            if self.path.exists():
                with contextlib.suppress(OSError):
                    os.chmod(tmp_path, os.stat(self.path).st_mode)
            self._replace_with_retry(tmp_path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
        return self

    def _replace_with_retry(self, tmp_path, attempts: int = 5):
        import time

        for attempt in range(attempts):
            try:
                os.replace(tmp_path, self.path)
                return
            except PermissionError:
                if attempt == attempts - 1:
                    raise
                time.sleep(0.02 * (attempt + 1))

    def _merge(self, base: dict, incoming: dict):
        for k, v in incoming.items():
            if isinstance(base.get(k), dict) and isinstance(v, dict):
                self._merge(base[k], v)
            else:
                base[k] = v

    def get(self, key: str, default=None):
        node = self._data
        for k in key.split("."):
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                return default
        return node

    def set(self, key: str, value, auto_save: bool | None = None):
        keys = key.split(".")
        node = self._data
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value
        if auto_save if auto_save is not None else self.auto_save:
            self.save()
        return self

    def update(self, updates: dict):
        """扁平点号路径 → 值，整次更新 all-or-nothing。

        支持三种写法：

        - ``{"permission": {...}}``：整块替换（未提交的固定分类会用默认值补齐，
          并通过 ``last_filled_keys`` 回显）；
        - ``{"permission.admin": {...}}``：只替换一个分类；
        - ``{"permission.admin.deny": [...]}``：只改一个字段。

        ``None`` 只用于删除分类（``allow_remove=True`` 时），其他字段不接受
        ``null``。校验失败会恢复更新前的快照，不留下半成品。
        """
        snapshot = copy.deepcopy(self._data)
        self.last_filled_keys = []
        pending_filled: list[str] = []
        try:
            for key, value in updates.items():
                self._apply_update(str(key), value, pending_filled)
            self._normalize_all()
            self.last_filled_keys = pending_filled
            errors = self.validate()
            if errors:
                raise ConfigValidationError(errors)
        except Exception:
            self._data = snapshot
            self.last_filled_keys = []
            raise
        if self.auto_save:
            self.save()
        return self

    def _apply_update(self, path: str, value: Any, pending_filled: list[str]) -> None:
        parts = [part for part in path.split(".") if part]
        if not parts:
            raise ValueError("配置路径不能为空")
        self._apply_at(
            self._data, type(self)._fields, parts, value, path, pending_filled
        )

    def _apply_at(
        self,
        node: dict,
        fields: dict[str, Field],
        parts: list[str],
        value: Any,
        full_path: str,
        pending_filled: list[str],
    ) -> None:
        part = parts[0]
        field = fields.get(part)
        if field is None:
            raise ValueError(f"未知的配置字段: {full_path}")

        if len(parts) == 1:
            if self._is_category(field):
                if value is None:
                    raise ValueError(f"分类集合不允许整体清空: {full_path}")
                if not isinstance(value, dict):
                    raise ValueError(f"配置字段必须是对象: {full_path}")
                self._check_category_keys(field, value, full_path)
                for key in field.fixed_keys or []:
                    if key not in value:
                        pending_filled.append(f"{full_path}.{key}")
                node[part] = self._normalize_category(
                    field, value, path=full_path, warn=False
                )
            else:
                if value is None:
                    raise ValueError(f"配置字段不能为 null: {full_path}")
                node[part] = copy.deepcopy(value)
            return

        if self._is_category(field):
            children = self._category_children(field)
            current = node.get(part)
            if not isinstance(current, dict):
                current = {}
                node[part] = current
            category_key = parts[1]
            known_key = field.fixed_keys is None or category_key in field.fixed_keys
            if not known_key and not field.allow_add:
                raise ValueError(f"不允许新增分类: {full_path}")

            if len(parts) == 2:
                if value is None:
                    if not field.allow_remove:
                        raise ValueError(f"不允许删除分类: {full_path}")
                    current.pop(category_key, None)
                    return
                if not isinstance(value, dict):
                    raise ValueError(f"配置字段必须是对象: {full_path}")
                if (
                    field.max_items is not None
                    and category_key not in current
                    and len(current) >= field.max_items
                ):
                    raise ValueError(f"分类数量超过上限 {field.max_items}: {full_path}")
                current[category_key] = self._normalize_item(field, value, full_path)
                return

            if (
                field.max_items is not None
                and category_key not in current
                and len(current) >= field.max_items
            ):
                raise ValueError(f"分类数量超过上限 {field.max_items}: {full_path}")
            item = current.get(category_key)
            if not isinstance(item, dict):
                item = self._normalize_item(field, None, full_path)
                current[category_key] = item
            self._apply_at(item, children, parts[2:], value, full_path, pending_filled)
            return

        if field.children:
            child_node = node.get(part)
            if not isinstance(child_node, dict):
                child_node = {}
                node[part] = child_node
            self._apply_at(
                child_node, field.children, parts[1:], value, full_path, pending_filled
            )
            return
        raise ValueError(f"未知的配置字段: {full_path}")

    def _check_category_keys(self, field: Field, value: dict, path: str) -> None:
        if field.max_items is not None and len(value) > field.max_items:
            raise ValueError(f"分类数量超过上限 {field.max_items}: {path}")
        if field.fixed_keys is not None and not field.allow_add:
            unknown = [key for key in value if key not in field.fixed_keys]
            if unknown:
                raise ValueError(f"不允许新增分类: {path}.{unknown[0]}")

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """返回错误列表；空列表表示通过。"""
        errors: list[str] = []
        self._validate_fields(self._data, type(self)._fields, "", errors)
        return errors

    def _validate_fields(
        self, data: dict, fields: dict[str, Field], prefix: str, errors: list[str]
    ) -> None:
        for name, field in fields.items():
            path = f"{prefix}{name}"
            if name not in data:
                if field.required:
                    errors.append(f"{path}: 必填")
                continue
            self._validate_value(field, data[name], path, errors)

    def _validate_value(
        self, field: Field, value: Any, path: str, errors: list[str]
    ) -> None:
        if self._is_category(field):
            self._validate_category(field, value, path, errors)
            return
        if field.children:
            if not isinstance(value, dict):
                errors.append(f"{path}: 类型应为对象")
                return
            self._validate_fields(value, field.children, f"{path}.", errors)
            return
        if field.required and value is None:
            errors.append(f"{path}: 必填")
        if not self._type_ok(field, value):
            errors.append(
                f"{path}: 类型应为 {getattr(field.type_, '__name__', field.type_)}"
            )
            return
        if (
            field.item_type is not None
            and not isinstance(field.item_type, Field)
            and isinstance(value, list)
            and not all(isinstance(item, field.item_type) for item in value)
        ):
            errors.append(f"{path}: 列表项类型不正确")
        if isinstance(value, int | float) and not isinstance(value, bool):
            if field.min is not None and value < field.min:
                errors.append(f"{path}: 不能小于 {field.min}")
            if field.max is not None and value > field.max:
                errors.append(f"{path}: 不能大于 {field.max}")
        self._run_validator(field, value, path, errors)

    def _validate_category(
        self, field: Field, value: Any, path: str, errors: list[str]
    ) -> None:
        if not isinstance(value, dict):
            errors.append(f"{path}: 类型应为对象")
            return
        fixed = field.fixed_keys or []
        for key in fixed:
            if key not in value:
                errors.append(f"{path}.{key}: 缺少固定分类")
        if field.max_items is not None and len(value) > field.max_items:
            errors.append(f"{path}: 分类数量超过上限 {field.max_items}")
        children = self._category_children(field)
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if (
                field.fixed_keys is not None
                and not field.allow_add
                and key not in fixed
            ):
                errors.append(f"{child_path}: 不允许新增分类")
                continue
            if not isinstance(item, dict):
                errors.append(f"{child_path}: 类型应为对象")
                continue
            self._validate_fields(item, children, f"{child_path}.", errors)
        self._run_validator(field, value, path, errors)

    @staticmethod
    def _type_ok(field: Field, value: Any) -> bool:
        expected = field.type_
        if expected is bool:
            return isinstance(value, bool)
        if expected is int:
            return isinstance(value, int) and not isinstance(value, bool)
        if expected is float:
            return isinstance(value, int | float) and not isinstance(value, bool)
        return isinstance(value, expected)

    @staticmethod
    def _run_validator(field: Field, value: Any, path: str, errors: list[str]) -> None:
        if field.validator is None:
            return
        try:
            message = field.validator(value)
        except Exception as exc:
            message = str(exc) or type(exc).__name__
        if message:
            errors.append(f"{path}: {message}")

    def to_dict(self):
        return json.loads(json.dumps(self._data))

    def describe(self) -> dict:
        """导出 schema + value 合一的描述"""
        return {
            "schemaVersion": self._schema_version,
            "type": type(self).__name__,
            "service": self.service,
            "fields": self._describe_fields(type(self)._fields, self._data),
        }

    @classmethod
    def _describe_fields(cls, fields: dict[str, Field], data: dict) -> list:
        result = []
        for name, field in fields.items():
            value = data.get(name)
            node = {
                "key": name,
                "label": field.label or name,
                "description": field.description,
                "type": field.infer_ui_type(),
                "value": value,
                "editable": field.editable,
                "required": field.required,
            }
            if field.options:
                node["options"] = field.options
            elif field.options_provider is not None:
                try:
                    dynamic = field.options_provider()
                except Exception as exc:
                    from ATRI.log import log

                    log.warning(f"字段 {name} 的动态选项获取失败：{exc}")
                    dynamic = None
                if dynamic:
                    node["options"] = dynamic
            if field.min is not None:
                node["min"] = field.min
            if field.max is not None:
                node["max"] = field.max
            if isinstance(field.item_type, Field):
                node["itemField"] = cls._describe_item_field(field.item_type)
            elif field.item_type is not None:
                node["itemType"] = field.item_type.__name__
            if field.key_field is not None:
                node["keyField"] = cls._describe_item_field(field.key_field)
            if field.infer_ui_type() == "category-collection":
                node["categoryLabel"] = field.category_label
                if field.fixed_keys:
                    node["fixedKeys"] = list(field.fixed_keys)
                node["allowAdd"] = field.allow_add
                node["allowRemove"] = field.allow_remove
                node["keyLabel"] = field.key_label
                if field.max_items is not None:
                    node["maxItems"] = field.max_items
                if field.validation_hint:
                    node["validationHint"] = field.validation_hint
                if isinstance(field.item_field, Field):
                    node["itemField"] = cls._describe_item_field(field.item_field)
            elif field.children:
                node["children"] = cls._describe_fields(field.children, value or {})
            result.append(node)
        return result

    @classmethod
    def _describe_item_field(cls, field: Field) -> dict:
        node = {
            "type": field.infer_ui_type(),
            "default": field.default,
            "label": field.label,
            "description": field.description,
            "editable": field.editable,
            "required": field.required,
        }
        if field.options:
            node["options"] = field.options
        if field.min is not None:
            node["min"] = field.min
        if field.max is not None:
            node["max"] = field.max
        if field.validation_hint:
            node["validationHint"] = field.validation_hint
        if isinstance(field.item_type, Field):
            node["itemField"] = cls._describe_item_field(field.item_type)
        elif field.item_type is not None:
            node["itemType"] = field.item_type.__name__
        if field.children:
            node["children"] = cls._describe_fields(
                field.children, field.default if isinstance(field.default, dict) else {}
            )
        return node
