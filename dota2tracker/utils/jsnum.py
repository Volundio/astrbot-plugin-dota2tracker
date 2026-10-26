"""JavaScript number semantics used by the archived Koishi implementation.

The original plugin formats numbers with ``Math.round``, ``toFixed``,
``String(number)`` and ``toLocaleString``. Python's defaults differ (banker's
rounding, ``repr`` formatting, ``1.0`` vs ``1``), so every user visible number
goes through the helpers in this module to keep the output identical.
"""

from __future__ import annotations

import math
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

__all__ = [
    "format_number",
    "has_line_terminator",
    "is_canonical_index",
    "js_div",
    "js_number",
    "js_object_key_order",
    "js_parse_int",
    "js_round",
    "js_str",
    "js_to_fixed",
    "js_truthy",
    "round_to_decimal_places",
    "to_locale_string",
    "utf16_length",
]

_GROUPING_RE = re.compile(r"\B(?=(\d{3})+(?!\d))")
_LINE_TERMINATORS = ("\n", "\r", "\u2028", "\u2029")


def js_truthy(value: Any) -> bool:
    """Return JavaScript truthiness (``0``, ``NaN``, ``""``, ``None`` are falsy)."""
    if value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, float) and math.isnan(value):
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (dict, list, tuple, set)):
        return True  # objects and arrays are always truthy in JS, even when empty
    return bool(value)


def js_round(value: float) -> float | int:
    """``Math.round``: nearest integer, ties rounded towards ``+Infinity``."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if math.isnan(value) or math.isinf(value):
        return value
    floor = math.floor(value)
    return floor + 1 if value - floor >= 0.5 else floor


def round_to_decimal_places(number: float, decimal_places: int = 2) -> float | int:
    """Port of ``roundToDecimalPlaces``: ``Math.round(n * 10^p) / 10^p``."""
    factor = 10**decimal_places
    rounded = js_round(number * factor)
    if isinstance(rounded, float) and (math.isnan(rounded) or math.isinf(rounded)):
        return rounded
    result = rounded / factor
    return int(result) if result == int(result) and abs(result) < 1e21 else result


def js_str(value: Any) -> str:
    """``String(value)`` for numbers (ECMAScript ``Number::toString``)."""
    if value is None:
        return "undefined"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, float):
        return str(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    text = repr(abs(value))
    mantissa, _, exponent_text = text.partition("e")
    exponent = int(exponent_text) if exponent_text else 0
    int_part, _, frac_part = mantissa.partition(".")
    all_digits = int_part + frac_part
    stripped = all_digits.lstrip("0")
    leading_zeros = len(all_digits) - len(stripped)
    digits = stripped.rstrip("0") or "0"
    k = len(digits)
    n = len(int_part) + exponent - leading_zeros
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        body = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return sign + body


def js_to_fixed(value: Any, digits: int = 0) -> str:
    """``Number.prototype.toFixed`` (round half away from zero on the exact binary value)."""
    if value is None:
        value = math.nan
    if isinstance(value, bool):
        value = int(value)
    number = float(value)
    if math.isnan(number):
        return "NaN"
    if abs(number) >= 1e21 or math.isinf(number):
        return js_str(number)
    if number == 0:
        number = 0.0
    quantum = Decimal(1).scaleb(-digits)
    result = Decimal(number).quantize(quantum, rounding=ROUND_HALF_UP)
    # Decimal keeps the sign for small negatives ("-0.00"), exactly like JS.
    return format(result, "f")


def js_div(numerator: float, denominator: float) -> float:
    """IEEE division without Python's ZeroDivisionError (``x/0`` -> ``±Infinity``/``NaN``)."""
    if denominator == 0:
        if numerator == 0 or (isinstance(numerator, float) and math.isnan(numerator)):
            return math.nan
        negative = (numerator < 0) != (math.copysign(1.0, float(denominator)) < 0)
        return -math.inf if negative else math.inf
    return numerator / denominator


def format_number(value: Any) -> str:
    """Port of ``formatNumber``: thousands separators inserted into ``String(n)``."""
    return _GROUPING_RE.sub(",", js_str(value))


def to_locale_string(value: Any) -> str:
    """``Number.prototype.toLocaleString()`` for en-US / zh-CN (identical grouping)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value:,}"
    number = float(value)
    if math.isnan(number):
        return "NaN"
    if math.isinf(number):
        return "∞" if number > 0 else "-∞"
    rounded = Decimal(number).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    if rounded == rounded.to_integral_value():
        return f"{int(rounded):,}"
    integer_part = int(rounded)
    fraction = format(abs(rounded - integer_part), "f").split(".")[1].rstrip("0")
    sign = "-" if rounded < 0 and integer_part == 0 else ""
    return f"{sign}{integer_part:,}.{fraction}"


def js_parse_int(value: Any) -> float | int:
    """``parseInt(String(value))`` with radix 10; returns ``NaN`` when no digits."""
    text = js_str(value) if isinstance(value, (int, float)) else ("" if value is None else str(value))
    match = re.match(r"^\s*([+-]?)([0-9]+)", text)
    if not match:
        return math.nan
    number = int(match.group(2))
    return -number if match.group(1) == "-" else number


def js_number(value: Any) -> float | int:
    """``Number(string)`` conversion used for item ID detection."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if value is None:
        return 0
    text = str(value).strip()
    if text == "":
        return 0
    lowered = text.lower()
    try:
        if re.fullmatch(r"0x[0-9a-f]+", lowered):
            return int(lowered, 16)
        if re.fullmatch(r"0o[0-7]+", lowered):
            return int(lowered, 8)
        if re.fullmatch(r"0b[01]+", lowered):
            return int(lowered, 2)
    except ValueError:
        return math.nan
    if text in ("Infinity", "+Infinity"):
        return math.inf
    if text == "-Infinity":
        return -math.inf
    if re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", text):
        number = float(text)
        return int(number) if number.is_integer() and abs(number) < 2**53 else number
    return math.nan


def is_canonical_index(key: Any) -> bool:
    """Whether a property key is an array index (sorted first by JS objects)."""
    text = str(key)
    if not text.isdigit() or not text.isascii():
        return False
    if len(text) > 1 and text[0] == "0":
        return False
    return int(text) < 2**32 - 1


def js_object_key_order(keys: list[Any]) -> list[Any]:
    """Order keys like ``Object.keys``: integer keys ascending, then insertion order."""
    indices = [k for k in keys if is_canonical_index(k)]
    others = [k for k in keys if not is_canonical_index(k)]
    return sorted(indices, key=lambda k: int(str(k))) + others


def utf16_length(text: str) -> int:
    """Length in UTF-16 code units, matching ``String.prototype.length``."""
    return len(text.encode("utf-16-le")) // 2


def has_line_terminator(text: str) -> bool:
    return any(terminator in text for terminator in _LINE_TERMINATORS)
