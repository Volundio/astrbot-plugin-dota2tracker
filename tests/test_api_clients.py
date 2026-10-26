"""Tests for the HTTP plumbing, Stratz/Valve/OpenDota clients and error handling."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from dota2tracker.constants import GRAPHQL_DIR
from dota2tracker.data import http as http_module
from dota2tracker.data.apis import OpenDotaAPI, ValveAPI
from dota2tracker.data.http import DEFAULT_TIMEOUT, RequestQueue, build_client
from dota2tracker.data.stratz import BASE_URL, CHUNK_SIZE, StratzAPI, load_graphql
from dota2tracker.errors import (
    ErrorReporter,
    FetchMatchDataFailError,
    ForbiddenError,
    GraphQLQueryError,
    NetworkError,
    PluginStoppedError,
    classify_error,
    redact,
    wrap_fetch_error,
)

TOKEN = "STRATZ-SECRET-TOKEN-123"
OD_KEY = "OD-SECRET-KEY-456"


def status_error(code: int, body: str = "oops") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", BASE_URL, headers={"Authorization": f"Bearer {TOKEN}"})
    response = httpx.Response(code, request=request, text=body)
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


class Recorder:
    def __init__(self, handler):
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


# ----------------------------------------------------------- classification
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (status_error(403), "Forbidden"),
        (status_error(404), "NotFound"),
        (status_error(429), "ClientError"),
        (status_error(400), "ClientError"),
        (status_error(500), "ServerError"),
        (status_error(503), "ServerError"),
        (httpx.ReadTimeout("t"), "Timeout"),
        (httpx.ConnectTimeout("t"), "Timeout"),
        (httpx.ConnectError("c"), "Network"),
        (httpx.RemoteProtocolError("r"), "Network"),
        (asyncio.CancelledError(), "Cancel"),
        (PluginStoppedError("s"), "Cancel"),
        (GraphQLQueryError("g", ["e"]), "GraphQLQueryError"),
        (ValueError("x"), "Unknown"),
    ],
)
def test_classify_error(error, expected):
    assert classify_error(error) == expected


@pytest.mark.parametrize(
    ("error", "cls", "error_type"),
    [
        (status_error(403, "banned"), ForbiddenError, "Forbidden"),
        (status_error(404), NetworkError, "NotFound"),
        (status_error(418), NetworkError, "ClientError"),
        (status_error(502), NetworkError, "ServerError"),
        (httpx.ReadTimeout("t"), NetworkError, "Timeout"),
        (httpx.ConnectError("c"), NetworkError, "Network"),
    ],
)
def test_wrap_fetch_error(error, cls, error_type):
    wrapped = wrap_fetch_error(error, "Stratz", "MatchInfo")
    assert type(wrapped) is cls
    assert wrapped.error_type == error_type
    assert wrapped.service == "Stratz"
    assert "Stratz query (MatchInfo) failed" in str(wrapped)
    assert wrapped.__cause__ is error
    assert TOKEN not in wrapped.detail


def test_wrap_fetch_error_passthrough_and_excerpt():
    original = ValueError("x")
    assert wrap_fetch_error(original, "S", "Q") is original
    cancelled = asyncio.CancelledError()
    assert wrap_fetch_error(cancelled, "S", "Q") is cancelled
    wrapped = wrap_fetch_error(status_error(500, "line1\nline2" + "x" * 1000), "S", "Q")
    assert wrapped.detail.startswith("HTTP 500: line1 line2")
    assert len(wrapped.detail) <= len("HTTP 500: ") + 500


def test_redact():
    assert redact("a SECRET b", ["SECRET"]) == "a *** b"
    assert redact("abc", ["ab", ""]) == "abc"  # too short to redact safely


# ---------------------------------------------------------------- reporter
@pytest.fixture
def capture():
    logger = logging.getLogger("dota2tracker.tests.reporter")
    logger.setLevel(logging.DEBUG)
    records: list[logging.LogRecord] = []

    class Handler(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Handler()
    logger.addHandler(handler)
    yield logger, records
    logger.removeHandler(handler)


def test_reporter_network_error_levels(capture):
    logger, records = capture
    error = wrap_fetch_error(httpx.ConnectError(f"boom {TOKEN}"), "Stratz", "MatchInfo")
    ErrorReporter(logger, secrets=(TOKEN,)).report(error)
    assert records[-1].levelno == logging.ERROR
    ErrorReporter(logger, suppress_network_errors=True, secrets=(TOKEN,)).report(error)
    assert records[-1].levelno == logging.DEBUG
    assert all(TOKEN not in r.getMessage() for r in records)


def test_reporter_forbidden_graphql_unknown_and_recent(capture):
    logger, records = capture
    reporter = ErrorReporter(logger, secrets=(TOKEN, OD_KEY), capacity=3)
    reporter.report(wrap_fetch_error(status_error(403, f"token {TOKEN}"), "Stratz", "Q"), "watcher")
    assert "stratz_token_banned" in records[-1].getMessage()
    reporter.report(wrap_fetch_error(status_error(403), "OpenDota", "Q"))
    assert "opendota_token_banned" in records[-1].getMessage()
    reporter.report(GraphQLQueryError("failed", [f"bad {OD_KEY}"]))
    reporter.report(RuntimeError(f"weird {TOKEN}"))
    assert "An unexpected error" in records[-1].getMessage()
    recent = reporter.recent()
    assert len(recent) == 3  # capacity
    assert [r["type"] for r in recent] == ["RuntimeError", "GraphQLQueryError", "Forbidden"]
    assert recent[0]["source"] == "plugin"
    dumped = json.dumps(recent) + "".join(r.getMessage() for r in records)
    assert TOKEN not in dumped and OD_KEY not in dumped
    assert reporter.redact(f"x{TOKEN}") == "x***"
    reporter.clear()
    assert reporter.recent() == []


def test_reporter_fetch_match_fail(capture):
    logger, records = capture
    reporter = ErrorReporter(logger)
    both = FetchMatchDataFailError(
        "x",
        stratz_error=wrap_fetch_error(httpx.ReadTimeout("t"), "Stratz", "MatchInfo"),
        opendota_error=wrap_fetch_error(status_error(500), "OpenDota", "/matches/1"),
    )
    reporter.report(both)
    messages = [r.getMessage() for r in records]
    assert messages[0].startswith("Failed to fetch match data from all available sources")
    assert "--- Stratz API Error ---" in messages and "--- OpenDota API Error ---" in messages
    assert [r["source"] for r in reporter.recent()] == ["match", "match"]
    records.clear()
    reporter.report(FetchMatchDataFailError("x", opendota_error=ValueError("v")))
    assert records[0].getMessage().startswith("A data source failed")


# ------------------------------------------------------------------- queue
async def test_request_queue_spacing(monkeypatch):
    now = {"t": 100.0}
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)
        now["t"] += delay

    monkeypatch.setattr(http_module.asyncio, "sleep", fake_sleep)
    queue = RequestQueue(0.2, clock=lambda: now["t"])
    order: list[int] = []

    def make(i):
        async def task():
            order.append(i)
            now["t"] += 0.05  # request duration
            return i

        return task

    results = await asyncio.gather(*(queue.run(make(i)) for i in range(3)))
    assert results == [0, 1, 2] and order == [0, 1, 2]
    assert sleeps == pytest.approx([0.2, 0.2])
    assert queue.pending == 0


async def test_request_queue_failure_still_spaces_and_dispose(monkeypatch):
    now = {"t": 0.0}
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)
        now["t"] += delay

    monkeypatch.setattr(http_module.asyncio, "sleep", fake_sleep)
    queue = RequestQueue(0.2, clock=lambda: now["t"])

    async def fail():
        raise ValueError

    with pytest.raises(ValueError):
        await queue.run(fail)

    async def ok():
        return 1

    assert await queue.run(ok) == 1
    assert sleeps == pytest.approx([0.2])
    queue.dispose()
    assert queue.stopped
    with pytest.raises(PluginStoppedError):
        await queue.run(ok)


def test_build_client_options():
    client = build_client()
    assert DEFAULT_TIMEOUT == 15.0
    assert client.timeout == httpx.Timeout(15.0)
    assert client.follow_redirects
    ipv4 = build_client(force_ipv4=True)
    pool = ipv4._transport._pool  # httpcore pool keeps the bound local address
    assert pool._local_address == "0.0.0.0"
    proxied = build_client(proxy="http://127.0.0.1:1", force_ipv4=True)
    assert proxied._mounts  # proxy wins over IPv4
    assert getattr(proxied._transport._pool, "_local_address", None) is None


# ------------------------------------------------------------------ stratz
def test_load_graphql_collapses_newlines():
    names = {p.stem for p in GRAPHQL_DIR.glob("*.graphql")}
    assert {"MatchInfo", "PlayersLastmatchRankinfo", "RequestMatchDataAnalysis"} <= names
    for name in names:
        text = load_graphql(name)
        assert "\n" not in text and "\r" not in text and text.strip()


def graphql_ok(data):
    return lambda request: httpx.Response(200, json={"data": data})


@pytest.fixture
def no_sleep(monkeypatch):
    async def fake_sleep(_delay):
        return None

    monkeypatch.setattr(http_module.asyncio, "sleep", fake_sleep)


async def test_stratz_query_payload_and_auth(no_sleep, caplog):
    recorder = Recorder(graphql_ok({"match": {"id": 1}}))
    api = StratzAPI(TOKEN, transport=recorder.transport, logger=logging.getLogger("t"))
    with caplog.at_level(logging.DEBUG):
        assert await api.query_match_info(1) == {"match": {"id": 1}}
        assert await api.query_player_extra_info(5, 10) == {"match": {"id": 1}}
    request = recorder.requests[0]
    assert str(request.url) == BASE_URL
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["User-Agent"] == "STRATZ_API"
    body = json.loads(request.content)
    assert body == {"query": load_graphql("MatchInfo"), "variables": {"matchId": 1}}
    # ``heroIds=None`` is dropped like JSON.stringify(undefined)
    assert json.loads(recorder.requests[1].content)["variables"] == {"steamAccountId": 5, "matchCount": 10}
    assert api.request_count == 2 and api.last_success and api.last_error is None
    assert TOKEN not in caplog.text
    await api.aclose()
    assert api.queue.stopped


async def test_stratz_batches_players_by_five(no_sleep):
    def handler(request):
        ids = json.loads(request.content)["variables"]["steamAccountIds"]
        return httpx.Response(200, json={"data": {"players": [{"steamAccountId": i} for i in ids]}})

    recorder = Recorder(handler)
    api = StratzAPI(TOKEN, transport=recorder.transport)
    ids = list(range(1, 13))
    result = await api.query_players_last_match_rank_info(ids)
    assert CHUNK_SIZE == 5
    sent = [json.loads(r.content)["variables"]["steamAccountIds"] for r in recorder.requests]
    assert sent == [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10], [11, 12]]
    assert [p["steamAccountId"] for p in result["players"]] == ids
    recorder.requests.clear()
    await api.query_players_last_match_rank_info([1, 2, 3, 4, 5])
    assert len(recorder.requests) == 1
    await api.aclose()


async def test_stratz_graphql_errors(no_sleep, caplog):
    def handler(request):
        name = json.loads(request.content)["query"]
        if "matchRetry" in name or "RequestMatchDataAnalysis" in name:
            return httpx.Response(200, json={"data": {"stratz": {"matchRetry": True}}})
        return httpx.Response(200, json={"data": {"match": None}, "errors": [{"message": "nope"}, "raw"]})

    api = StratzAPI(TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(GraphQLQueryError) as info:
        await api.query_match_info(1)
    assert info.value.errors == ["nope", "raw"]
    assert classify_error(info.value) == "GraphQLQueryError"
    assert api.last_error == "GraphQLQueryError"
    assert await api.request_parse_match(1) is True
    await api.aclose()


async def test_stratz_partial_errors_with_valid_data_warn(no_sleep):
    warnings: list[str] = []

    class L:
        def warning(self, msg):
            warnings.append(msg)

    api = StratzAPI(
        TOKEN,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"data": {"match": {"id": 1}}, "errors": [{"message": "partial"}]})
        ),
        logger=L(),
        translate=lambda key, params=None: f"{key}:{params['cause']}",
    )
    assert await api.query_match_info(1) == {"match": {"id": 1}}
    assert warnings == ["dota2tracker.logger.stratz_api_query_error:partial"]
    await api.aclose()


@pytest.mark.parametrize(
    ("response", "cls", "error_type"),
    [
        (lambda r: httpx.Response(403, text="banned"), ForbiddenError, "Forbidden"),
        (lambda r: httpx.Response(404), NetworkError, "NotFound"),
        (lambda r: httpx.Response(429), NetworkError, "ClientError"),
        (lambda r: httpx.Response(500), NetworkError, "ServerError"),
    ],
)
async def test_stratz_http_errors(no_sleep, response, cls, error_type):
    api = StratzAPI(TOKEN, transport=httpx.MockTransport(response))
    with pytest.raises(cls) as info:
        await api.query_match_info(1)
    assert info.value.error_type == error_type
    assert TOKEN not in (api.last_error or "") and TOKEN not in str(info.value)
    await api.aclose()


@pytest.mark.parametrize(
    ("exc", "error_type"), [(httpx.ReadTimeout("slow"), "Timeout"), (httpx.ConnectError("down"), "Network")]
)
async def test_stratz_transport_errors(no_sleep, exc, error_type):
    def handler(request):
        raise exc

    api = StratzAPI(TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(NetworkError) as info:
        await api.query_match_info(1)
    assert info.value.error_type == error_type
    await api.aclose()


async def test_stratz_cancel_propagates(no_sleep):
    started = asyncio.Event()

    async def handler(request):
        started.set()
        await asyncio.Event().wait()  # ``asyncio.sleep`` is patched by ``no_sleep``

    api = StratzAPI(TOKEN, transport=httpx.MockTransport(handler))
    task = asyncio.create_task(api.query_match_info(1))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await api.aclose()
    with pytest.raises(PluginStoppedError):
        await api.query_match_info(1)


# ---------------------------------------------------------------- valve
async def test_valve_paths_and_language(no_sleep):
    def handler(request):
        if request.url.path.endswith("/patchnoteslist"):
            return httpx.Response(200, json={"patches": [{"patch_number": "7.36"}, {"patch_number": "7.37"}]})
        return httpx.Response(200, json={"result": {"data": {"heroes": [{"id": 1}]}}})

    recorder = Recorder(handler)
    api = ValveAPI(
        transport=recorder.transport, language_of=lambda locale: "english" if locale == "en-US" else "schinese"
    )
    assert await api.query_hero_details(1, "en-US") == {"id": 1}
    assert await api.query_last_patch_number() == "7.37"
    first = recorder.requests[0].url
    assert first.path == "/datafeed/herodata"
    assert dict(first.params) == {"hero_id": "1", "language": "english"}
    assert "language" not in dict(recorder.requests[1].url.params)
    await api.aclose()


# -------------------------------------------------------------- opendota
async def test_opendota_usage_and_auth(no_sleep, caplog):
    usage: list[tuple[str, int]] = []

    async def record(path, count):
        usage.append((path, count))

    recorder = Recorder(lambda r: httpx.Response(200, json={"match_id": 1, "jobId": 9}))
    api = OpenDotaAPI(api_key=OD_KEY, record_usage=record, transport=recorder.transport)
    with caplog.at_level(logging.DEBUG):
        await api.query_match_info(1)
        await api.request_parse_match(1)
    assert usage == [("/matches/1", 1), ("/request/1", 10)]
    assert [r.method for r in recorder.requests] == ["GET", "POST"]
    assert recorder.requests[0].url.path == "/api/matches/1"
    assert recorder.requests[0].headers["Authorization"] == f"Bearer {OD_KEY}"
    assert OD_KEY not in caplog.text
    await api.aclose()


async def test_opendota_without_key_and_failed_usage(no_sleep):
    async def broken(path, count):
        raise RuntimeError("db down")

    recorder = Recorder(lambda r: httpx.Response(200, json={}))
    api = OpenDotaAPI(record_usage=broken, transport=recorder.transport)
    assert await api.query_match_info(1) == {}  # usage failure never breaks the query
    assert "Authorization" not in recorder.requests[0].headers
    await api.aclose()


async def test_opendota_error_not_counted(no_sleep):
    usage = []

    async def record(path, count):
        usage.append(path)

    api = OpenDotaAPI(record_usage=record, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(NetworkError) as info:
        await api.query_match_info(1)
    assert info.value.service == "OpenDota" and info.value.error_type == "ServerError"
    assert usage == []
    await api.aclose()


def test_opendota_ipv4_option():
    api = OpenDotaAPI(force_ipv4=True)
    assert api._client._transport._pool._local_address == "0.0.0.0"
    with_proxy = OpenDotaAPI(force_ipv4=True, proxy="http://127.0.0.1:1")
    assert getattr(with_proxy._client._transport._pool, "_local_address", None) is None
