from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import anyio
import httpx
import pytest

from synthient import (
    APIConnectionError,
    APIError,
    AsyncClient,
    ProxyEvent,
    ResponseMetadata,
    SynthientError,
)
from tests.conftest import (
    API_KEY,
    EXPORT,
    IP_INFO,
    PRESIGNED,
    async_client,
    json_response,
    problem,
    redirect,
)

pytestmark = pytest.mark.anyio


class AsyncChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False
        self.sent = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.sent += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


async def test_lookups() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return json_response(200, {"results": [IP_INFO]})
        return json_response(200, IP_INFO)

    async with async_client(handler) as client:
        info = await client.lookup_ip("8.8.8.8")
        results = await client.lookup_ips(["8.8.8.8"])
        assert await client.lookup_ips([]) == []

    assert info.intelligence is not None
    assert info.intelligence.providers[0].last_seen == datetime(2026, 5, 7, 21, tzinfo=UTC)
    assert results == [info]
    assert [request.url.path for request in requests] == [
        "/api/v4/lookup/ip/8.8.8.8",
        "/api/v4/lookup/ips",
    ]
    assert json.loads(requests[1].content) == {"ips": ["8.8.8.8"]}


async def test_health_is_not_retried() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return problem(503)

    async with async_client(handler) as client:
        with pytest.raises(APIError):
            await client.health()
    assert len(requests) == 1


async def test_retries(sleeps: list[float]) -> None:
    responses = iter(
        [
            problem(503),
            httpx.ConnectError("refused"),
            json_response(200, {"stream": "proxies", "feeds": []}),
        ]
    )
    seen: list[ResponseMetadata] = []

    def handler(request: httpx.Request) -> httpx.Response:
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    async with async_client(handler, response_hook=seen.append) as client:
        await client.proxies.list_exports()
    assert len(sleeps) == 2
    assert [metadata.status_code for metadata in seen] == [503, 200]


async def test_billed_transport_error_is_not_retried(sleeps: list[float]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("reset", request=request)

    async with async_client(handler) as client:
        with pytest.raises(APIConnectionError):
            await client.lookup_domain("example.com")
    assert sleeps == []


async def test_cancel_during_backoff() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return problem(503)

    async with async_client(handler) as client:
        with anyio.move_on_after(0.1) as scope:
            await client.account()
    assert scope.cancelled_caught
    assert len(requests) == 1


# Under Trio, httpx's own nested async generators warn when a stream is left early. The
# SDK closes the response itself, which the stream's closed flag below verifies.
@pytest.mark.filterwarnings("ignore:Async generator 'httpx\\.:ResourceWarning")
async def test_stream() -> None:
    record = json.dumps({"ip": "203.0.113.7", "timestamp": 1778187600}).encode()
    body = AsyncChunkStream([record[:5], record[5:] + b"\n\n", record + b"\n", record + b"\n"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "application/x-ndjson"
        return httpx.Response(200, stream=body)

    received: list[ProxyEvent] = []
    async with async_client(handler) as client, client.proxies.stream() as events:
        async for event in events:
            received.append(event)
            if len(received) == 2:
                break
    assert [event.ip for event in received] == ["203.0.113.7", "203.0.113.7"]
    assert body.closed
    assert body.sent == 3


async def test_stream_rejected(sleeps: list[float]) -> None:
    async with async_client(lambda _: problem(429)) as client:
        with pytest.raises(APIError) as caught:
            async with client.torrents.stream():
                pass
    assert caught.value.status_code == 429
    assert sleeps == []


async def test_iter_exports() -> None:
    pages = {
        None: json_response(200, {"stream": "proxies", "feeds": [EXPORT], "next_cursor": "p2"}),
        "p2": json_response(200, {"stream": "proxies", "feeds": [], "next_cursor": "p2"}),
    }
    async with async_client(lambda request: pages[request.url.params.get("cursor")]) as client:
        exports = client.proxies.iter_exports()
        assert (await anext(exports)).id == EXPORT["id"]
        with pytest.raises(SynthientError, match="repeated cursor"):
            await anext(exports)


async def test_download() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.test":
            return redirect(PRESIGNED)
        return httpx.Response(200, content=b"PAR1")

    async with async_client(handler) as client:
        assert await client.helios_dns.download_url("latest") == PRESIGNED
        async with client.helios_dns.download("latest") as response:
            assert await response.aread() == b"PAR1"
    assert "x-api-key" not in requests[-1].headers


async def test_download_downgrade_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.test":
            return redirect(PRESIGNED)
        return redirect("http://storage.test/file", 302)

    async with async_client(handler) as client:
        with pytest.raises(SynthientError, match="does not use https") as caught:
            async with client.proxies.download("latest"):
                pass
    assert "secret-signature" not in str(caught.value)


async def test_close() -> None:
    borrowed = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: json_response(200, {})))
    async with AsyncClient(API_KEY, http_client=borrowed) as client:
        await client.health()
    assert not borrowed.is_closed
    await borrowed.aclose()

    owned = AsyncClient(API_KEY)
    await owned.aclose()
    await owned.aclose()
    with pytest.raises(RuntimeError, match="closed"):
        await owned.health()
