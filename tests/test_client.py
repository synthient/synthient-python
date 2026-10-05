from __future__ import annotations

from importlib.metadata import version

import httpx
import pytest

import synthient
from synthient import APIError, Client
from tests.conftest import API_KEY, BASE_URL, PRESIGNED, MakeClient, json_response, redirect


def test_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYNTHIENT_API_KEY", raising=False)
    with pytest.raises(ValueError, match="missing API key"):
        Client()
    with pytest.raises(ValueError, match="missing API key"):
        Client("  \t")


def test_api_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNTHIENT_API_KEY", " env-key ")
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-api-key"))
        return json_response(200, {"status": "ok"})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http,
        Client(http_client=http) as from_env,
        Client("explicit", http_client=http) as explicit,
    ):
        from_env.health()
        explicit.health()
    assert seen == ["env-key", "explicit"]


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://api.synthient.com",
        "https://",
        "api.synthient.com/api/v4",
        "https://api.synthient.com/api/v4?debug=1",
        "https://api.synthient.com/api/v4#top",
        "https://api.synthient.com/%zz",
    ],
)
def test_invalid_base_url(base_url: str) -> None:
    with pytest.raises(ValueError, match="invalid base URL"):
        Client(API_KEY, base_url=base_url)


@pytest.mark.parametrize("max_retries", [-1, True, 1.5])
def test_invalid_max_retries(max_retries: int) -> None:
    with pytest.raises(ValueError, match="max_retries"):
        Client(API_KEY, max_retries=max_retries)


def test_request_headers(make_client: MakeClient) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return json_response(200, {"status": "ok"})

    make_client(handler).health()
    make_client(handler, user_agent="my-app/1.2", base_url=f"{BASE_URL}/").health()

    first, second = requests
    assert str(first.url) == f"{BASE_URL}/health"
    assert first.headers["x-api-key"] == API_KEY
    assert first.headers["accept"] == "application/json"
    assert "content-type" not in first.headers
    assert first.headers["user-agent"] == f"synthient-python/{version('synthient')}"
    assert second.headers["user-agent"] == f"synthient-python/{version('synthient')} my-app/1.2"
    assert str(second.url) == f"{BASE_URL}/health"


def test_timeout_applies_to_every_request(make_client: MakeClient) -> None:
    timeouts: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"])
        return json_response(200, {"status": "ok"})

    make_client(handler).health()
    make_client(handler, timeout=5).health()
    assert timeouts == [
        {"connect": 10.0, "read": 60.0, "write": 60.0, "pool": 60.0},
        {"connect": 5, "read": 5, "write": 5, "pool": 5},
    ]


@pytest.mark.parametrize(
    "timeout",
    [-1, True, float("nan"), float("inf"), httpx.Timeout(1, read=-1)],
)
def test_invalid_timeout(timeout: float | httpx.Timeout) -> None:
    with pytest.raises(ValueError, match="timeout values"):
        Client(API_KEY, timeout=timeout)


def test_timeout_configuration_is_copied(make_client: MakeClient) -> None:
    timeout = httpx.Timeout(5)
    timeouts: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"])
        return json_response(200, {})

    client = make_client(handler, timeout=timeout)
    timeout.read = 10
    client.health()
    assert timeouts == [{"connect": 5, "read": 5, "write": 5, "pool": 5}]


def test_borrowed_http_client_is_not_closed() -> None:
    http = httpx.Client(transport=httpx.MockTransport(lambda _: json_response(200, {})))
    with Client(API_KEY, http_client=http) as client:
        client.health()
    assert not http.is_closed
    http.close()


def test_api_redirects_override_http_client_default() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.test":
            return redirect(PRESIGNED)
        return json_response(200, {})

    with (
        httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as http,
        Client(API_KEY, base_url=BASE_URL, http_client=http) as client,
        pytest.raises(APIError) as caught,
    ):
        client.health()
    assert caught.value.status_code == 307
    assert len(requests) == 1


def test_api_key_is_not_in_repr() -> None:
    client = Client("sk_live_secret")
    assert "sk_live_secret" not in repr(client._transport.config)
    client.close()


def test_owned_http_client_closes_once() -> None:
    client = Client(API_KEY)
    client.close()
    client.close()
    with pytest.raises(RuntimeError, match="closed"):
        client.health()


def test_public_exports() -> None:
    assert synthient.__version__ == version("synthient")
    for name in synthient.__all__:
        assert hasattr(synthient, name), name
