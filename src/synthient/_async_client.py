from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import date
from http import HTTPStatus
from ipaddress import IPv4Address, IPv6Address
from typing import Generic, Self

import anyio
import httpx

from synthient._base import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    MAX_ERROR_SIZE,
    MAX_REDIRECTS,
    MAX_RESPONSE_SIZE,
    BaseTransport,
    Config,
    EventT,
    ModelT,
    Request,
    ResponseHook,
    connection_error,
    decode,
    export_meta_request,
    export_url_path,
    health_request,
    list_exports_request,
    lookup_domain_request,
    lookup_ip_request,
    lookup_ips_request,
    storage_redirect,
    stream_request,
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


class AsyncTransport(BaseTransport):
    def __init__(self, config: Config, http: httpx.AsyncClient | None) -> None:
        super().__init__(config)
        self.owns_http: bool = http is None
        self.http: httpx.AsyncClient = http or httpx.AsyncClient()

    async def aclose(self) -> None:
        if self.owns_http and not self.closed:
            await self.http.aclose()
        self.closed = True

    async def send(self, request: Request) -> httpx.Response:
        """Send a request with retries and return the unread response."""
        attempt = 0
        while True:
            can_retry = attempt < self.config.max_retries
            try:
                response = await self.http.send(self.build_request(self.http, request), stream=True)
            except httpx.TransportError as exc:
                if not (
                    can_retry and request.policy is RetryPolicy.TRANSIENT and is_transient(exc)
                ):
                    raise connection_error(exc) from exc
                await anyio.sleep(backoff(attempt))
                attempt += 1
                continue
            try:
                self.notify(response)
            except BaseException:
                await response.aclose()
                raise
            delay = retry_delay(request.policy, response, attempt) if can_retry else None
            if delay is None:
                return response
            await response.aclose()
            await anyio.sleep(delay)
            attempt += 1

    async def read(self, response: httpx.Response, limit: int) -> bytes:
        """Read at most ``limit + 1`` bytes of a response body."""
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > limit:
                break
        return bytes(body)

    async def api_error(self, response: httpx.Response) -> APIError:
        try:
            body = await self.read(response, MAX_ERROR_SIZE)
        except httpx.TransportError as exc:
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
        finally:
            await response.aclose()
        if len(body) > MAX_RESPONSE_SIZE:
            raise SynthientError(f"response body exceeds {MAX_RESPONSE_SIZE} bytes")
        return decode(body, model)

    async def open_stream(self, path: str) -> httpx.Response:
        response = await self.send(stream_request(path))
        if response.status_code != HTTPStatus.OK:
            try:
                raise await self.api_error(response)
            finally:
                await response.aclose()
        return response

    async def download_url(self, path: str) -> str:
        response = await self.send(Request(path=path))
        try:
            if response.status_code >= HTTPStatus.BAD_REQUEST:
                raise await self.api_error(response)
            return self.export_location(response)
        finally:
            await response.aclose()

    async def open_download(self, url: str) -> httpx.Response:
        target = httpx.URL(url)
        for _ in range(MAX_REDIRECTS + 1):
            try:
                response = await self.http.send(
                    self.build_storage_request(self.http, target),
                    stream=True,
                    follow_redirects=False,
                )
            except httpx.TransportError as exc:
                raise connection_error(exc) from exc
            if not response.is_redirect:
                break
            try:
                target = storage_redirect(response)
            finally:
                await response.aclose()
        else:
            raise SynthientError("too many export redirects")
        if response.status_code != HTTPStatus.OK:
            try:
                raise await self.api_error(response)
            finally:
                await response.aclose()
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
        """Check that the API is available.

        Raises:
            APIError: If the API reports a problem. A 503 is not retried.
        """
        await self._transport.request(health_request(), HealthResponse)

    async def lookup_ip(self, ip: str | IPv4Address | IPv6Address) -> IPInfo:
        """Return intelligence for an IPv4 or IPv6 address. Each lookup costs one credit.

        Raises:
            ValueError: If ``ip`` is not a valid address. No request is sent.
        """
        return await self._transport.request(lookup_ip_request(ip), IPInfo)

    async def lookup_ips(self, ips: Iterable[str | IPv4Address | IPv6Address]) -> list[IPInfo]:
        """Look up as many as 1000 addresses in one request.

        The API bills ceil(n * 0.9) credits for n unique addresses. Duplicates are
        collapsed into one result, so match results by IP rather than by position. An
        empty list returns [] without a request.

        Raises:
            ValueError: If an address is invalid. No request is sent.
        """
        request = lookup_ips_request(ips)
        if request is None:
            return []
        return (await self._transport.request(request, IPBatchResponse)).results

    async def lookup_domain(self, domain: str, *, limit: int | None = None) -> DomainInfo:
        """Return the honeypot traffic observed for a domain.

        Each lookup costs one credit, and the API allows 20 per minute per team. A domain
        that has never been observed returns ``status`` "inactive" with zeroed stats.

        Args:
            domain: The domain. Internationalized names must be sent in Punycode.
            limit: Caps the number of recent events, from 1 to 1000. The API default is 50.
        """
        return await self._transport.request(lookup_domain_request(domain, limit), DomainInfo)


class AsyncFeed(Generic[EventT]):
    """The async counterpart of ``Feed``. Access feeds through an ``AsyncClient``."""

    def __init__(self, transport: AsyncTransport, path: str, event: type[EventT]) -> None:
        self._transport = transport
        self._path = path
        self._event = event

    @asynccontextmanager
    async def stream(self) -> AsyncGenerator[AsyncIterator[EventT], None]:
        """Open the feed's real-time stream and yield an async iterator over its events.

        The connection opens when the ``async with`` block is entered and closes when it
        exits, even after a ``break``. See ``Feed.stream``.
        """
        response = await self._transport.open_stream(self._path)
        events = self._events(response)
        try:
            yield events
        finally:
            await events.aclose()
            await response.aclose()

    async def _events(self, response: httpx.Response) -> AsyncGenerator[EventT, None]:
        lines = LineBuffer()
        try:
            async for chunk in response.aiter_bytes():
                for line in lines.feed(chunk):
                    yield decode(line, self._event)
        except httpx.TransportError as exc:
            raise connection_error(exc) from exc
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
        """Iterate over the feed's exports, newest first, fetching pages lazily.

        The newest hourly export can appear twice, under "latest" and under its own ID.

        Raises:
            SynthientError: If the API repeats a cursor, after that page's exports.
        """
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
        return await self._transport.request(export_meta_request(self._path, export), ExportMeta)

    async def download_url(self, export: str | date) -> str:
        """Return a presigned URL for an export's parquet file. See ``Feed.download_url``."""
        return await self._transport.download_url(export_url_path(self._path, export))

    @asynccontextmanager
    async def download(self, export: str | date) -> AsyncGenerator[httpx.Response, None]:
        """Download an export's parquet file and yield the streaming httpx response.

        Read the body with ``response.aiter_bytes()``. See ``Feed.download``.
        """
        response = await self._transport.open_download(await self.download_url(export))
        try:
            yield response
        finally:
            await response.aclose()
