from __future__ import annotations

import time
from collections.abc import Generator, Iterable, Iterator
from contextlib import contextmanager
from datetime import date
from http import HTTPStatus
from ipaddress import IPv4Address, IPv6Address
from typing import Generic, Self

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


class Transport(BaseTransport):
    def __init__(self, config: Config, http: httpx.Client | None) -> None:
        super().__init__(config)
        self.owns_http: bool = http is None
        self.http: httpx.Client = http or httpx.Client()

    def close(self) -> None:
        if self.owns_http and not self.closed:
            self.http.close()
        self.closed = True

    def send(self, request: Request) -> httpx.Response:
        """Send a request with retries and return the unread response."""
        attempt = 0
        while True:
            can_retry = attempt < self.config.max_retries
            try:
                response = self.http.send(
                    self.build_request(self.http, request), stream=True, follow_redirects=False
                )
            except httpx.TransportError as exc:
                if not (
                    can_retry and request.policy is RetryPolicy.TRANSIENT and is_transient(exc)
                ):
                    raise connection_error(exc) from exc
                time.sleep(backoff(attempt))
                attempt += 1
                continue
            try:
                self.notify(response)
            except BaseException:
                response.close()
                raise
            delay = retry_delay(request.policy, response, attempt) if can_retry else None
            if delay is None:
                return response
            response.close()
            time.sleep(delay)
            attempt += 1

    def read(self, response: httpx.Response, limit: int) -> bytes:
        """Read at most ``limit + 1`` bytes of a response body."""
        body = bytearray()
        for chunk in response.iter_bytes():
            body += chunk[: limit + 1 - len(body)]
            if len(body) > limit:
                break
        return bytes(body)

    def api_error(self, response: httpx.Response) -> APIError:
        try:
            body = self.read(response, MAX_ERROR_SIZE)
        except (httpx.TransportError, httpx.DecodingError) as exc:
            error = api_error(response, b"")
            error.__cause__ = exc
            return error
        return api_error(response, body[:MAX_ERROR_SIZE])

    def request(self, request: Request, model: type[ModelT]) -> ModelT:
        response = self.send(request)
        try:
            if not response.is_success:
                raise self.api_error(response)
            try:
                body = self.read(response, MAX_RESPONSE_SIZE)
            except httpx.TransportError as exc:
                raise connection_error(exc) from exc
            except httpx.DecodingError as exc:
                raise SynthientError("invalid response encoding") from exc
        finally:
            response.close()
        if len(body) > MAX_RESPONSE_SIZE:
            raise SynthientError(f"response body exceeds {MAX_RESPONSE_SIZE} bytes")
        return decode(body, model)

    def open_stream(self, path: str) -> httpx.Response:
        response = self.send(Request(path=f"{path}/stream", accept="application/x-ndjson"))
        if response.status_code != HTTPStatus.OK:
            try:
                raise self.api_error(response)
            finally:
                response.close()
        return response

    def download_url(self, path: str) -> str:
        response = self.send(Request(path=path))
        try:
            if response.status_code >= HTTPStatus.BAD_REQUEST:
                raise self.api_error(response)
            return self.export_location(response)
        finally:
            response.close()

    def open_download(self, url: str) -> httpx.Response:
        target = httpx.URL(url)
        for _ in range(MAX_REDIRECTS + 1):
            try:
                response = self.http.send(
                    self.build_storage_request(target),
                    auth=None,
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
                response.close()
        else:
            raise SynthientError("too many export redirects")
        if response.status_code != HTTPStatus.OK:
            try:
                raise self.api_error(response)
            finally:
                response.close()
        return response


class Client:
    """A client for the Synthient API.

    Reuse one client for many requests; it pools connections and is safe to share across
    threads. Close it with ``close()`` or a ``with`` block.

    Requests that cost no credits are retried on 429, 500, 502, 503, and 504 responses and
    on transient connection failures. Billed lookups are retried only on 429, which the API
    returns before doing any work. Retries back off exponentially with jitter and honor
    Retry-After up to one minute. A 429 without Retry-After is not retried, because it
    means the key's monthly request budget is spent or too many of its streams are open.

    Args:
        api_key: The API key. Defaults to the SYNTHIENT_API_KEY environment variable.
        base_url: The API base URL, including the version path.
        max_retries: How many times a failed request is retried. Zero disables retries.
        timeout: The httpx timeout for every request. The default waits up to 10 seconds
            to connect and 60 seconds for each read, which also suits streams because the
            API sends a keepalive line every 15 seconds.
        http_client: An httpx client to send requests with, for proxies, TLS, or
            transports. The SDK never closes or modifies it. API redirects are disabled.
            Storage requests exclude its default headers, cookies, query parameters,
            and authentication to protect credentials and presigned URLs.
        user_agent: Appended to the User-Agent header, such as "my-app/1.2".
        response_hook: Called with the ResponseMetadata of every API response, including
            retries, pages, stream handshakes, and errors. Download responses from
            storage are excluded.

    Raises:
        ValueError: If the API key is missing or an option is invalid.
    """

    proxies: Feed[ProxyEvent]
    """The residential, mobile, and datacenter proxy feed."""
    anonymizers: Feed[AnonymizerEvent]
    """The VPN and anonymizer IP range feed."""
    torrents: Feed[TorrentEvent]
    """The BitTorrent DHT and tracker feed."""
    ja4t: Feed[JA4TEvent]
    """The JA4T TCP fingerprint feed."""
    helios_http: Feed[HeliosHTTPEvent]
    """The Helios honeypot HTTP capture feed."""
    helios_https: Feed[HeliosHTTPSEvent]
    """The Helios honeypot TLS ClientHello capture feed."""
    helios_dns: Feed[HeliosDNSEvent]
    """The Helios honeypot DNS tunnel destination feed."""
    helios_adb: Feed[HeliosADBEvent]
    """The Helios honeypot Android Debug Bridge command feed."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float | httpx.Timeout | None = DEFAULT_TIMEOUT,
        http_client: httpx.Client | None = None,
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
        self._transport = Transport(config, http_client)
        self.proxies = Feed(self._transport, "/feeds/proxies", ProxyEvent)
        self.anonymizers = Feed(self._transport, "/feeds/anonymizers", AnonymizerEvent)
        self.torrents = Feed(self._transport, "/feeds/torrents", TorrentEvent)
        self.ja4t = Feed(self._transport, "/feeds/ja4t", JA4TEvent)
        self.helios_http = Feed(self._transport, "/feeds/helio/http", HeliosHTTPEvent)
        self.helios_https = Feed(self._transport, "/feeds/helio/https", HeliosHTTPSEvent)
        self.helios_dns = Feed(self._transport, "/feeds/helio/dns", HeliosDNSEvent)
        self.helios_adb = Feed(self._transport, "/feeds/helio/adb", HeliosADBEvent)

    def close(self) -> None:
        """Close the connection pool. Calling it again does nothing."""
        self._transport.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def account(self) -> Account:
        """Return the account that owns the API key, including its remaining credits."""
        return self._transport.request(Request(path="/account/me"), Account)

    def health(self) -> None:
        """Check that the API is available.

        Raises:
            APIError: If the API reports a problem. A 503 is not retried.
        """
        # A 503 from /health is the answer, so only 429 is retried.
        self._transport.request(
            Request(path="/health", policy=RetryPolicy.RATE_LIMIT), HealthResponse
        )

    def lookup_ip(self, ip: str | IPv4Address | IPv6Address) -> IPInfo:
        """Return intelligence for an IPv4 or IPv6 address.

        Each lookup costs one credit. Failed lookups are not billed.

        Raises:
            ValueError: If ``ip`` is not a valid address. No request is sent.
            APIError: With status 422 if ``ip`` is a private or reserved address.
        """
        return self._transport.request(lookup_ip_request(ip), IPInfo)

    def lookup_ips(self, ips: Iterable[str | IPv4Address | IPv6Address]) -> list[IPInfo]:
        """Look up as many as 1000 addresses in one request.

        The API bills ceil(n * 0.9) credits for n distinct usable addresses. Private and
        reserved addresses are skipped without a result, and duplicates collapse into one,
        so match results by IP rather than by position. Results follow the order in which
        each address first appears. An empty list returns [] without a request.

        Raises:
            ValueError: If an address is invalid or there are more than 1000. No request
                is sent.
            APIError: With status 422 if every address is private or reserved.
        """
        addresses = list(ips)
        if not addresses:
            return []
        return self._transport.request(lookup_ips_request(addresses), IPBatchResponse).results

    def lookup_domain(self, domain: str, *, limit: int | None = None) -> DomainInfo:
        """Return the honeypot traffic observed for a domain.

        Each lookup costs one credit, and the API allows 20 per minute per team. A domain
        that has never been observed returns ``status`` "inactive" with zeroed stats.

        Args:
            domain: The domain. Internationalized names must be sent in Punycode.
            limit: Caps the number of recent events, from 1 to 1000. The API default is 50.
        """
        return self._transport.request(lookup_domain_request(domain, limit), DomainInfo)


class Feed(Generic[EventT]):
    """A Synthient data feed: a real-time event stream and hourly parquet exports that
    roll up into daily exports. Access feeds through a client, such as ``client.proxies``.

    Export IDs are a UTC day as "YYYY-MM-DD" or a ``date``, a UTC hour as "YYYY-MM-DD/H"
    or a timezone-aware ``datetime``, or "latest" for the freshest export: the newest
    hourly during the day, and the daily rollup once the day's hourly exports are rolled
    up. ``Export.id`` values can be passed as they are.
    """

    def __init__(self, transport: Transport, path: str, event: type[EventT]) -> None:
        self._transport = transport
        self._path = path
        self._event = event

    @contextmanager
    def stream(self) -> Generator[Iterator[EventT], None, None]:
        """Open the feed's real-time stream and yield an iterator over its events.

        The connection opens when the ``with`` block is entered and closes when it exits,
        even after a ``break``. The server ends streams after about 30 minutes, which
        ends the iteration without an error; open a new stream to keep consuming.

        Raises:
            APIError: If the stream cannot be opened, such as 403 without the feed's
                scope or 429 with too many concurrent streams.
        """
        response = self._transport.open_stream(self._path)
        events = self._events(response)
        try:
            yield events
        finally:
            events.close()
            response.close()

    def _events(self, response: httpx.Response) -> Generator[EventT, None, None]:
        lines = LineBuffer()
        try:
            for chunk in response.iter_bytes():
                for line in lines.feed(chunk):
                    yield decode(line, self._event)
        except httpx.TransportError as exc:
            raise connection_error(exc) from exc
        except httpx.DecodingError as exc:
            raise SynthientError("invalid response encoding") from exc
        last = lines.finish()
        if last is not None:
            yield decode(last, self._event)

    def list_exports(self, *, limit: int | None = None, cursor: str | None = None) -> ExportPage:
        """Return one page of the feed's exports, newest first.

        Args:
            limit: The page size, from 1 to 500. The API default is 100.
            cursor: Continues a listing from a previous page's ``next_cursor``.
        """
        return self._transport.request(list_exports_request(self._path, limit, cursor), ExportPage)

    def iter_exports(
        self, *, limit: int | None = None, cursor: str | None = None
    ) -> Iterator[Export]:
        """Iterate over the feed's exports, newest first, fetching pages lazily.

        The newest hourly export can appear twice, under "latest" and under its own ID.

        Raises:
            SynthientError: If the API repeats a cursor, after that page's exports.
        """
        seen = {cursor}
        while True:
            page = self.list_exports(limit=limit, cursor=cursor)
            yield from page.exports
            if not page.next_cursor:
                return
            if page.next_cursor in seen:
                raise SynthientError("export listing returned a repeated cursor")
            seen.add(page.next_cursor)
            cursor = page.next_cursor

    def export_meta(self, export: str | date) -> ExportMeta:
        """Return the checksum, size, row count, and parquet schema of an export."""
        return self._transport.request(
            Request(path=f"{self._path}/export/{export_path(export)}/meta"), ExportMeta
        )

    def download_url(self, export: str | date) -> str:
        """Return a presigned URL for an export's parquet file.

        The URL is valid for 24 hours and grants access without an API key, so treat it
        as a secret. It suits tools that read parquet over HTTPS directly.
        """
        return self._transport.download_url(f"{self._path}/export/{export_path(export)}")

    @contextmanager
    def download(self, export: str | date) -> Generator[httpx.Response, None, None]:
        """Download an export's parquet file and yield the streaming httpx response.

        Read the body with ``response.iter_bytes()``; it closes when the ``with`` block
        exits. The API key is sent only to the Synthient API, never to the storage host,
        and errors never include the presigned URL. Compare the SHA-256 of the file with
        ``Export.checksum`` to verify it.

        Raises:
            APIError: If the API or the storage host returns an error status. A daily
                export that is still being built returns 404 with ``retry_after`` set.
        """
        response = self._transport.open_download(self.download_url(export))
        try:
            yield response
        finally:
            response.close()
