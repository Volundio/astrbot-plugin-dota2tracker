"""Luxon compatible date formatting used by the archived templates.

Only the behaviour that the Koishi plugin relied on is implemented:

* ``DateTime.toFormat`` token parsing (``yyyy-MM-dd HH:mm:ss``, ``cccc``…);
* ``DateTime.diff`` for calendar units (years / months / days) and clock units;
* ``DateTime.toRelative`` with ``Intl.RelativeTimeFormat`` output for
  ``zh-CN`` / ``en-US`` (``numeric: "always"``).

All functions take timezone-aware ``datetime`` objects; ``local_now()`` and
``from_seconds()`` reproduce Luxon's default of using the system timezone.
"""

from __future__ import annotations

import calendar
import functools
import math
import os
from datetime import UTC, date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .jsnum import js_str, to_locale_string

__all__ = [
    "add_calendar",
    "format_custom_relative_time",
    "from_seconds",
    "local_now",
    "luxon_diff",
    "luxon_format",
    "minus_days",
    "relative_time",
]

_WEEKDAYS = {
    "zh-CN": {
        "long": ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"],
        "short": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
        "narrow": ["一", "二", "三", "四", "五", "六", "日"],
    },
    "en-US": {
        "long": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        "short": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "narrow": ["M", "T", "W", "T", "F", "S", "S"],
    },
}
_MONTHS = {
    "zh-CN": {
        "long": ["一月", "二月", "三月", "四月", "五月", "六月", "七月", "八月", "九月", "十月", "十一月", "十二月"],
        "short": [f"{i}月" for i in range(1, 13)],
    },
    "en-US": {
        "long": [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        "short": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    },
}
_MERIDIEM = {"zh-CN": ("上午", "下午"), "en-US": ("AM", "PM")}

_RELATIVE_UNITS = ("years", "months", "days", "hours", "minutes", "seconds")
_RELATIVE_TEXT = {
    "zh-CN": {
        "years": ("{n}年前", "{n}年后"),
        "months": ("{n}个月前", "{n}个月后"),
        "days": ("{n}天前", "{n}天后"),
        "hours": ("{n}小时前", "{n}小时后"),
        "minutes": ("{n}分钟前", "{n}分钟后"),
        "seconds": ("{n}秒钟前", "{n}秒钟后"),
    },
    "en-US": {
        "years": ("year", "years"),
        "months": ("month", "months"),
        "days": ("day", "days"),
        "hours": ("hour", "hours"),
        "minutes": ("minute", "minutes"),
        "seconds": ("second", "seconds"),
    },
}


def normalize_locale(locale: str | None) -> str:
    lowered = (locale or "").lower()
    if lowered.startswith("zh"):
        return "zh-CN"
    return "en-US"


@functools.cache
def system_timezone() -> tzinfo | None:
    """The system zone with its DST rules (Luxon's default zone), if it can be determined.

    ``datetime.now().astimezone()`` only yields today's fixed UTC offset, so
    midnight of another day computed from it is off by an hour around DST
    changes. ``TZ`` (an IANA name) wins over ``/etc/localtime`` like in libc;
    a non-IANA ``TZ`` or a system without zone files returns ``None`` and
    callers fall back to the fixed offset.
    """
    name = os.environ.get("TZ", "").lstrip(":")
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return None
    try:
        with open("/etc/localtime", "rb") as handle:
            return ZoneInfo.from_file(handle, key="localtime")
    except (OSError, ValueError):
        return None


def local_now(tz: tzinfo | None = None) -> datetime:
    """``DateTime.now()`` in the given (default: system) timezone."""
    zone = tz or system_timezone()
    if zone is None:
        return datetime.now().astimezone()
    return datetime.now(zone)


def from_seconds(seconds: float, tz: tzinfo | None = None) -> datetime:
    """``DateTime.fromSeconds`` in the given (default: system) timezone."""
    moment = datetime.fromtimestamp(seconds, UTC)
    return moment.astimezone(tz) if tz is not None else moment.astimezone()


def _parse_format(fmt: str) -> list[tuple[bool, str]]:
    """Port of Luxon ``Formatter.parseFormat``: ``(is_literal, value)`` chunks."""
    splits: list[tuple[bool, str]] = []
    current: str | None = None
    current_full = ""
    bracketed = False
    for char in fmt:
        if char == "'":
            if current_full:
                splits.append((bracketed or current_full.isspace(), current_full))
            current = None
            current_full = ""
            bracketed = not bracketed
        elif bracketed or char == current:
            current_full += char
        else:
            if current_full:
                splits.append((current_full.isspace(), current_full))
            current_full = char
            current = char
    if current_full:
        splits.append((bracketed or current_full.isspace(), current_full))
    return splits


def _format_month_name(locale: str, length: str, month: int) -> str:
    """Luxon extracts format-context month names from ``{month, day}``; zh-CN yields digits."""
    if locale == "zh-CN":
        return str(month)
    return _MONTHS[locale][length][month - 1]


def _pad(value: int, width: int) -> str:
    sign = "-" if value < 0 else ""
    return sign + str(abs(value)).rjust(width, "0")


def _token(moment: datetime, token: str, locale: str) -> str:
    weekday_index = moment.isoweekday() - 1
    month_index = moment.month - 1
    hour12 = moment.hour % 12 or 12
    mapping = {
        "S": lambda: str(moment.microsecond // 1000),
        "SSS": lambda: _pad(moment.microsecond // 1000, 3),
        "s": lambda: str(moment.second),
        "ss": lambda: _pad(moment.second, 2),
        "m": lambda: str(moment.minute),
        "mm": lambda: _pad(moment.minute, 2),
        "h": lambda: str(hour12),
        "hh": lambda: _pad(hour12, 2),
        "H": lambda: str(moment.hour),
        "HH": lambda: _pad(moment.hour, 2),
        "a": lambda: _MERIDIEM[locale][0 if moment.hour < 12 else 1],
        "d": lambda: str(moment.day),
        "dd": lambda: _pad(moment.day, 2),
        "c": lambda: str(moment.isoweekday()),
        "E": lambda: str(moment.isoweekday()),
        "ccc": lambda: _WEEKDAYS[locale]["short"][weekday_index],
        "EEE": lambda: _WEEKDAYS[locale]["short"][weekday_index],
        "cccc": lambda: _WEEKDAYS[locale]["long"][weekday_index],
        "EEEE": lambda: _WEEKDAYS[locale]["long"][weekday_index],
        "ccccc": lambda: _WEEKDAYS[locale]["narrow"][weekday_index],
        "EEEEE": lambda: _WEEKDAYS[locale]["narrow"][weekday_index],
        "L": lambda: str(moment.month),
        "M": lambda: str(moment.month),
        "LL": lambda: _pad(moment.month, 2),
        "MM": lambda: _pad(moment.month, 2),
        "LLL": lambda: _MONTHS[locale]["short"][month_index],
        "MMM": lambda: _format_month_name(locale, "short", moment.month),
        "LLLL": lambda: _MONTHS[locale]["long"][month_index],
        "MMMM": lambda: _format_month_name(locale, "long", moment.month),
        "y": lambda: str(moment.year),
        "yy": lambda: _pad(int(str(moment.year)[-2:]), 2),
        "yyyy": lambda: _pad(moment.year, 4),
    }
    factory = mapping.get(token)
    return factory() if factory else token


def luxon_format(moment: datetime, fmt: str, locale: str | None = None) -> str:
    """Port of Luxon ``toFormat`` for the tokens used by the plugin."""
    resolved = normalize_locale(locale)
    return "".join(value if literal else _token(moment, value, resolved) for literal, value in _parse_format(fmt))


def _days_in_month(year: int, month: int) -> int:
    mod_month = (month - 1) % 12 + 1
    mod_year = year + (month - mod_month) // 12
    return calendar.monthrange(mod_year, mod_month)[1]


def add_calendar(moment: datetime, years: int = 0, months: int = 0, days: int = 0) -> datetime:
    """Luxon ``plus`` for calendar units (wall time preserved, day clamped)."""
    year = moment.year + years
    month = moment.month + months
    day = min(moment.day, _days_in_month(year, month))
    normalized_year = year + (month - 1) // 12
    normalized_month = (month - 1) % 12 + 1
    base = moment.replace(year=normalized_year, month=normalized_month, day=day)
    if days:
        naive = base.replace(tzinfo=None) + timedelta(days=days)
        base = naive.replace(tzinfo=moment.tzinfo)
    return base


def minus_days(moment: datetime, days: float) -> datetime:
    """Luxon ``minus({ days })``: whole days keep the wall clock, fractions are milliseconds."""
    whole = math.trunc(days)
    result = add_calendar(moment, days=-whole)
    fraction = days - whole
    return result - timedelta(days=fraction) if fraction else result


def _ms(later: datetime, earlier: datetime) -> float:
    """Elapsed milliseconds using absolute instants (same-tzinfo subtraction ignores DST)."""
    return (later.timestamp() - earlier.timestamp()) * 1000.0


def _after(left: datetime, right: datetime) -> bool:
    return left.timestamp() > right.timestamp()


def _day_diff(earlier: datetime, later: datetime) -> int:
    return (date(later.year, later.month, later.day) - date(earlier.year, earlier.month, earlier.day)).days


def _calendar_diff(earlier: datetime, later: datetime, unit: str) -> float:
    differs = {
        "years": lambda a, b: b.year - a.year,
        "months": lambda a, b: b.month - a.month + (b.year - a.year) * 12,
        "days": _day_diff,
    }
    plus = {
        "years": lambda base, n: add_calendar(base, years=n),
        "months": lambda base, n: add_calendar(base, months=n),
        "days": lambda base, n: add_calendar(base, days=n),
    }
    # Mirrors Luxon's ``highOrderDiffs`` followed by the fractional remainder step.
    count = differs[unit](earlier, later)
    high_water = plus[unit](earlier, count)
    same_object = False
    if _after(high_water, later):
        count -= 1
        cursor = plus[unit](earlier, count)
        if _after(cursor, later):
            high_water = cursor
            count -= 1
            cursor = plus[unit](earlier, count)
    else:
        cursor = high_water
        same_object = True
    remaining = _ms(later, cursor)
    if _after(later, high_water):
        high_water = plus[unit](cursor, 1)
        same_object = False
    if not same_object:
        span = _ms(high_water, cursor)
        if span:
            return count + remaining / span
    return float(count)


_CLOCK_MS = {"hours": 3_600_000.0, "minutes": 60_000.0, "seconds": 1000.0}


def luxon_diff(this: datetime, other: datetime, unit: str) -> float:
    """``this.diff(other, unit).get(unit)`` – positive when ``this`` is later."""
    other_is_later = _after(other, this)
    earlier, later = (this, other) if other_is_later else (other, this)
    if unit in _CLOCK_MS:
        value = _ms(later, earlier) / _CLOCK_MS[unit]
    else:
        value = _calendar_diff(earlier, later, unit)
    return -value if other_is_later else value


def _format_relative(count: float, unit: str, locale: str) -> str:
    truncated = math.trunc(count)
    negative = count < 0 or (count == 0 and math.copysign(1.0, count) < 0)
    number = to_locale_string(abs(int(truncated)))
    if locale == "zh-CN":
        past, future = _RELATIVE_TEXT["zh-CN"][unit]
        return (past if negative else future).format(n=number)
    singular, plural = _RELATIVE_TEXT["en-US"][unit]
    word = singular if abs(int(truncated)) == 1 else plural
    return f"{number} {word} ago" if negative else f"in {number} {word}"


def relative_time(target: datetime, base: datetime, locale: str | None) -> str:
    """Luxon ``target.toRelative({ base })`` with ``trunc`` rounding."""
    resolved = normalize_locale(locale)
    for unit in _RELATIVE_UNITS:
        count = luxon_diff(target, base, unit)
        if abs(count) >= 1:
            return _format_relative(count, unit, resolved)
    return _format_relative(luxon_diff(target, base, "seconds"), "seconds", resolved)


def format_custom_relative_time(target: datetime, now: datetime, locale: str | None, translate) -> str:
    """Port of ``formatCustomRelativeTime`` (Luxon relative time + i18n years)."""
    diff_in_months = luxon_diff(now, target, "months")
    if diff_in_months < 12:
        return relative_time(target, now, locale)
    years = math.floor(diff_in_months / 12)
    months = math.floor(math.fmod(diff_in_months, 12))
    if months == 0:
        return translate("dota2tracker.time.years_ago", {"years": years})
    return translate("dota2tracker.time.years_months_ago", {"years": years, "months": months})


def iso_date(seconds: float) -> str:
    """``new Date(seconds * 1000).toISOString().split('T')[0]`` (UTC date)."""
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d")


def js_date_str(seconds: float) -> str:
    """Helper for debugging output; keeps JS number formatting for seconds."""
    return js_str(seconds)
