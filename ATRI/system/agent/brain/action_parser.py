"""Agent 动作解释器：把模型的文本输出解析成可执行的功能调用。

模型约定：先写思考内容，再用一个代码块（标头 text）列出调用，每行一个
「功能名(参数名=参数值, ...)」。真实模型经常偏离约定，这里一律按
「宁可宽容解析，也不让整轮思考崩掉」处理：

- 按括号配平切出调用，容忍字符串里直接换行、调用跨行书写；
- 合并多个代码块；解析不出调用的块按正文保留，不静默丢弃；
- 兼容模型自发的 DSML 原生工具调用，并把它连同参数值从正文里剥除；
- 裸值（未加引号的字符串）按字符串回落，全角引号视同引号；
- 按功能声明的参数名 / 类型校验，把问题变成可回填给模型的提示。

模块只依赖标准库与 ATRI.log：功能声明以鸭子类型传入（需要 name 与 type
两个属性），因此不导入 function_calling，避免循环依赖。
"""

import ast
import json
import re
from collections.abc import Iterator
from typing import Any

from ATRI.log import log

__all__ = ["ActionParseError", "normalize_args", "parse_calls", "split_response"]


class ActionParseError(ValueError):
    """一处调用无法解析；由调用方转成回填给模型的提示。"""


#: 代码块围栏（三个反引号）
_FENCE = "\x60" * 3
#: 从模型输出里提取代码块（语言标注可有可无）
_CALL_BLOCK_RE = re.compile(
    rf"{_FENCE}(?P<lang>[A-Za-z]*)[ \t]*\n?(?P<body>[\s\S]*?)\n?{_FENCE}"
)
#: 合法语言标注：只有这类首行才当作标注剥掉，避免误吃调用的第一行
_LANG_TAG_RE = re.compile(r"[A-Za-z][\w+-]*")
#: 一次调用的起点：行首（允许列表符号）的「功能名(」
_CALL_START_RE = re.compile(
    r"^[ \t]*(?:[-*+•>]|\d+[.)、])?[ \t]*(?P<name>[A-Za-z_]\w*)[ \t]*\(", re.MULTILINE
)
#: 完整调用片段：功能名(参数…)
_CALL_LINE_RE = re.compile(r"^(?P<name>[A-Za-z_]\w*)[ \t]*\((?P<args>[\s\S]*)\)$")
#: 参数名：key=
_KEYWORD_RE = re.compile(r"(?P<key>[A-Za-z_]\w*)[ \t]*=[ \t]*")
#: 模型自发的 DSML 原生调用：invoke 块（功能名 + 参数体）
_DSML_INVOKE_RE = re.compile(
    r"<[｜|]+DSML[｜|]+\s*invoke\s+name=\"(?P<name>[^\"]+)\"[^>]*>"
    r"(?P<body>[\s\S]*?)"
    r"</[｜|]+DSML[｜|]+\s*invoke\s*>"
)
#: DSML 参数：带 string="true" 时表示该值按字符串原样保留
_DSML_PARAM_RE = re.compile(
    r"<[｜|]+DSML[｜|]+\s*parameter\s+name=\"(?P<name>[^\"]+)\""
    r"(?P<attrs>[^>]*)>(?P<value>[\s\S]*?)"
    r"</[｜|]+DSML[｜|]+\s*parameter\s*>"
)
#: 残留的 DSML 标签（如未配对的 calls 包裹），清理正文时直接剥除
_DSML_TAG_RE = re.compile(r"</?[｜|]+DSML[｜|]+[^>]*>")
#: 引号种类 -> 结束引号
_QUOTES = {'"""': '"""', '"': '"', "'": "'", "“": "”", "‘": "’"}
#: 字符串参数里的转义符还原表
_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "b": "\b",
    "f": "\f",
    '"': '"',
    "'": "'",
    "/": "/",
    "\\": "\\",
}
#: 列表类型声明归一表
_LIST_TYPES = {"list": "list", "array": "list", "tuple": "tuple", "set": "set"}
#: 常见类型别名归一表
_TYPE_ALIASES = {
    "string": "str",
    "text": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "object": "dict",
    "json": "dict",
}
_TRUE_WORDS = {"true", "yes", "on", "1", "是", "真"}
_FALSE_WORDS = {"false", "no", "off", "0", "否", "假"}


def split_response(text: str) -> tuple[str, list[dict[str, Any]], list[str]]:
    """把模型输出拆成「(正文, 调用列表, 待回填的问题)」。

    兼容三种写法：约定的单个代码块、拆成多个的代码块（逐块解析后合并），
    以及模型自发的 DSML 原生调用。解析不出调用的代码块按正文保留，
    避免整段叙述被静默丢掉；单处调用的问题收集进第三个返回值，
    交给调用方回填给模型，而不是让整轮思考崩掉。
    """
    issues: list[str] = []
    calls: list[dict[str, Any]] = []

    raw = text or ""
    dsml_calls, raw = extract_dsml(raw)
    calls.extend(dsml_calls)

    parts: list[str] = []
    last = 0
    for match in _CALL_BLOCK_RE.finditer(raw):
        parts.append(raw[last : match.start()])
        block = match.group("body")
        block_calls = parse_calls(block, issues)
        if block_calls:
            calls.extend(block_calls)
        elif block.strip():
            # 解析不出调用：当正文保留，宁可多一段原文，也不吞掉叙述。
            parts.append(_strip_lang_tag(block, match.group("lang")))
        last = match.end()
    parts.append(raw[last:])

    content = "\n\n".join(part.strip() for part in parts if part.strip())
    return content, calls, issues


def parse_calls(text: str, issues: list[str] | None = None) -> list[dict[str, Any]]:
    """把 DSL 文本解析成 [{"function": ..., "args": {...}, "line": ...}]。

    逐行严格解析（ast）时，模型一旦在字符串参数里直接写换行就会整块报
    unterminated string literal 而被丢弃；这里改成按括号配平切出调用、
    再逐个参数取值，因此跨行写法也能正确解析。既不是调用的行（多余的
    说明文字）直接跳过；单处调用的问题记进 issues（未传入则只写日志）
    后跳过，不影响同块里的其它调用。
    """
    calls: list[dict[str, Any]] = []
    try:
        segments = list(_iter_calls(text or ""))
    except ActionParseError as e:
        _note(str(e), issues)
        return calls
    for line_no, segment in segments:
        try:
            name, args = _parse_call(segment, line_no)
        except ActionParseError as e:
            _note(str(e), issues)
            continue
        calls.append({"function": name, "args": args, "line": line_no})
    return calls


def extract_dsml(text: str) -> tuple[list[dict[str, Any]], str]:
    """解析 DSML 原生工具调用，并把这段 DSL 从正文里剥除。

    部分模型不按约定写代码块，而是输出形如
    <｜｜DSML｜｜ invoke name="功能名">…</｜｜DSML｜｜ invoke> 的原生调用；
    这里转成与 parse_calls 相同的结构，同时把 invoke 块（连同参数值）与
    残留标签从文本里移除，避免原样泄漏成对外可见的内容。
    """
    calls: list[dict[str, Any]] = []
    for match in _DSML_INVOKE_RE.finditer(text or ""):
        func_name = match.group("name").strip()
        if not func_name:
            continue
        args: dict[str, Any] = {}
        for param in _DSML_PARAM_RE.finditer(match.group("body")):
            key = param.group("name").strip()
            if not key:
                continue
            is_string = 'string="true"' in param.group("attrs")
            args[key] = (
                param.group("value").strip()
                if is_string
                else _literal(param.group("value"))
            )
        calls.append({"function": func_name, "args": args, "line": 0})

    cleaned = _DSML_INVOKE_RE.sub("", text or "")
    cleaned = _DSML_TAG_RE.sub("", cleaned)
    if calls:
        log.debug("识别到 DSML 原生工具调用:" + "、".join(c["function"] for c in calls))
    return calls, cleaned


def normalize_args(func: Any, args: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """按功能声明校验并强制类型，返回「(可用参数, 问题列表)」。

    未声明的参数直接剔除（否则要等 call() 抛 TypeError 才暴露，模型只能收到
    「调用失败」）；声明了类型的参数做可逆转换（"3" 转 3、"true" 转 True、
    "[1,2]" 转 [1,2]）。缺参数不在这里报：各功能对缺省参数有自己的默认值。
    """
    declared = {arg.name: arg for arg in getattr(func, "args", ()) or ()}
    if not declared:
        return dict(args), []

    issues: list[str] = []
    clean: dict[str, Any] = {}
    valid = "、".join(declared) or "无"
    for key, value in args.items():
        arg = declared.get(key)
        if arg is None:
            issues.append(f"参数「{key}」不存在，可用参数：{valid}")
            continue
        value, error = _coerce(getattr(arg, "type", "") or "", value)
        if error:
            issues.append(f"参数「{key}」{error}")
            continue
        clean[key] = value
    return clean, issues


def _iter_calls(text: str) -> Iterator[tuple[int, str]]:
    """按括号配平切出「功能名(...)」片段，产出 (起始行号, 片段)。"""
    pos = 0
    total = len(text)
    while pos < total:
        match = _CALL_START_RE.search(text, pos)
        if not match:
            return
        line_no = text.count("\n", 0, match.start()) + 1
        depth = 0
        quote = ""
        idx = match.end() - 1
        while idx < total:
            char = text[idx]
            if quote:
                if char == "\\":
                    idx += 2
                    continue
                if text.startswith(quote, idx):
                    idx += len(quote)
                    quote = ""
                    continue
            elif char in '"' + "'“":
                quote = _QUOTES[char]
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    idx += 1
                    break
            idx += 1
        else:
            raise ActionParseError(f"第 {line_no} 行的调用括号没有闭合")
        # 从功能名起切，丢掉行首的列表符号 / 缩进
        yield line_no, text[match.start("name") : idx]
        pos = idx


def _parse_call(segment: str, line_no: int) -> tuple[str, dict[str, Any]]:
    """解析一段「功能名(参数=值, ...)」，返回 (功能名, 参数字典)。"""
    match = _CALL_LINE_RE.match(segment.strip())
    if not match:
        raise ActionParseError(f"第 {line_no} 行不是完整的功能调用")
    name = match.group("name")
    return name, _parse_args(match.group("args"), line_no, name)


def _parse_args(raw: str, line_no: int, name: str) -> dict[str, Any]:
    """解析调用括号内的关键字参数；值可以是跨行 / 三引号字符串。"""
    args: dict[str, Any] = {}
    pos = 0
    total = len(raw)
    while pos < total:
        while pos < total and (raw[pos].isspace() or raw[pos] == ","):
            pos += 1
        if pos >= total:
            break
        key_match = _KEYWORD_RE.match(raw, pos)
        if not key_match:
            raise ActionParseError(
                f"第 {line_no} 行「{name}」只允许「参数名=参数值」写法"
            )
        key = key_match.group("key")
        value, pos = _read_value(raw, key_match.end(), line_no, key)
        args[key] = value
    return args


def _read_value(raw: str, pos: int, line_no: int, key: str) -> tuple[Any, int]:
    """从 pos 起读一个参数值，返回 (值, 新的位置)。"""
    total = len(raw)
    while pos < total and raw[pos].isspace():
        pos += 1
    if pos >= total:
        raise ActionParseError(f"第 {line_no} 行参数「{key}」缺少值")

    quote = _match_quote(raw, pos)
    if quote:
        closing = _QUOTES[quote]
        idx = pos + len(quote)
        body: list[str] = []
        while idx < total:
            if raw[idx] == "\\" and idx + 1 < total:
                body.append(raw[idx : idx + 2])
                idx += 2
                continue
            if raw.startswith(closing, idx):
                idx += len(closing)
                break
            body.append(raw[idx])
            idx += 1
        else:
            raise ActionParseError(f"第 {line_no} 行参数「{key}」的引号没有闭合")
        return _unescape("".join(body)), idx

    # 裸值（数字 / 布尔 / 列表 / 字典，或没加引号的字符串）
    start = pos
    depth = 0
    while pos < total:
        char = raw[pos]
        if char in "[{(":
            depth += 1
        elif char in "]})":
            if depth == 0:
                break
            depth -= 1
        elif char == "," and depth == 0:
            break
        pos += 1
    return _literal(raw[start:pos]), pos


def _match_quote(raw: str, pos: int) -> str:
    """判断 pos 处是否是引号（三引号优先），返回引号种类；不是引号返回空串。"""
    for quote in ('"""', '"', "'", "“", "‘"):
        if raw.startswith(quote, pos):
            return quote
    return ""


def _strip_lang_tag(block: str, lang: str) -> str:
    """去掉代码块首行的语言标注，只留正文。"""
    lines = block.splitlines()
    if lang and lines and lines[0].strip() == lang:
        return "\n".join(lines[1:])
    if lines and _LANG_TAG_RE.fullmatch(lines[0].strip()):
        return "\n".join(lines[1:])
    return block


def _note(message: str, issues: list[str] | None) -> None:
    """记录一处解析问题：收集给模型，或只在日志里留痕。"""
    log.debug(f"动作解析问题:{message}")
    if issues is not None:
        issues.append(message)


def _unescape(text: str) -> str:
    """还原转义符（\n 转换行），保留原文里的真实换行。"""
    return re.sub(
        r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(0)), text, flags=re.DOTALL
    )


def _literal(text: str) -> Any:
    """裸值还原：能当字面量就转类型，否则按字符串保留。"""
    value = text.strip()
    if not value:
        return ""
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


def _coerce(type_name: str, value: Any) -> tuple[Any, str]:
    """按声明类型转换参数值，返回 (值, 错误信息)；错误信息为空串表示成功。"""
    kind = _type_kind(type_name)
    if kind is None:
        return value, ""

    if kind == "str":
        if isinstance(value, str):
            return value, ""
        if value is None:
            return "", ""
        if isinstance(value, bool):
            return ("true" if value else "false"), ""
        return str(value), ""

    if kind == "int":
        return _to_int(value)

    if kind == "float":
        return _to_float(value)

    if kind == "bool":
        return _to_bool(value)

    if kind in ("list", "tuple", "set"):
        items, error = _to_list(value)
        if error:
            return value, error
        inner = _inner_type(type_name)
        if inner:
            converted: list[Any] = []
            for item in items:
                new_item, item_error = _coerce(inner, item)
                if item_error:
                    return value, item_error
                converted.append(new_item)
            items = converted
        if kind == "tuple":
            return tuple(items), ""
        if kind == "set":
            return set(items), ""
        return items, ""

    if kind == "dict":
        return _to_dict(value)

    return value, ""


def _type_kind(type_name: str) -> str | None:
    """把声明类型名归一成 str/int/float/bool/list/tuple/set/dict；未知类型返回 None。"""
    name = (type_name or "").strip().lower()
    if not name:
        return None
    if name in _LIST_TYPES:
        return _LIST_TYPES[name]
    if name.startswith(("list[", "list[ ")):
        return "list"
    base = name.split("[", 1)[0].strip()
    if base in _LIST_TYPES:
        return _LIST_TYPES[base]
    name = _TYPE_ALIASES.get(name, name)
    return name if name in {"str", "int", "float", "bool", "dict"} else None


def _inner_type(type_name: str) -> str:
    """取出 list[int] 这类声明里的元素类型；没有申明返回空串。"""
    if "[" not in type_name or not type_name.rstrip().endswith("]"):
        return ""
    return type_name.split("[", 1)[1].rstrip()[:-1].strip()


def _to_int(value: Any) -> tuple[Any, str]:
    if isinstance(value, bool):
        return int(value), ""
    if isinstance(value, int):
        return value, ""
    if isinstance(value, float) and value.is_integer():
        return int(value), ""
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text), ""
        except ValueError:
            try:
                number = float(text)
            except ValueError:
                return value, f"需要 int，收到 {value!r}"
            if number.is_integer():
                return int(number), ""
    return value, f"需要 int，收到 {value!r}"


def _to_float(value: Any) -> tuple[Any, str]:
    if isinstance(value, bool):
        return float(value), ""
    if isinstance(value, int | float):
        return float(value), ""
    if isinstance(value, str):
        try:
            return float(value.strip()), ""
        except ValueError:
            return value, f"需要 float，收到 {value!r}"
    return value, f"需要 float，收到 {value!r}"


def _to_bool(value: Any) -> tuple[Any, str]:
    if isinstance(value, bool):
        return value, ""
    if isinstance(value, int | float):
        return bool(value), ""
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_WORDS:
            return True, ""
        if text in _FALSE_WORDS:
            return False, ""
    return value, f"需要 bool，收到 {value!r}"


def _to_list(value: Any) -> tuple[list[Any], str]:
    """把值归一成列表：真列表直接用，字符串先试字面量再按分隔符切。"""
    if isinstance(value, list):
        return value, ""
    if isinstance(value, tuple | set):
        return list(value), ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return [], ""
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(text)
            except (ValueError, SyntaxError, TypeError):
                continue
            if isinstance(parsed, list | tuple | set):
                return list(parsed), ""
        return [
            part.strip() for part in re.split(r"[，,、;；]", text) if part.strip()
        ], ""
    return [value], ""


def _to_dict(value: Any) -> tuple[Any, str]:
    if isinstance(value, dict):
        return value, ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}, ""
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(text)
            except (ValueError, SyntaxError, TypeError):
                continue
            if isinstance(parsed, dict):
                return parsed, ""
    return value, f"需要 dict，收到 {value!r}"
