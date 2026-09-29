from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from synthient import APIConnectionError, APIError, Client, ResponseMetadata, SynthientError
from tests.conftest import PRESIGNED, MakeClient, redirect

PARQUET = b"PAR1 parquet bytes PAR1"


def storage_client(
    make_client: MakeClient,
    storage: Callable[[httpx.Request], httpx.Response],
    **options: object,
) -> tuple[Client, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.test":
            return redirect(PRESIGNED)
        return storage(request)

    return make_client(handler, **options), requests


def test_download(make_client: MakeClient) -> None:
    hooks: list[ResponseMetadata] = []
    client, requests = storage_client(
        make_client, lambda _: httpx.Response(200, content=PARQUET), response_hook=hooks.append
    )

    with client.proxies.download("2026-05-06") as response:
        assert b"".join(response.iter_bytes()) == PARQUET

    api, storage = requests
    assert api.url.path == "/api/v4/feeds/proxies/export/2026-05-06"
    assert api.headers["x-api-key"] == "test-key"
    assert str(storage.url) == PRESIGNED
    assert "x-api-key" not in storage.headers
    assert storage.headers["user-agent"].startswith("synthient-python/")
    assert [hook.status_code for hook in hooks] == [307]


def test_download_url(make_client: MakeClient) -> None:
    client, _ = storage_client(make_client, lambda _: httpx.Response(500))
    assert client.proxies.download_url("latest") == PRESIGNED


def test_download_url_relative_location(make_client: MakeClient) -> None:
    client = make_client(lambda _: redirect("/files/export.parquet"))
    assert client.anonymizers.download_url("latest") == "https://api.test/files/export.parquet"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(200, json={}), "expected a redirect to the export, got status 200"),
        (httpx.Response(307), "export redirect has no valid location"),
        (redirect("http://storage.test/file"), "export redirect does not use https"),
        (redirect("ftp://storage.test/file"), "export redirect does not use https"),
    ],
)
def test_download_url_invalid_redirect(
    make_client: MakeClient, response: httpx.Response, message: str
) -> None:
    client = make_client(lambda _: response)
    with pytest.raises(SynthientError, match=message):
        client.proxies.download_url("latest")


def test_download_url_http_base_allows_http(make_client: MakeClient) -> None:
    client = make_client(
        lambda _: redirect("http://localhost:9000/file"), base_url="http://localhost:8080/api/v4"
    )
    assert client.proxies.download_url("latest") == "http://localhost:9000/file"


def test_daily_export_in_progress(make_client: MakeClient) -> None:
    client = make_client(
        lambda _: httpx.Response(404, json={"title": "Not Found"}, headers={"retry-after": "60"})
    )
    with pytest.raises(APIError) as caught:
        client.proxies.download_url("2026-05-06")
    assert caught.value.status_code == 404
    assert caught.value.retry_after == 60


def test_storage_redirects(make_client: MakeClient) -> None:
    def storage(request: httpx.Request) -> httpx.Response:
        if request.url.host == "storage.test":
            return redirect("https://cdn.test/file?sig=other", 302)
        return httpx.Response(200, content=PARQUET)

    client, requests = storage_client(make_client, storage)
    with client.proxies.download("latest") as response:
        assert response.read() == PARQUET
    assert [request.url.host for request in requests] == ["api.test", "storage.test", "cdn.test"]
    assert all("x-api-key" not in request.headers for request in requests[1:])


@pytest.mark.parametrize(
    ("location", "message"),
    [
        ("http://cdn.test/file", "export redirect does not use https"),
        ("file:///etc/passwd", "export redirect has an unsupported scheme"),
    ],
)
def test_storage_redirect_rejected(make_client: MakeClient, location: str, message: str) -> None:
    client, requests = storage_client(make_client, lambda _: redirect(location, 302))
    with pytest.raises(SynthientError, match=message) as caught, client.proxies.download("latest"):
        pass
    assert "secret-signature" not in str(caught.value)
    assert len(requests) == 2


def test_too_many_storage_redirects(make_client: MakeClient) -> None:
    client, requests = storage_client(
        make_client, lambda r: redirect(str(r.url.copy_with(path=r.url.path + "x")), 302)
    )
    with (
        pytest.raises(SynthientError, match="too many export redirects"),
        client.proxies.download("latest"),
    ):
        pass
    assert len(requests) == 12


def test_storage_error(make_client: MakeClient) -> None:
    client, _ = storage_client(
        make_client,
        lambda _: httpx.Response(403, text="<Error><Code>AccessDenied</Code></Error>"),
    )
    with pytest.raises(APIError) as caught, client.proxies.download("latest"):
        pass
    assert caught.value.status_code == 403
    assert "secret-signature" not in str(caught.value)


def test_storage_unreachable(make_client: MakeClient) -> None:
    def storage(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client, requests = storage_client(make_client, storage)
    with pytest.raises(APIConnectionError) as caught, client.proxies.download("latest"):
        pass
    assert "secret-signature" not in str(caught.value)
    assert "secret-signature" not in str(caught.value.__cause__)
    assert len(requests) == 2
