"""Koishi / Satori compatible message template interpreter.

Koishi renders translations with ``Element.parse(template, params)`` from
``@satorijs/element``. The syntax supports ``{path}`` interpolation,
``{#if expr}…{:else}…{/if}``, ``{#each list as item}…{/each}`` and inline
angle-bracket elements. Whitespace that contains a line break is trimmed next
to angle tags and at the beginning/end of the template, but *not* next to curly
blocks. This module reproduces those rules so multi-line translations render
exactly like the original plugin.

JavaScript expressions inside ``{#if}``/``{expr}`` are evaluated by a tiny,
side-effect free interpreter (member access, ``length``, arithmetic,
comparisons and boolean operators). Nothing is ``eval``-ed.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..utils.jsnum import js_str, js_truthy

__all__ = ["Node", "TemplateError", "escape", "parse_template", "render_html", "render_text", "satori_trim"]

_TAG_PATTERN = re.compile(
    r"(?P<comment><!--[\s\S]*?-->)"
    r"|(?P<tag><(?P<close>/?)(?P<type>[^!\s>/]*)(?P<extra>[^>]*?)\s*(?P<empty>/?)>)"
    r"|(?P<curly>\{(?P<derivative>[@:/#][^\s}]*)?[\s\S]*?\})"
)
_TAG_PATTERN_NO_CONTEXT = re.compile(
    r"(?P<comment><!--[\s\S]*?-->)|(?P<tag><(?P<close>/?)(?P<type>[^!\s>/]*)(?P<extra>[^>]*?)\s*(?P<empty>/?)>)"
)
_ATTR_PATTERN = re.compile(r"""([^\s=]+)(?:="(?P<v1>[^"]*)"|='(?P<v2>[^']*)'|=\{(?P<curly>[^}]+)\})?""")
_SIMPLE_PATH = re.compile(r"^[\w.]+$")

OPEN, CLOSE, EMPTY, CONTINUE = range(4)


class TemplateError(ValueError):
    """Raised for malformed expressions (mirrors JS exceptions that Satori swallows)."""


class _Undefined:
    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return "undefined"

    def __bool__(self) -> bool:
        return False


UNDEFINED = _Undefined()


def escape(source: str, inline: bool = False) -> str:
    result = (source or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return result.replace('"', "&quot;") if inline else result


def unescape(source: str) -> str:
    def numeric(match: re.Match[str]) -> str:
        code = match.group(1)
        return match.group(0) if code == "38" else chr(int(code))

    def hexadecimal(match: re.Match[str]) -> str:
        code = match.group(1)
        return match.group(0) if code.lower() == "26" else chr(int(code, 16))

    source = source.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    source = re.sub(r"&#(\d+);", numeric, source)
    source = re.sub(r"&#x([0-9a-f]+);", hexadecimal, source, flags=re.I)
    return re.sub(r"&(amp|#38|#x26);", "&", source)


def satori_trim(text: str) -> str:
    """Trimming applied when a plain string is parsed as a Satori message."""
    text = re.sub(r"^\s*\n\s*", "", text)
    return re.sub(r"\s*\n\s*$", "", text)


@dataclass
class _Token:
    kind: str  # "angle" | "curly"
    name: str
    position: int
    source: str
    extra: str
    children: dict[str, list[Any]] | None = None


@dataclass
class Node:
    """Rendered element: ``text`` nodes carry ``content``; others are tags."""

    type: str
    attrs: dict[str, Any] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)

    @property
    def content(self) -> str:
        return str(self.attrs.get("content", ""))

    def to_markup(self) -> str:
        """``Element.toString()``: escaped text, tags serialised Satori style."""
        if self.type == "text":
            return escape(self.content)
        inner = "".join(child.to_markup() for child in self.children)
        attrs = "".join(_attr_string(key, value) for key, value in self.attrs.items())
        if not self.children:
            return f"<{self.type}{attrs}/>"
        return f"<{self.type}{attrs}>{inner}</{self.type}>"

    def to_plain(self) -> str:
        """``Element.toString(true)``: text content only."""
        if self.type == "text":
            return self.content
        return "".join(child.to_plain() for child in self.children)


def _attr_string(key: str, value: Any) -> str:
    if value is None or value is UNDEFINED:
        return ""
    key = re.sub(r"[A-Z]", lambda m: "-" + m.group(0).lower(), key)
    if value is True:
        return f" {key}"
    if value is False:
        return f" no-{key}"
    return f' {key}="{escape(_to_text(value), True)}"'


def _to_text(value: Any) -> str:
    if value is None or value is UNDEFINED:
        return ""
    if isinstance(value, (bool, int, float)):
        return js_str(value)
    return str(value)


def parse_template(source: str, context: Any = None) -> list[Node]:
    """Port of ``Element.parse(source, context)``."""
    tokens: list[str | _Token] = []
    pattern = _TAG_PATTERN if context is not None else _TAG_PATTERN_NO_CONTEXT
    trim_start = True
    rest = source

    def push_text(content: str, start: bool, end: bool) -> None:
        content = unescape(content)
        if start:
            content = re.sub(r"^\s*\n\s*", "", content)
        if end:
            content = re.sub(r"\s*\n\s*$", "", content)
        if content:
            tokens.append(content)

    while True:
        match = pattern.search(rest)
        if not match:
            break
        curly = match.groupdict().get("curly")
        trim_end = not curly
        push_text(rest[: match.start()], trim_start, trim_end)
        trim_start = trim_end
        rest = rest[match.end() :]
        if match.group("comment"):
            continue
        if curly:
            derivative = match.group("derivative")
            name, position = "", EMPTY
            if derivative:
                name = derivative[1:]
                position = {"@": EMPTY, "#": OPEN, "/": CLOSE, ":": CONTINUE}[derivative[0]]
            extra = curly[1 + len(derivative or "") : -1]
            tokens.append(_Token("curly", name, position, curly, extra))
            continue
        close, tag_type, extra, empty = (
            match.group("close"),
            match.group("type"),
            match.group("extra"),
            match.group("empty"),
        )
        position = CLOSE if close else EMPTY if empty else OPEN
        tokens.append(_Token("angle", tag_type or "template", position, match.group(0), extra))
    push_text(rest, trim_start, True)
    return _parse_tokens(_fold_tokens(tokens), context)


def _fold_tokens(tokens: Sequence[str | _Token]) -> list[str | _Token]:
    root = _Token("angle", "template", OPEN, "", "", {"default": []})
    stack: list[list[Any]] = [[root, "default"]]

    for token in tokens:
        if isinstance(token, str):
            stack[0][0].children[stack[0][1]].append(token)
            continue
        if token.position == CLOSE:
            if stack[0][0].name == token.name:
                stack.pop(0)
        elif token.position == CONTINUE:
            stack[0][0].children[token.name] = []
            stack[0][1] = token.name
        elif token.position == OPEN:
            stack[0][0].children[stack[0][1]].append(token)
            token.children = {"default": []}
            stack.insert(0, [token, "default"])
        else:
            stack[0][0].children[stack[0][1]].append(token)
    return stack[-1][0].children["default"]


def _to_nodes(value: Any) -> list[Node]:
    if value is None or value is UNDEFINED:
        return []
    if isinstance(value, Node):
        return [value]
    if isinstance(value, (list, tuple)):
        nodes: list[Node] = []
        for item in value:
            nodes.extend(_to_nodes(item))
        return nodes
    text = _to_text(value)
    return [Node("text", {"content": text})] if text else []


def _parse_tokens(tokens: Iterable[str | _Token], context: Any) -> list[Node]:
    result: list[Node] = []
    for token in tokens:
        if isinstance(token, str):
            result.append(Node("text", {"content": token}))
            continue
        if token.kind == "angle":
            attrs: dict[str, Any] = {}
            for attr in _ATTR_PATTERN.finditer(token.extra):
                key = attr.group(1)
                value = attr.group("v1") if attr.group("v1") is not None else attr.group("v2")
                curly = attr.group("curly")
                if curly and context is not None:
                    attrs[key] = interpolate(curly, context)
                elif value is not None:
                    attrs[key] = unescape(value)
                elif key.startswith("no-"):
                    attrs[key[3:]] = False
                else:
                    attrs[key] = True
            children = _parse_tokens(token.children["default"], context) if token.children else []
            result.append(Node(token.name, attrs, children))
        elif not token.name:
            result.extend(_to_nodes(interpolate(token.extra, context)))
        elif token.name == "if":
            branch = "default" if js_truthy(_safe_evaluate(token.extra, context)) else "else"
            result.extend(_parse_tokens((token.children or {}).get(branch, []), context))
        elif token.name == "each":
            parts = re.split(r"\s+as\s+", token.extra)
            expr = parts[0]
            ident = parts[1].strip() if len(parts) > 1 else "undefined"
            items = interpolate(expr, context)
            # ``if (!items || !items[Symbol.iterator]) continue`` – plain objects are not iterable.
            if not js_truthy(items) or not isinstance(items, (list, tuple, str)):
                continue
            for item in items:
                child_context = dict(context) if isinstance(context, Mapping) else {}
                child_context[ident] = item
                result.extend(_parse_tokens((token.children or {}).get("default", []), child_context))
    return result


def interpolate(expr: str, context: Any) -> Any:
    """Port of ``Element.interpolate``."""
    expr = expr.strip()
    if not _SIMPLE_PATH.match(expr):
        value = _safe_evaluate(expr, context)
        return "" if value is None or value is UNDEFINED else value
    value = context
    for part in expr.split("."):
        value = _get_member(value, part)
        if value is None or value is UNDEFINED:
            return ""
    return value


def _safe_evaluate(expr: str, context: Any) -> Any:
    try:
        return _Evaluator(expr, context).evaluate()
    except (TemplateError, TypeError, ValueError, IndexError, KeyError, ZeroDivisionError):
        return UNDEFINED


def _get_member(value: Any, key: Any) -> Any:
    if value is None or value is UNDEFINED:
        raise TemplateError("cannot read property of undefined")
    if isinstance(value, Mapping):
        if key in value:
            return value[key]
        if isinstance(key, str) and key.isdigit() and int(key) in value:
            return value[int(key)]
        return UNDEFINED
    if isinstance(value, (list, tuple, str)):
        if key == "length":
            return len(value)
        index = key if isinstance(key, int) else (int(key) if isinstance(key, str) and key.isdigit() else None)
        if isinstance(key, float) and key.is_integer():
            index = int(key)
        if index is not None and 0 <= index < len(value):
            return value[index]
        return UNDEFINED
    attr = getattr(value, str(key), UNDEFINED)
    return attr


_TOKEN_RE = re.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<str>\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')"
    r"|(?P<op>===|!==|==|!=|<=|>=|&&|\|\||[-+*/%<>!()\[\].?:])|(?P<name>[A-Za-z_$][\w$]*))"
)


class _Evaluator:
    """Recursive-descent evaluator for the small JS subset used in locales."""

    def __init__(self, source: str, context: Any) -> None:
        self.tokens: list[tuple[str, str]] = []
        position = 0
        source = source.strip()
        while position < len(source):
            match = _TOKEN_RE.match(source, position)
            if not match or match.end() == position:
                raise TemplateError(f"unexpected token in {source!r}")
            kind = match.lastgroup or ""
            self.tokens.append((kind, match.group(kind)))
            position = match.end()
        self.index = 0
        self.context = context

    def evaluate(self) -> Any:
        value = self._or()
        if self.index != len(self.tokens):
            raise TemplateError("trailing tokens")
        return value

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _take(self, op: str | None = None) -> tuple[str, str]:
        token = self._peek()
        if token is None or (op is not None and token[1] != op):
            raise TemplateError(f"expected {op}")
        self.index += 1
        return token

    def _or(self) -> Any:
        left = self._and()
        while self._peek() == ("op", "||"):
            self._take()
            right = self._and()
            left = left if js_truthy(left) else right
        return left

    def _and(self) -> Any:
        left = self._equality()
        while self._peek() == ("op", "&&"):
            self._take()
            right = self._equality()
            left = right if js_truthy(left) else left
        return left

    def _equality(self) -> Any:
        left = self._relational()
        while self._peek() in (("op", "==="), ("op", "!=="), ("op", "=="), ("op", "!=")):
            op = self._take()[1]
            right = self._relational()
            same = _strict_equal(left, right) if op in ("===", "!==") else _loose_equal(left, right)
            left = same if op in ("===", "==") else not same
        return left

    def _relational(self) -> Any:
        left = self._additive()
        while self._peek() in (("op", "<"), ("op", ">"), ("op", "<="), ("op", ">=")):
            op = self._take()[1]
            right = self._additive()
            left = {"<": left < right, ">": left > right, "<=": left <= right, ">=": left >= right}[op]
        return left

    def _additive(self) -> Any:
        left = self._multiplicative()
        while self._peek() in (("op", "+"), ("op", "-")):
            op = self._take()[1]
            right = self._multiplicative()
            if op == "+":
                left = (
                    _to_text(left) + _to_text(right)
                    if isinstance(left, str) or isinstance(right, str)
                    else left + right
                )
            else:
                left = left - right
        return left

    def _multiplicative(self) -> Any:
        left = self._unary()
        while self._peek() in (("op", "*"), ("op", "/"), ("op", "%")):
            op = self._take()[1]
            right = self._unary()
            left = {"*": lambda a, b: a * b, "/": lambda a, b: a / b, "%": lambda a, b: a % b}[op](left, right)
        return left

    def _unary(self) -> Any:
        token = self._peek()
        if token == ("op", "!"):
            self._take()
            return not js_truthy(self._unary())
        if token == ("op", "-"):
            self._take()
            return -self._unary()
        return self._member()

    def _member(self) -> Any:
        value = self._primary()
        while True:
            token = self._peek()
            if token == ("op", "."):
                self._take()
                name = self._take()
                if name[0] != "name":
                    raise TemplateError("expected property name")
                value = _get_member(value, name[1])
            elif token == ("op", "["):
                self._take()
                key = self._or()
                self._take("]")
                value = _get_member(value, key if not isinstance(key, float) or not key.is_integer() else int(key))
            else:
                return value

    def _primary(self) -> Any:
        kind, text = self._take()
        if kind == "num":
            return float(text) if "." in text else int(text)
        if kind == "str":
            return bytes(text[1:-1], "utf-8").decode("unicode_escape")
        if kind == "name":
            if text == "true":
                return True
            if text == "false":
                return False
            if text in ("null",):
                return None
            if text == "undefined":
                return UNDEFINED
            if isinstance(self.context, Mapping) and text in self.context:
                return self.context[text]
            raise TemplateError(f"{text} is not defined")
        if text == "(":
            value = self._or()
            self._take(")")
            return value
        raise TemplateError(f"unexpected {text}")


def _strict_equal(left: Any, right: Any) -> bool:
    if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
        return left is right
    if left is UNDEFINED or right is UNDEFINED:
        return left is right
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    numeric = (int, float)
    if isinstance(left, numeric) and isinstance(right, numeric):
        return left == right
    if type(left) is not type(right):
        return False
    return left == right


def _loose_equal(left: Any, right: Any) -> bool:
    if (left is None or left is UNDEFINED) and (right is None or right is UNDEFINED):
        return True
    return _strict_equal(left, right) or _to_text(left) == _to_text(right)


def render_text(source: str, context: Any = None) -> str:
    """Render to plain text (what users see after Koishi parses the message)."""
    return "".join(node.to_plain() if node.type != "text" else node.content for node in parse_template(source, context))


def render_html(source: str, context: Any = None) -> str:
    """Render to HTML: text escaped, inline tags serialised like ``Element.toString``."""
    return "".join(node.to_markup() for node in parse_template(source, context))


def html_unescape(text: str) -> str:
    return html.unescape(text)
