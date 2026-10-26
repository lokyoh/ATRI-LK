from __future__ import annotations

import contextlib
import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from ATRI.dir import PLUGIN_CONFIG_DIR

plugin_config = {}
"""插件设置数据"""


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
        self._apply_defaults(self._data, type(self)._fields)
        self.load()
        plugin_config[service] = self

    @classmethod
    def get_config(cls, service: str) -> PluginConfig | None:
        return plugin_config.get(service)

    def _apply_defaults(self, target: dict, fields: dict[str, Field]):
        for name, field in fields.items():
            if field.children:
                target[name] = {}
                self._apply_defaults(target[name], field.children)
            else:
                target[name] = copy.deepcopy(field.default)

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
            self._merge(self._data, saved)
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
        """扁平点号路径 → 值"""
        for key, value in updates.items():
            self.set(key, value, auto_save=False)
        if self.auto_save:
            self.save()
        return self

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
            if field.children:
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
        if isinstance(field.item_type, Field):
            node["itemField"] = cls._describe_item_field(field.item_type)
        elif field.item_type is not None:
            node["itemType"] = field.item_type.__name__
        if field.children:
            node["children"] = cls._describe_fields(
                field.children, field.default if isinstance(field.default, dict) else {}
            )
        return node
