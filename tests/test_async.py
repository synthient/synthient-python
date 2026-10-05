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
    BASE_URL,
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


async def test_api_redirects_override_http_client_default() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.test":
            return redirect(PRESIGNED)
        return json_response(200, {})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as http,
        AsyncClient(API_KEY, base_url=BASE_URL, http_client=http) as client,
    ):
        with pytest.raises(APIError) as caught:
            await client.health()
    assert caught.value.status_code == 307
    assert len(requests) == 1


async def test_download_isolates_http_client_defaults() -> None:
    requests: list[httpx.Request] = []
    cdn_url = "https://cdn.test/file?signature=a%2Fb&part=1&part=2"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.test":
            return redirect(PRESIGNED)
        if request.url.host == "storage.test":
            return redirect(cdn_url)
        return httpx.Response(200, content=b"PAR1")

    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
            headers={"x-api-key": "default-key", "authorization": "default-auth"},
            auth=("user", "password"),
            cookies={"session": "private"},
            params={"debug": "1"},
        ) as http,
        AsyncClient(API_KEY, base_url=BASE_URL, http_client=http) as client,
    ):
        async with client.proxies.download("latest") as response:
            assert await response.aread() == b"PAR1"
        assert http.headers["x-api-key"] == "default-key"
        assert http.follow_redirects

    assert len(requests) == 3
    assert requests[0].headers["x-api-key"] == API_KEY
    assert [str(request.url) for request in requests[1:]] == [PRESIGNED, cdn_url]
    for request in requests[1:]:
        assert not {"x-api-key", "authorization", "cookie"} & request.headers.keys()


class StalledStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b" "
        await anyio.sleep_forever()

    async def aclose(self) -> None:
        await anyio.lowlevel.checkpoint()
        self.closed = True


@pytest.mark.parametrize("operation", ["request", "error", "stream", "download"])
async def test_cancellation_closes_response(operation: str) -> None:
    body = StalledStream()

    def handler(request: httpx.Request) -> httpx.Response:
        if operation == "download" and request.url.host == "api.test":
            return redirect(PRESIGNED)
        return httpx.Response(403 if operation == "error" else 200, stream=body)

    async with async_client(handler) as client:
        with anyio.move_on_after(0.01) as scope:
            if operation == "stream":
                async with client.proxies.stream() as events:
                    await anext(events)
            elif operation == "download":
                async with client.proxies.download("latest") as response:
                    await response.aread()
            else:
                await client.account()
    assert scope.cancelled_caught
    assert body.closed


async def test_response_buffer_is_bounded() -> None:
    async with async_client(lambda _: httpx.Response(200)) as client:
        response = httpx.Response(200, content=b"a" * 100)
        try:
            assert await client._transport.read(response, 8) == b"a" * 9
        finally:
            await response.aclose()


async def test_cancellation_at_end_of_body_closes_response() -> None:
    scope = anyio.CancelScope()

    class CancelAtEOF(StalledStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"{}"
            scope.cancel()

    body = CancelAtEOF()
    with scope:
        async with async_client(lambda _: httpx.Response(200, stream=body)) as client:
            await client.health()
    assert body.closed


@pytest.mark.parametrize("status", [200, 403])
@pytest.mark.filterwarnings("ignore:Async generator 'httpx\\.:ResourceWarning")
async def test_invalid_compression(status: int) -> None:
    body = AsyncChunkStream([b"invalid gzip"])
    async with async_client(
        lambda _: httpx.Response(status, stream=body, headers={"content-encoding": "gzip"})
    ) as client:
        with pytest.raises(SynthientError) as caught:
            await client.account()
    if status == 403:
        assert isinstance(caught.value, APIError)
        assert caught.value.status_code == status
    else:
        assert str(caught.value) == "invalid response encoding"
    assert isinstance(caught.value.__cause__, httpx.DecodingError)
    assert body.closed
