from __future__ import annotations

import json

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
            httpx.Response(401, text="401 invalid api key"),
            {"status_code": 401, "title": "", "request_id": ""},
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
