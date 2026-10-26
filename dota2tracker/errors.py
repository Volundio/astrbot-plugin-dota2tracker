"""Error types, classification and reporting (port of ``common/error.ts``).

API clients wrap transport failures into :class:`NetworkError` /
:class:`ForbiddenError`, Stratz business failures raise
:class:`GraphQLQueryError`, and the match service combines both data sources
into :class:`FetchMatchDataFailError`. :class:`ErrorReporter` reproduces the
original ``handleError`` logging (including ``suppress_api_network_errors``)
and keeps a bounded, redacted list of recent errors for the WebUI.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal

import httpx

__all__ = [
    "ApiError",
    "ErrorReporter",
    "ErrorType",
    "FetchMatchDataFailError",
    "ForbiddenError",
    "GraphQLQueryError",
    "NetworkError",
    "PluginStoppedError",
    "classify_error",
    "redact",
    "wrap_fetch_error",
]

ErrorType = Literal[
    "Forbidden",
    "NotFound",
    "ClientError",
    "ServerError",
    "Timeout",
    "Network",
    "Cancel",
    "GraphQLQueryError",
    "Unknown",
]


class ApiError(Exception):
    """Base class for wrapped API failures."""

    def __init__(self, message: str, *, service: str = "", error_type: ErrorType = "Unknown", detail: str = "") -> None:
        super().__init__(message)
        self.service = service
        self.error_type = error_type
        self.detail = detail


class NetworkError(ApiError):
    """Timeouts, connection failures and non-403 HTTP errors."""


class ForbiddenError(ApiError):
    """HTTP 403 – usually an invalid/banned token or rate limit."""


class GraphQLQueryError(ApiError):
    """Stratz returned errors without usable data."""

    def __init__(self, message: str, errors: list[str], *, service: str = "Stratz") -> None:
        super().__init__(message, service=service, error_type="GraphQLQueryError", detail="\n".join(errors))
        self.errors = errors


class FetchMatchDataFailError(Exception):
    """Both Stratz and OpenDota failed (or the fallback failed after Stratz)."""

    def __init__(
        self, message: str, *, stratz_error: BaseException | None = None, opendota_error: BaseException | None = None
    ):
        super().__init__(message)
        self.stratz_error = stratz_error
        self.opendota_error = opendota_error
        self.__cause__ = stratz_error or opendota_error


class PluginStoppedError(RuntimeError):
    """Raised when a request is attempted after the plugin was terminated."""


def classify_error(error: BaseException) -> ErrorType:
    """Port of ``classifyAxiosError`` for httpx exceptions."""
    if isinstance(error, asyncio.CancelledError):
        return "Cancel"
    if isinstance(error, PluginStoppedError):
        return "Cancel"
    if isinstance(error, httpx.TimeoutException):
        return "Timeout"
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status == 403:
            return "Forbidden"
        if status == 404:
            return "NotFound"
        if 400 <= status < 500:
            return "ClientError"
        if status >= 500:
            return "ServerError"
    if isinstance(error, httpx.RequestError):
        return "Network"
    if isinstance(error, GraphQLQueryError):
        return "GraphQLQueryError"
    return "Unknown"


_SENSITIVE_HEADERS = ("authorization", "proxy-authorization", "cookie", "x-api-key")


def redact(text: str, secrets: list[str] | tuple[str, ...] = ()) -> str:
    """Remove configured secrets from free text before logging or exporting."""
    result = text
    for secret in secrets:
        if secret and len(secret) >= 4:
            result = result.replace(secret, "***")
    return result


def _response_excerpt(error: BaseException, limit: int = 500) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        try:
            body = error.response.text
        except Exception:  # pragma: no cover - defensive
            body = ""
        body = body.replace("\n", " ")
        return f"HTTP {error.response.status_code}: {body[:limit]}"
    return ""


def wrap_fetch_error(error: BaseException, service: str, query_name: str) -> BaseException:
    """Port of ``processFetchError``: returns the exception that should be raised."""
    error_type = classify_error(error)
    message = f"{service} query ({query_name}) failed. Type: {error_type}"
    detail = _response_excerpt(error) or f"{type(error).__name__}: {error}"
    if error_type == "Forbidden":
        wrapped: BaseException = ForbiddenError(message, service=service, error_type=error_type, detail=detail)
    elif error_type in ("Timeout", "Network", "ServerError", "ClientError", "NotFound"):
        wrapped = NetworkError(message, service=service, error_type=error_type, detail=detail)
    else:
        return error
    wrapped.__cause__ = error
    return wrapped


@dataclass
class RecentError:
    timestamp: float
    source: str
    error_type: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": int(self.timestamp),
            "source": self.source,
            "type": self.error_type,
            "message": self.message,
        }


class ErrorReporter:
    """Port of ``handleError`` plus a redacted ring buffer of recent errors."""

    def __init__(
        self,
        logger: logging.Logger,
        *,
        translate=None,
        suppress_network_errors: bool = False,
        secrets: tuple[str, ...] = (),
        capacity: int = 50,
    ) -> None:
        self._logger = logger
        self._translate = translate or (lambda key, params=None: key)
        self._suppress = suppress_network_errors
        self._secrets = secrets
        self._recent: deque[RecentError] = deque(maxlen=capacity)

    def recent(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in reversed(self._recent)]

    def clear(self) -> None:
        self._recent.clear()

    def redact(self, text: str) -> str:
        return redact(text, self._secrets)

    def _remember(self, source: str, error_type: str, message: str) -> None:
        self._recent.append(RecentError(time.time(), source, error_type, redact(message, self._secrets)[:1000]))

    def _log(self, level: int, message: str) -> None:
        self._logger.log(level, redact(message, self._secrets))

    def report(self, error: BaseException, source: str = "") -> None:
        if isinstance(error, FetchMatchDataFailError):
            if error.stratz_error is not None and error.opendota_error is not None:
                self._log(logging.ERROR, "Failed to fetch match data from all available sources. See details below:")
            else:
                self._log(logging.ERROR, "A data source failed during the match fetch process. See details below:")
            if error.stratz_error is not None:
                self._log(logging.ERROR, "--- Stratz API Error ---")
                self.report(error.stratz_error, source or "match")
            if error.opendota_error is not None:
                self._log(logging.ERROR, "--- OpenDota API Error ---")
                self.report(error.opendota_error, source or "match")
            return
        if isinstance(error, ForbiddenError):
            key = "stratz_token_banned" if "Stratz" in str(error) else "opendota_token_banned"
            output = self._translate(f"dota2tracker.logger.{key}") + "\n"
            if error.detail:
                output += f"Details: {error.detail}\n"
            output += str(error)
            self._log(logging.ERROR, output)
            self._remember(source or error.service, "Forbidden", str(error))
            return
        if isinstance(error, GraphQLQueryError):
            self._log(logging.ERROR, f"Stratz GraphQL Query Error: {error}")
            self._log(logging.ERROR, f"Details: {error.errors}")
            self._remember(source or "Stratz", "GraphQLQueryError", f"{error}: {error.detail}")
            return
        if isinstance(error, NetworkError):
            output = f"A network error occurred: {error}\n"
            if error.detail:
                output += f"Details: {error.detail}\n"
            self._log(logging.DEBUG if self._suppress else logging.ERROR, output.rstrip())
            self._remember(source or error.service, error.error_type, f"{error} {error.detail}".strip())
            return
        stack = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        self._log(logging.ERROR, f"An unexpected error was thrown:\n{stack}")
        self._remember(source or "plugin", type(error).__name__, str(error) or type(error).__name__)
