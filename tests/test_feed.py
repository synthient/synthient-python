from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone

import httpx
import pytest

from synthient import APIError, SynthientError
from synthient._base import export_path
from tests.conftest import EXPORT, PRESIGNED, MakeClient, json_response, problem, redirect

FEEDS = {
    "proxies": "/api/v4/feeds/proxies",
    "anonymizers": "/api/v4/feeds/anonymizers",
    "torrents": "/api/v4/feeds/torrents",
    "ja4t": "/api/v4/feeds/ja4t",
    "helios_http": "/api/v4/feeds/helios/http",
    "helios_https": "/api/v4/feeds/helios/https",
    "helios_dns": "/api/v4/feeds/helios/dns",
    "helios_adb": "/api/v4/feeds/helios/adb",
}

META = {
    "id": "2026-05-03/7",
    "stream": "proxies",
    "kind": "hourly",
    "format": "parquet",
    "date": 1777791600,
    "hour": 7,
    "rows": 3,
    "size": 9,
    "checksum": "ab12",
    "created_at": 1777795200,
    "schema": {"fields": [{"name": "ip", "type": "string"}]},
    "formats": [{"format": "parquet", "size_bytes": 9, "checksum": "ab12"}],
}

VOLUME = {
    "stream": "proxies",
    "days": [
        {
            "date": "2026-11-17",
            "kind": "daily",
            "snapshots": 1,
            "row_count": 1528344568,
            "size_bytes": 20640294576,
        },
        {
            "date": "2026-11-18",
            "kind": "hourly",
            "snapshots": 3,
            "row_count": 64417250,
            "size_bytes": 880000000,
        },
    ],
}

EST = timezone(timedelta(hours=-5))


@pytest.mark.parametrize(
    ("export", "path"),
    [
        ("latest", "latest"),
        ("2026-05-03", "2026-05-03"),
        ("2026-05-03/07", "2026-05-03/7"),
        ("2026-05-03/7", "2026-05-03/7"),
        ("2026-05-03/23", "2026-05-03/23"),
        (date(2026, 5, 3), "2026-05-03"),
        (datetime(2026, 5, 3, 22, 30, tzinfo=EST), "2026-05-04/3"),
        (datetime(2026, 5, 3, 7, tzinfo=UTC), "2026-05-03/7"),
    ],
)
def test_export_path(export: str | date, path: str) -> None:
    assert export_path(export) == path


@pytest.mark.parametrize(
    "export",
    [
        "",
        "latest/3",
        "2026-5-3",
        "2026-02-30",
        "2026-05-03/24",
        "2026-05-03/",
        "2026-05-03/007",
        "../account/me",
        "2026-05-03/07/meta",
        "２０２６-05-03",  # noqa: RUF001
    ],
)
def test_invalid_export_path(export: str) -> None:
    with pytest.raises(ValueError, match="invalid export ID"):
        export_path(export)


def test_naive_datetime_export() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        export_path(datetime(2026, 5, 3, 7))  # noqa: DTZ001


@pytest.mark.parametrize(("feed", "prefix"), FEEDS.items())
def test_feed_routes(make_client: MakeClient, feed: str, prefix: str) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/daily"):
            return json_response(200, VOLUME)
        return json_response(200, META)

    client = make_client(handler)
    getattr(client, feed).export_meta("latest")
    getattr(client, feed).export_meta("2026-05-03/07")
    getattr(client, feed).export_volume()
    assert paths == [
        f"{prefix}/export/latest/meta",
        f"{prefix}/export/2026-05-03/7/meta",
        f"{prefix}/export/daily",
    ]


def test_export_meta(make_client: MakeClient) -> None:
    meta = make_client(lambda _: json_response(200, META)).proxies.export_meta("2026-05-03/7")
    assert meta.hour == 7
    assert meta.date == datetime(2026, 5, 3, 7, tzinfo=UTC)
    assert meta.parquet_schema.fields[0].type == "string"
    assert meta.formats[0].format == "parquet"
    assert meta.variant is None


def test_invalid_export_sends_nothing(make_client: MakeClient) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return json_response(200, META)

    client = make_client(handler)
    with pytest.raises(ValueError, match="invalid export ID"):
        client.proxies.export_meta("../account/me")
    with pytest.raises(ValueError, match="invalid export ID"):
        client.proxies.download_url("2026-05-03/24")
    assert requests == []


def test_list_exports(make_client: MakeClient) -> None:
    urls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(request.url)
        return json_response(200, {"stream": "proxies", "feeds": [EXPORT], "next_cursor": "c2"})

    client = make_client(handler)
    page = client.proxies.list_exports(limit=2, cursor="c1")
    client.proxies.list_exports()

    assert urls[0].path == "/api/v4/feeds/proxies/export"
    assert dict(urls[0].params) == {"limit": "2", "cursor": "c1"}
    assert not urls[1].params
    assert page.stream == "proxies"
    assert page.next_cursor == "c2"
    export = page.exports[0]
    assert export.date == date(2026, 5, 7)
    assert export.hour == 21
    assert export.created_at == datetime(2026, 5, 7, 21, tzinfo=UTC)


def test_list_exports_day_and_variant(make_client: MakeClient) -> None:
    urls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(request.url)
        return json_response(200, {"stream": "proxies", "feeds": []})

    client = make_client(handler)
    client.proxies.list_exports(day="2026-11-17", variant="lite")
    client.proxies.list_exports(day=date(2026, 11, 17))
    client.anonymizers.list_exports(variant="full")
    client.ja4t.list_exports(cursor="")

    assert [dict(url.params) for url in urls] == [
        {"day": "2026-11-17", "variant": "lite"},
        {"day": "2026-11-17"},
        {"variant": "full"},
        {},
    ]


@pytest.mark.parametrize(
    "day",
    [
        "latest",
        "2026-11-17/3",
        "2026-11-31",
        "20261117",
        datetime(2026, 11, 17, tzinfo=UTC),
        datetime(2026, 11, 17),  # noqa: DTZ001
    ],
)
def test_invalid_day_sends_nothing(make_client: MakeClient, day: str | date) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return json_response(200, {"stream": "proxies", "feeds": []})

    client = make_client(handler)
    with pytest.raises(ValueError, match="invalid day"):
        client.proxies.list_exports(day=day)
    with pytest.raises(ValueError, match="invalid day"):
        client.proxies.iter_exports(day=day)
    assert requests == []


@pytest.mark.parametrize("export", ["2026-11-17", "2026-11-17/3"])
def test_variant(make_client: MakeClient, export: str) -> None:
    urls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(request.url)
        if request.url.host == "storage.test":
            return httpx.Response(200, content=b"PAR1")
        if request.url.path.endswith("/meta"):
            return json_response(200, {**META, "variant": "lite"})
        return redirect(PRESIGNED)

    client = make_client(handler)
    assert client.proxies.export_meta(export, variant="lite").variant == "lite"
    client.proxies.download_url(export, variant="lite")
    with client.proxies.download(export, variant="lite") as response:
        response.read()
    client.proxies.download_url(export)

    api = [url for url in urls if url.host == "api.test"]
    assert [url.path.removeprefix("/api/v4/feeds/proxies/export/") for url in api] == [
        f"{export}/meta",
        export,
        export,
        export,
    ]
    assert [dict(url.params) for url in api] == [{"variant": "lite"}] * 3 + [{}]


def test_lite_not_published(make_client: MakeClient) -> None:
    client = make_client(lambda _: problem(404, title="Not Found"))
    with pytest.raises(APIError) as caught:
        client.proxies.download_url("latest", variant="lite")
    assert caught.value.status_code == 404


def test_export_volume(make_client: MakeClient) -> None:
    urls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(request.url)
        return json_response(200, VOLUME)

    client = make_client(handler)
    volume = client.proxies.export_volume(days=7)
    client.torrents.export_volume()

    assert urls[0].path == "/api/v4/feeds/proxies/export/daily"
    assert dict(urls[0].params) == {"days": "7"}
    assert urls[1].path == "/api/v4/feeds/torrents/export/daily"
    assert not urls[1].params
    assert volume.stream == "proxies"
    daily, hourly = volume.days
    assert daily.date == date(2026, 11, 17)
    assert daily.kind == "daily"
    assert daily.size_bytes == 20640294576
    assert daily.row_count == 1528344568
    assert hourly.kind == "hourly"
    assert hourly.snapshots == 3


@pytest.mark.parametrize("days", [0, -1, True])
def test_invalid_export_volume_days(make_client: MakeClient, days: int) -> None:
    requests: list[httpx.Request] = []
    client = make_client(lambda request: requests.append(request) or json_response(200, VOLUME))
    with pytest.raises(ValueError, match="days must be a positive integer"):
        client.proxies.export_volume(days=days)
    assert requests == []


def paged(
    pages: dict[str | None, httpx.Response],
) -> tuple[list[str | None], Callable[[httpx.Request], httpx.Response]]:
    cursors: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        cursors.append(cursor)
        return pages[cursor]

    return cursors, handler


def page(ids: list[str], next_cursor: str | None = None) -> httpx.Response:
    exports = [{**EXPORT, "id": export_id} for export_id in ids]
    return json_response(200, {"stream": "proxies", "feeds": exports, "next_cursor": next_cursor})


def test_iter_exports(make_client: MakeClient) -> None:
    cursors, handler = paged(
        {
            None: page(["2026-05-07/21", "2026-05-07/20"], "p2"),
            "p2": page([], "p3"),
            "p3": page(["2026-05-06"]),
        }
    )
    exports = list(make_client(handler).proxies.iter_exports(limit=2))
    assert [export.id for export in exports] == ["2026-05-07/21", "2026-05-07/20", "2026-05-06"]
    assert cursors == [None, "p2", "p3"]


def test_iter_exports_keeps_filters(make_client: MakeClient) -> None:
    params: list[dict[str, str]] = []
    pages = {None: page(["a"], "p2"), "p2": page(["b"])}

    def handler(request: httpx.Request) -> httpx.Response:
        params.append(dict(request.url.params))
        return pages[request.url.params.get("cursor")]

    client = make_client(handler)
    exports = list(client.proxies.iter_exports(limit=1, day="2026-11-17", variant="lite"))
    assert [export.id for export in exports] == ["a", "b"]
    assert params == [
        {"limit": "1", "day": "2026-11-17", "variant": "lite"},
        {"limit": "1", "day": "2026-11-17", "variant": "lite", "cursor": "p2"},
    ]


def test_iter_exports_is_lazy(make_client: MakeClient) -> None:
    cursors, handler = paged({None: page(["a", "b"], "p2"), "p2": page(["c"])})
    exports = make_client(handler).proxies.iter_exports()
    assert cursors == []
    for export in exports:
        assert export.id == "a"
        break
    assert cursors == [None]


def test_iter_exports_repeated_cursor(make_client: MakeClient) -> None:
    _, handler = paged({None: page(["a"], "same"), "same": page(["b"], "same")})
    seen: list[str] = []
    with pytest.raises(SynthientError, match="repeated cursor"):
        seen.extend(export.id for export in make_client(handler).proxies.iter_exports())
    assert seen == ["a", "b"]


def test_iter_exports_starting_cursor_repeated(make_client: MakeClient) -> None:
    _, handler = paged({"start": page(["a"], "start")})
    with pytest.raises(SynthientError, match="repeated cursor"):
        list(make_client(handler).proxies.iter_exports(cursor="start"))


def test_iter_exports_later_page_error(make_client: MakeClient) -> None:
    _, handler = paged({None: page(["a"], "p2"), "p2": problem(403, title="Forbidden")})
    seen: list[str] = []
    with pytest.raises(APIError) as caught:
        seen.extend(export.id for export in make_client(handler).proxies.iter_exports())
    assert seen == ["a"]
    assert caught.value.status_code == 403
