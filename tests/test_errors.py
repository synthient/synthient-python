from __future__ import annotations

import json
from contextlib import closing

import httpx
import pytest

from synthient import APIConnectionError, APIError, SynthientError
from synthient._base import MAX_RESPONSE_SIZE
from synthient._errors import api_error
from tests.conftest import ChunkStream, MakeClient

REQUEST_ID = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            httpx.Response(
                402,
                json={
                    "type": "https://docs.synthient.com/errors/credits",
                    "title": "Payment Required",
                    "status": 402,
                    "detail": "out of lookup credits",
                    "instance": "ignored-when-header-present",
                },
                headers={"x-request-id": REQUEST_ID},
            ),
            {
                "status_code": 402,
                "type": "https://docs.synthient.com/errors/credits",
                "title": "Payment Required",
                "detail": "out of lookup credits",
                "request_id": REQUEST_ID,
            },
        ),
        (
            httpx.Response(500, json={"title": "Internal Server Error", "instance": REQUEST_ID}),
            {"status_code": 500, "title": "Internal Server Error", "request_id": REQUEST_ID},
        ),
        (
            httpx.Response(
                422,
                json={"title": "Unprocessable Entity", "errors": {"ip_address": ["must be an IP"]}},
            ),
            {"status_code": 422, "errors": {"ip_address": ["must be an IP"]}},
        ),
        (
            httpx.Response(400, json={"title": "Bad Request", "errors": ["cursor"]}),
            {"status_code": 400, "title": "Bad Request", "errors": {}},
        ),
        (
            httpx.Response(400, json={"title": None, "detail": 7}),
            {"status_code": 400, "title": "", "detail": ""},
        ),
        (
            httpx.Response(400, json={"type": 7, "title": "Bad Request", "detail": "bad cursor"}),
            {"status_code": 400, "type": "", "title": "Bad Request", "detail": "bad cursor"},
        ),
        (
            httpx.Response(422, json={"title": "Unprocessable Entity", "errors": {"ip": [7]}}),
            {"status_code": 422, "title": "Unprocessable Entity", "errors": {}},
        ),
        (
            httpx.Response(401, text="401 invalid api key\n"),
            {"status_code": 401, "title": "", "detail": "401 invalid api key", "request_id": ""},
        ),
        (
            httpx.Response(
                502, content=b"<html>Bad Gateway</html>", headers={"content-type": "text/html"}
            ),
            {"status_code": 502, "title": "", "detail": ""},
        ),
        (
            httpx.Response(502, content=b"[" * 30_000 + b"]" * 30_000),
            {"status_code": 502, "detail": ""},
        ),
        (
            httpx.Response(429, headers={"retry-after": "7"}),
            {"status_code": 429, "retry_after": 7.0},
        ),
        (
            httpx.Response(502, text="<html>" + "x" * 100_000 + "</html>"),
            {"status_code": 502, "title": "", "retry_after": None},
        ),
    ],
)
def test_api_error_parsing(response: httpx.Response, expected: dict[str, object]) -> None:
    error = api_error(response, response.content[: 64 << 10])
    for name, value in expected.items():
        assert getattr(error, name) == value, name


def test_error_string_excludes_server_data(make_client: MakeClient) -> None:
    secret = "sk_live_secret"
    client = make_client(
        lambda _: httpx.Response(
            403,
            json={"title": f"Forbidden\n{secret}", "detail": f"key {secret} lacks scope"},
            headers={"x-request-id": secret},
        )
    )
    with pytest.raises(APIError) as caught:
        client.account()
    assert str(caught.value) == "403 Forbidden"
    assert secret not in repr(caught.value)
    assert caught.value.detail == f"key {secret} lacks scope"


def test_unknown_status_string() -> None:
    assert str(APIError(status_code=599)) == "599"


def failing_stream() -> ChunkStream:
    return ChunkStream([b'{"title": "Forb'], httpx.ReadTimeout("stalled"))


def test_error_body_read_failure_keeps_status(make_client: MakeClient) -> None:
    client = make_client(
        lambda _: httpx.Response(
            403, stream=failing_stream(), headers={"x-request-id": REQUEST_ID}
        ),
        max_retries=0,
    )
    with pytest.raises(APIError) as caught:
        client.account()
    assert caught.value.status_code == 403
    assert caught.value.request_id == REQUEST_ID
    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)


def test_success_body_read_failure(make_client: MakeClient) -> None:
    client = make_client(lambda _: httpx.Response(200, stream=failing_stream()))
    with pytest.raises(APIConnectionError) as caught:
        client.account()
    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b'{"stream": "proxies", "feeds": []} {}', "invalid ExportPage"),
        (b'{"stream": "proxies", "feeds": [', "invalid ExportPage"),
        (b"", "invalid ExportPage"),
        (b"<html>maintenance</html>", "invalid ExportPage"),
    ],
)
def test_invalid_success_body(make_client: MakeClient, body: bytes, message: str) -> None:
    client = make_client(lambda _: httpx.Response(200, content=body))
    with pytest.raises(SynthientError, match=message) as caught:
        client.proxies.list_exports()
    assert not isinstance(caught.value, APIError)


def test_oversized_success_body(make_client: MakeClient) -> None:
    padding = " " * MAX_RESPONSE_SIZE
    body = json.dumps({"stream": "proxies", "feeds": []}).encode() + padding.encode()
    client = make_client(lambda _: httpx.Response(200, content=body))
    with pytest.raises(SynthientError, match="response body exceeds") as caught:
        client.proxies.list_exports()
    assert not isinstance(caught.value, APIError)


def test_response_buffer_is_bounded(make_client: MakeClient) -> None:
    body = ChunkStream([b"a" * 100, b"b" * 100])
    client = make_client(lambda _: httpx.Response(200, stream=body))
    with closing(httpx.Response(200, stream=body)) as response:
        assert client._transport.read(response, 8) == b"a" * 9
    assert body.sent == 1
    assert body.closed


@pytest.mark.parametrize("status", [200, 403])
def test_invalid_compression(make_client: MakeClient, status: int) -> None:
    body = ChunkStream([b"invalid gzip"])
    client = make_client(
        lambda _: httpx.Response(status, stream=body, headers={"content-encoding": "gzip"})
    )
    with pytest.raises(SynthientError) as caught:
        client.account()
    if status == 403:
        assert isinstance(caught.value, APIError)
        assert caught.value.status_code == status
    else:
        assert str(caught.value) == "invalid response encoding"
    assert isinstance(caught.value.__cause__, httpx.DecodingError)
    assert body.closed
