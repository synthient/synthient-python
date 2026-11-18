from __future__ import annotations

import gzip
from datetime import UTC, date, datetime

import httpx
import pytest

from synthient import APIError, Client
from tests.conftest import EXPORT, PRESIGNED, MakeClient, json_response, problem, redirect

META = {
    "id": "2026-11-17",
    "stream": "geolocation",
    "kind": "daily",
    "format": "mmdb",
    "date": 1794873600,
    "rows": 27456467,
    "size": 1874587084,
    "checksum": "ae71",
    "created_at": 1794960045,
    "schema": {"fields": [{"name": "network", "type": "string"}]},
    "formats": [
        {"format": "parquet", "size_bytes": 466431938, "checksum": "9712"},
        {"format": "mmdb", "size_bytes": 1874587084, "checksum": "ae71"},
    ],
}

CSV_GZ = gzip.compress(b"network,city\n203.0.113.0/24,Mountain View\n")


def dataset_client(make_client: MakeClient) -> tuple[Client, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "storage.test":
            return httpx.Response(200, content=CSV_GZ, headers={"content-type": "application/gzip"})
        if request.url.path.endswith("/meta"):
            return json_response(200, META)
        if request.url.path.endswith("/daily"):
            return json_response(200, {"stream": "geolocation", "days": []})
        if request.url.path.endswith("/export"):
            return json_response(200, {"stream": "geolocation", "feeds": [EXPORT]})
        return redirect(PRESIGNED)

    return make_client(handler), requests


@pytest.mark.parametrize(
    ("dataset", "prefix"), [("geolocation", "geolocation"), ("whois", "whois")]
)
def test_dataset_routes(make_client: MakeClient, dataset: str, prefix: str) -> None:
    client, requests = dataset_client(make_client)
    exports = getattr(client, dataset)
    exports.list_exports(day="2026-11-17")
    exports.export_volume(days=30)
    exports.export_meta("latest")
    exports.download_url(date(2026, 11, 17))

    base = f"/api/v4/feeds/{prefix}/export"
    assert [(request.url.path, dict(request.url.params)) for request in requests] == [
        (base, {"day": "2026-11-17"}),
        (f"{base}/daily", {"days": "30"}),
        (f"{base}/latest/meta", {}),
        (f"{base}/2026-11-17", {}),
    ]


def test_dataset_format(make_client: MakeClient) -> None:
    client, requests = dataset_client(make_client)
    meta = client.geolocation.export_meta("2026-11-17", format="mmdb")
    client.geolocation.download_url("2026-11-17", format="csv")
    with client.geolocation.download("latest", format="csv") as response:
        body = response.read()
    client.whois.download_url("latest", format="tar.gz")

    assert meta.format == "mmdb"
    assert [file.format for file in meta.formats] == ["parquet", "mmdb"]
    api = [request.url for request in requests if request.url.host == "api.test"]
    assert [dict(url.params) for url in api] == [
        {"format": "mmdb"},
        {"format": "csv"},
        {"format": "csv"},
        {"format": "tar.gz"},
    ]
    assert body == CSV_GZ


@pytest.mark.parametrize("export", ["2026-11-17/3", datetime(2026, 11, 17, 3, tzinfo=UTC)])
def test_dataset_rejects_hourly_ids(make_client: MakeClient, export: str | date) -> None:
    client, requests = dataset_client(make_client)
    with pytest.raises(ValueError, match=r"invalid export ID .* only daily exports"):
        client.geolocation.export_meta(export)
    with pytest.raises(ValueError, match=r"invalid export ID .* only daily exports"):
        client.whois.download_url(export)
    with pytest.raises(ValueError, match="invalid export ID"), client.geolocation.download(export):
        pass
    assert requests == []


def test_dataset_download_keeps_key_from_storage(make_client: MakeClient) -> None:
    client, requests = dataset_client(make_client)
    with client.whois.download("latest") as response:
        assert response.read() == CSV_GZ
    api, storage = requests
    assert api.headers["x-api-key"] == "test-key"
    assert str(storage.url) == PRESIGNED
    assert "x-api-key" not in storage.headers


def test_dataset_iter_exports_keeps_day(make_client: MakeClient) -> None:
    params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params.append(dict(request.url.params))
        if "cursor" in request.url.params:
            return json_response(200, {"stream": "whois", "feeds": []})
        return json_response(200, {"stream": "whois", "feeds": [EXPORT], "next_cursor": "p2"})

    exports = list(make_client(handler).whois.iter_exports(day=date(2026, 11, 17)))
    assert len(exports) == 1
    assert params == [{"day": "2026-11-17"}, {"day": "2026-11-17", "cursor": "p2"}]


def test_dataset_missing_format(make_client: MakeClient) -> None:
    client = make_client(
        lambda _: problem(404, detail="This snapshot has no csv file; available: tar.gz")
    )
    with pytest.raises(APIError) as caught:
        client.whois.download_url("latest", format="csv")
    assert caught.value.status_code == 404
