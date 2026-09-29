from __future__ import annotations

import ssl
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler

import httpx
import pytest

from synthient import APIConnectionError, APIError, APITimeoutError, Client
from synthient._retry import backoff, is_transient, parse_retry_after
from tests.conftest import (
    API_KEY,
    IP_INFO,
    MakeClient,
    ServerHandler,
    json_response,
    problem,
    write_json,
)

CALLS: dict[str, tuple[Callable[[Client], object], object]] = {
    "free": (lambda client: client.proxies.list_exports(), {"stream": "proxies", "feeds": []}),
    "billed": (lambda client: client.lookup_ip("8.8.8.8"), IP_INFO),
}


def sequence(
    *responses: httpx.Response | Exception,
) -> tuple[list[httpx.Request], Callable[[httpx.Request], httpx.Response]]:
    requests: list[httpx.Request] = []
    remaining = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        response = next(remaining)
        if isinstance(response, Exception):
            raise response
        return response

    return requests, handler


@pytest.mark.parametrize(
    ("kind", "statuses", "final"),
    [
        ("free", [503, 502, 200], 200),
        ("free", [500, 504, 429], 429),
        ("free", [400], 400),
        ("free", [404], 404),
        ("billed", [429, 429, 200], 200),
        ("billed", [500], 500),
        ("billed", [503], 503),
        ("billed", [402], 402),
    ],
)
def test_retry_policy(
    make_client: MakeClient,
    sleeps: list[float],
    kind: str,
    statuses: list[int],
    final: int,
) -> None:
    call, body = CALLS[kind]
    requests, handler = sequence(
        *(json_response(200, body) if status == 200 else problem(status) for status in statuses)
    )
    client = make_client(handler)
    if final == 200:
        call(client)
    else:
        with pytest.raises(APIError) as caught:
            call(client)
        assert caught.value.status_code == final
    assert len(requests) == len(statuses)
    assert len(sleeps) == len(statuses) - 1


def test_retry_after_is_honored(make_client: MakeClient, sleeps: list[float]) -> None:
    requests, handler = sequence(
        httpx.Response(429, headers={"retry-after": "3"}), json_response(200, IP_INFO)
    )
    make_client(handler).lookup_ip("8.8.8.8")
    assert len(requests) == 2
    assert sleeps[0] >= 3


def test_long_retry_after_stops(make_client: MakeClient, sleeps: list[float]) -> None:
    requests, handler = sequence(httpx.Response(429, headers={"retry-after": "120"}))
    with pytest.raises(APIError) as caught:
        make_client(handler).lookup_ip("8.8.8.8")
    assert caught.value.retry_after == 120
    assert len(requests) == 1
    assert sleeps == []


def test_zero_retries(make_client: MakeClient, sleeps: list[float]) -> None:
    requests, handler = sequence(problem(503))
    with pytest.raises(APIError):
        make_client(handler, max_retries=0).account()
    assert len(requests) == 1
    assert sleeps == []


def test_free_requests_retry_transport_errors(make_client: MakeClient) -> None:
    requests, handler = sequence(
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
        json_response(200, {"stream": "proxies", "feeds": []}),
    )
    make_client(handler).proxies.list_exports()
    assert len(requests) == 3


def test_free_requests_give_up_on_transport_errors(make_client: MakeClient) -> None:
    requests, handler = sequence(*(httpx.ReadTimeout("slow") for _ in range(3)))
    with pytest.raises(APITimeoutError) as caught:
        make_client(handler).account()
    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)
    assert len(requests) == 3


def test_billed_requests_do_not_retry_transport_errors(make_client: MakeClient) -> None:
    requests, handler = sequence(httpx.RemoteProtocolError("disconnected"))
    with pytest.raises(APIConnectionError):
        make_client(handler).lookup_ips(["8.8.8.8"])
    assert len(requests) == 1


def test_certificate_errors_are_not_retried(make_client: MakeClient) -> None:
    error = httpx.ConnectError("certificate verify failed")
    error.__cause__ = ssl.SSLCertVerificationError("certificate verify failed")
    requests, handler = sequence(error)
    with pytest.raises(APIConnectionError):
        make_client(handler).account()
    assert len(requests) == 1


def test_billed_request_is_not_replayed_on_reused_connection(
    serve: Callable[[ServerHandler], str],
) -> None:
    ports: list[int] = []
    lookups: list[str] = []

    def handle(handler: BaseHTTPRequestHandler) -> None:
        ports.append(handler.client_address[1])
        if handler.path.endswith("/health"):
            write_json(handler, 200, {"status": "ok"})
            return
        lookups.append(handler.path)
        handler.close_connection = True

    with Client(API_KEY, base_url=serve(handle)) as client:
        client.health()
        with pytest.raises(APIConnectionError):
            client.lookup_ip("8.8.8.8")
    assert lookups == ["/api/v4/lookup/ip/8.8.8.8"]
    assert ports[0] == ports[1]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("3", 3.0),
        (" 3 ", 3.0),
        ("0", 0.0),
        ("-3", 0.0),
        ("99999999999", 2.0**31),
        ("1.5", None),
        ("soon", None),
        ("", None),
        (format_datetime(datetime(2000, 1, 1, tzinfo=UTC), usegmt=True), 0.0),
    ],
)
def test_parse_retry_after(header: str | None, expected: float | None) -> None:
    assert parse_retry_after(header) == expected


def test_parse_retry_after_http_date() -> None:
    header = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    parsed = parse_retry_after(header)
    assert parsed is not None
    assert 28 <= parsed <= 30


@pytest.mark.parametrize("attempt", range(8))
def test_backoff_bounds(attempt: int) -> None:
    base = min(2.0 ** min(attempt, 5), 30.0)
    for _ in range(50):
        assert 0.75 * base <= backoff(attempt) <= 1.25 * base


def test_is_transient() -> None:
    assert is_transient(httpx.ConnectError("refused"))
    assert is_transient(httpx.PoolTimeout("busy"))
    assert is_transient(httpx.RemoteProtocolError("disconnected"))
    assert not is_transient(httpx.UnsupportedProtocol("gopher"))
    assert not is_transient(httpx.ProxyError("denied"))
