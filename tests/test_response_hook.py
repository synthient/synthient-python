from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from synthient import ExportPage, ResponseMetadata, SynthientError
from tests.conftest import EXPORT, MakeClient, json_response, problem


def test_hook_sees_retries_and_pages(make_client: MakeClient) -> None:
    seen: list[ResponseMetadata] = []
    responses = iter(
        [
            problem(503),
            json_response(
                200,
                {"stream": "proxies", "feeds": [EXPORT], "next_cursor": "p2"},
                headers={"x-request-id": "r2", "ratelimit-remaining": "9"},
            ),
            json_response(200, {"stream": "proxies", "feeds": []}, headers={"x-request-id": "r3"}),
        ]
    )
    client = make_client(lambda _: next(responses), response_hook=seen.append)

    list(client.proxies.iter_exports())

    assert [(m.status_code, m.request_id) for m in seen] == [(503, ""), (200, "r2"), (200, "r3")]
    assert seen[1].headers["ratelimit-remaining"] == "9"


def test_hook_sees_decode_failures(make_client: MakeClient) -> None:
    seen: list[ResponseMetadata] = []
    client = make_client(lambda _: httpx.Response(200, text="nope"), response_hook=seen.append)
    with pytest.raises(SynthientError):
        client.account()
    assert [m.status_code for m in seen] == [200]


def test_hook_headers_are_a_copy(make_client: MakeClient, sleeps: list[float]) -> None:
    def hook(metadata: ResponseMetadata) -> None:
        metadata.headers["retry-after"] = "0"

    responses = iter(
        [
            httpx.Response(429, headers={"retry-after": "30"}),
            json_response(200, {"stream": "proxies", "feeds": []}),
        ]
    )
    make_client(lambda _: next(responses), response_hook=hook).proxies.list_exports()
    assert sleeps[0] >= 30


def test_hook_concurrent_use(make_client: MakeClient) -> None:
    lock = threading.Lock()
    request_ids: list[str] = []

    def hook(metadata: ResponseMetadata) -> None:
        with lock:
            request_ids.append(metadata.request_id)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            200,
            {"stream": "proxies", "feeds": []},
            headers={"x-request-id": request.url.params["cursor"]},
        )

    client = make_client(handler, response_hook=hook)

    def list_page(cursor: int) -> ExportPage:
        return client.proxies.list_exports(cursor=str(cursor))

    with ThreadPoolExecutor(max_workers=8) as pool:
        pages = list(pool.map(list_page, range(20)))
    assert len(pages) == 20
    assert sorted(request_ids, key=int) == [str(i) for i in range(20)]
