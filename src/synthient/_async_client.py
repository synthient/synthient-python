from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Iterable
from contextlib import aclosing, asynccontextmanager
from datetime import date
from http import HTTPStatus
from ipaddress import IPv4Address, IPv6Address
from typing import Generic, Self, cast

import anyio
import httpx
from anyio import sleep

from synthient._base import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    MAX_ERROR_SIZE,
    MAX_REDIRECTS,
    MAX_RESPONSE_SIZE,
    BaseTransport,
    Config,
    ModelT,
    Request,
    ResponseHook,
    connection_error,
    decode,
    export_path,
    list_exports_request,
    lookup_domain_request,
    lookup_ip_request,
    lookup_ips_request,
    storage_redirect,
)
from synthient._errors import APIError, SynthientError, api_error
from synthient._models import (
    Account,
    AnonymizerEvent,
    DomainInfo,
    Export,
    ExportMeta,
    ExportPage,
    HealthResponse,
    HeliosADBEvent,
    HeliosDNSEvent,
    HeliosHTTPEvent,
    HeliosHTTPSEvent,
    IPBatchResponse,
    IPInfo,
    JA4TEvent,
    ProxyEvent,
    TorrentEvent,
)
from synthient._ndjson import LineBuffer
from synthient._retry import RetryPolicy, backoff, is_transient, retry_delay


async def _close_http(
    resource: httpx.AsyncClient | httpx.Response | httpx.AsyncByteStream,
) -> None:
    # AnyIO cancellation would otherwise interrupt the awaits that release connections.
    with anyio.move_on_after(5, shield=True):
        await resource.aclose()


class _ClosingStream(httpx.AsyncByteStream):
    """Protect cleanup when HTTPX closes a fully consumed response itself."""

    def __init__(self, stream: httpx.AsyncByteStream) -> None:
        self._stream = stream

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._stream.__aiter__()

    async def aclose(self) -> None:
        await _close_http(self._stream)


class AsyncTransport(BaseTransport):
    def __init__(self, config: Config, http: httpx.AsyncClient | None) -> None:
        super().__init__(config)
        self.owns_http: bool = http is None
        self.http: httpx.AsyncClient = http or httpx.AsyncClient()

    async def aclose(self) -> None:
        if self.owns_http and not self.closed:
            await _close_http(self.http)
        self.closed = True

    async def send(self, request: Request) -> httpx.Response:
        """Send a request with retries and return the unread response."""
        attempt = 0
        while True:
            can_retry = attempt < self.config.max_retries
            try:
                response = await self.http.send(
                    self.build_request(self.http, request), stream=True, follow_redirects=False
                )
            except httpx.TransportError as exc:
                if not (
                    can_retry and request.policy is RetryPolicy.TRANSIENT and is_transient(exc)
                ):
                    raise connection_error(exc) from exc
                await sleep(backoff(attempt))
                attempt += 1
                continue
            if isinstance(response.stream, httpx.AsyncByteStream):
                response.stream = _ClosingStream(response.stream)
            try:
                self.notify(response)
            except BaseException:
                await _close_http(response)
                raise
            delay = retry_delay(request.policy, response, attempt) if can_retry else None
            if delay is None:
                return response
            await _close_http(response)
            await sleep(delay)
            attempt += 1

    async def read(self, response: httpx.Response, limit: int) -> bytes:
        """Read at most ``limit + 1`` bytes of a response body."""
        body = bytearray()
        chunks = cast(AsyncGenerator[bytes, None], response.aiter_bytes())
        async with aclosing(chunks):
            async for chunk in chunks:
                body += chunk[: limit + 1 - len(body)]
                if len(body) > limit:
                    break
        return bytes(body)

    async def api_error(self, response: httpx.Response) -> APIError:
        try:
            body = await self.read(response, MAX_ERROR_SIZE)
        except (httpx.TransportError, httpx.DecodingError) as exc:
            error = api_error(response, b"")
            error.__cause__ = exc
            return error
        return api_error(response, body[:MAX_ERROR_SIZE])

    async def request(self, request: Request, model: type[ModelT]) -> ModelT:
        response = await self.send(request)
        try:
            if not response.is_success:
                raise await self.api_error(response)
            try:
                body = await self.read(response, MAX_RESPONSE_SIZE)
            except httpx.TransportError as exc:
                raise connection_error(exc) from exc
            except httpx.DecodingError as exc:
                raise SynthientError("invalid response encoding") from exc
        finally:
            await _close_http(response)
        if len(body) > MAX_RESPONSE_SIZE:
            raise SynthientError(f"response body exceeds {MAX_RESPONSE_SIZE} bytes")
        return decode(body, model)

    async def open_stream(self, path: str) -> httpx.Response:
        response = await self.send(Request(path=f"{path}/stream", accept="application/x-ndjson"))
        if response.status_code != HTTPStatus.OK:
            try:
                raise await self.api_error(response)
            finally:
                await _close_http(response)
        return response

    async def download_url(self, path: str) -> str:
        response = await self.send(Request(path=path))
        try:
            if response.status_code >= HTTPStatus.BAD_REQUEST:
                raise await self.api_error(response)
            return self.export_location(response)
        finally:
            await _close_http(response)

    async def open_download(self, url: str) -> httpx.Response:
        target = httpx.URL(url)
        for _ in range(MAX_REDIRECTS + 1):
            try:
                response = await self.http.send(
                    self.build_storage_request(target),
                    auth=None,
                    stream=True,
                    follow_redirects=False,
                )
            except httpx.TransportError as exc:
                raise connection_error(exc) from exc
            if isinstance(response.stream, httpx.AsyncByteStream):
                response.stream = _ClosingStream(response.stream)
            if not response.is_redirect:
                break
            try:
                target = storage_redirect(response)
            finally:
                await _close_http(response)
        else:
            raise SynthientError("too many export redirects")
        if response.status_code != HTTPStatus.OK:
            try:
                raise await self.api_error(response)
            finally:
                await _close_http(response)
        return response


class AsyncClient:
    """An asyncio and Trio client for the Synthient API.

    It takes the same arguments and behaves the same as ``Client``, with an
    ``httpx.AsyncClient`` for ``http_client``. Close it with ``aclose()`` or an
    ``async with`` block. Use one client per event loop.
    """

    proxies: AsyncFeed[ProxyEvent]
    """The residential, mobile, and datacenter proxy feed."""
    anonymizers: AsyncFeed[AnonymizerEvent]
    """The VPN and anonymizer IP range feed."""
    torrents: AsyncFeed[TorrentEvent]
    """The BitTorrent DHT and tracker feed."""
    ja4t: AsyncFeed[JA4TEvent]
    """The JA4T TCP fingerprint feed."""
    helios_http: AsyncFeed[HeliosHTTPEvent]
    """The Helios honeypot HTTP capture feed."""
    helios_https: AsyncFeed[HeliosHTTPSEvent]
    """The Helios honeypot TLS ClientHello capture feed."""
    helios_dns: AsyncFeed[HeliosDNSEvent]
    """The Helios honeypot DNS tunnel destination feed."""
    helios_adb: AsyncFeed[HeliosADBEvent]
    """The Helios honeypot Android Debug Bridge command feed."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float | httpx.Timeout | None = DEFAULT_TIMEOUT,
        http_client: httpx.AsyncClient | None = None,
        user_agent: str | None = None,
        response_hook: ResponseHook | None = None,
    ) -> None:
        config = Config.create(
            api_key,
            base_url=base_url,
            max_retries=max_retries,
            timeout=timeout,
            user_agent=user_agent,
            response_hook=response_hook,
        )
        self._transport = AsyncTransport(config, http_client)
        self.proxies = AsyncFeed(self._transport, "/feeds/proxies", ProxyEvent)
        self.anonymizers = AsyncFeed(self._transport, "/feeds/anonymizers", AnonymizerEvent)
        self.torrents = AsyncFeed(self._transport, "/feeds/torrents", TorrentEvent)
        self.ja4t = AsyncFeed(self._transport, "/feeds/ja4t", JA4TEvent)
        self.helios_http = AsyncFeed(self._transport, "/feeds/helio/http", HeliosHTTPEvent)
        self.helios_https = AsyncFeed(self._transport, "/feeds/helio/https", HeliosHTTPSEvent)
        self.helios_dns = AsyncFeed(self._transport, "/feeds/helio/dns", HeliosDNSEvent)
        self.helios_adb = AsyncFeed(self._transport, "/feeds/helio/adb", HeliosADBEvent)

    async def aclose(self) -> None:
        """Close the connection pool. Calling it again does nothing."""
        await self._transport.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def account(self) -> Account:
        """Return the account that owns the API key, including its remaining credits."""
        return await self._transport.request(Request(path="/account/me"), Account)

    async def health(self) -> None:
        """Check that the API is available. See ``Client.health``."""
        # A 503 from /health is the answer, so only 429 is retried.
        await self._transport.request(
            Request(path="/health", policy=RetryPolicy.RATE_LIMIT), HealthResponse
        )

    async def lookup_ip(self, ip: str | IPv4Address | IPv6Address) -> IPInfo:
        """Return intelligence for an IPv4 or IPv6 address. See ``Client.lookup_ip``."""
        return await self._transport.request(lookup_ip_request(ip), IPInfo)

    async def lookup_ips(self, ips: Iterable[str | IPv4Address | IPv6Address]) -> list[IPInfo]:
        """Look up as many as 1000 addresses in one request. See ``Client.lookup_ips``."""
        addresses = list(ips)
        if not addresses:
            return []
        batch = await self._transport.request(lookup_ips_request(addresses), IPBatchResponse)
        return batch.results

    async def lookup_domain(self, domain: str, *, limit: int | None = None) -> DomainInfo:
        """Return the honeypot traffic observed for a domain. See ``Client.lookup_domain``."""
        return await self._transport.request(lookup_domain_request(domain, limit), DomainInfo)


class AsyncFeed(Generic[ModelT]):
    """The async counterpart of ``Feed``. Access feeds through an ``AsyncClient``."""

    def __init__(self, transport: AsyncTransport, path: str, event: type[ModelT]) -> None:
        self._transport = transport
        self._path = path
        self._event = event

    @asynccontextmanager
    async def stream(self) -> AsyncGenerator[AsyncIterator[ModelT], None]:
        """Open the feed's real-time stream and yield an async iterator over its events.

        See ``Feed.stream``.
        """
        response = await self._transport.open_stream(self._path)
        events = self._events(response)
        try:
            yield events
        finally:
            try:
                await events.aclose()
            finally:
                await _close_http(response)

    async def _events(self, response: httpx.Response) -> AsyncGenerator[ModelT, None]:
        lines = LineBuffer()
        try:
            async for chunk in response.aiter_bytes():
                for line in lines.feed(chunk):
                    yield decode(line, self._event)
        except httpx.TransportError as exc:
            raise connection_error(exc) from exc
        except httpx.DecodingError as exc:
            raise SynthientError("invalid response encoding") from exc
        last = lines.finish()
        if last is not None:
            yield decode(last, self._event)

    async def list_exports(
        self, *, limit: int | None = None, cursor: str | None = None
    ) -> ExportPage:
        """Return one page of the feed's exports, newest first. See ``Feed.list_exports``."""
        return await self._transport.request(
            list_exports_request(self._path, limit, cursor), ExportPage
        )

    async def iter_exports(
        self, *, limit: int | None = None, cursor: str | None = None
    ) -> AsyncIterator[Export]:
        """Iterate over the feed's exports, newest first. See ``Feed.iter_exports``."""
        seen = {cursor}
        while True:
            page = await self.list_exports(limit=limit, cursor=cursor)
            for export in page.exports:
                yield export
            if not page.next_cursor:
                return
            if page.next_cursor in seen:
                raise SynthientError("export listing returned a repeated cursor")
            seen.add(page.next_cursor)
            cursor = page.next_cursor

    async def export_meta(self, export: str | date) -> ExportMeta:
        """Return the checksum, size, row count, and parquet schema of an export."""
        return await self._transport.request(
            Request(path=f"{self._path}/export/{export_path(export)}/meta"), ExportMeta
        )

    async def download_url(self, export: str | date) -> str:
        """Return a presigned URL for an export's parquet file. See ``Feed.download_url``."""
        return await self._transport.download_url(f"{self._path}/export/{export_path(export)}")

    @asynccontextmanager
    async def download(self, export: str | date) -> AsyncGenerator[httpx.Response, None]:
        """Download an export's parquet file and yield the streaming httpx response.

        Read the body with ``response.aiter_bytes()``. See ``Feed.download``.
        """
        response = await self._transport.open_download(await self.download_url(export))
        try:
            yield response
        finally:
            await _close_http(response)
