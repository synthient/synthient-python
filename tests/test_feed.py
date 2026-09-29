from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone

import httpx
import pytest

from synthient import APIError, SynthientError
from synthient._base import export_path
from tests.conftest import EXPORT, MakeClient, json_response, problem

FEEDS = {
    "proxies": "/api/v4/feeds/proxies",
    "anonymizers": "/api/v4/feeds/anonymizers",
    "torrents": "/api/v4/feeds/torrents",
    "ja4t": "/api/v4/feeds/ja4t",
    "helios_http": "/api/v4/feeds/helio/http",
    "helios_https": "/api/v4/feeds/helio/https",
    "helios_dns": "/api/v4/feeds/helio/dns",
    "helios_adb": "/api/v4/feeds/helio/adb",
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
        return json_response(200, META)

    client = make_client(handler)
    getattr(client, feed).export_meta("latest")
    getattr(client, feed).export_meta("2026-05-03/07")
    assert paths == [f"{prefix}/export/latest/meta", f"{prefix}/export/2026-05-03/7/meta"]


def test_export_meta(make_client: MakeClient) -> None:
    meta = make_client(lambda _: json_response(200, META)).proxies.export_meta("2026-05-03/7")
    assert meta.hour == 7
    assert meta.date == datetime(2026, 5, 3, 7, tzinfo=UTC)
    assert meta.parquet_schema.fields[0].type == "string"


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
