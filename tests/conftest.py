from __future__ import annotations

import json
import threading
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol

import httpx
import pytest

import synthient._async_client
import synthient._client
from synthient import AsyncClient, Client

API_KEY = "test-key"
BASE_URL = "https://api.test/api/v4"
PRESIGNED = "https://storage.test/proxies/2026-05-06.parquet?X-Amz-Signature=secret-signature"

Handler = Callable[[httpx.Request], httpx.Response]
ServerHandler = Callable[[BaseHTTPRequestHandler], None]


class MakeClient(Protocol):
    def __call__(self, handler: Handler, /, **options: Any) -> Client: ...


IP_INFO: dict[str, Any] = {
    "ip": "8.8.8.8",
    "network": {
        "asn": 15169,
        "isp": "Google LLC",
        "org": "Google",
        "domain": "google.com",
        "type": "DATACENTER",
        "abuse_email": "network-abuse@google.com",
    },
    "location": {
        "country": "US",
        "state": "California",
        "city": "Mountain View",
        "timezone": "America/Los_Angeles",
        "latitude": 37.4056,
        "longitude": -122.0775,
        "geo_hash": "9q9hvu",
    },
    "intelligence": {
        "risk_score": 12,
        "categories": ["DATACENTER"],
        "behavior": [],
        "providers": [
            {"provider": "BRIGHTDATA", "type": "RESIDENTIAL_PROXY", "last_seen": 1778187600}
        ],
        "devices": [{"os": "Android", "version": "14", "last_seen": 1778187600}],
    },
}

EXPORT: dict[str, Any] = {
    "id": "2026-05-07/21",
    "kind": "hourly",
    "date": "2026-05-07",
    "hour": 21,
    "size_bytes": 1048576,
    "row_count": 1200,
    "checksum": "ab12",
    "created_at": 1778187600,
    "download_path": "/api/v4/feeds/proxies/export/2026-05-07/21",
}

HELIOS_HTTPS: dict[str, Any] = {
    "timestamp": 1778200137487,
    "tunnel_id": 42,
    "domain": "example.com",
    "port": 443,
    "protocol": "https",
    "meta": {
        "proxy_ip": "203.0.113.10",
        "server": "hp-eu-1",
        "pool_id": "prod",
        "provider": "luminati",
    },
    "details": {
        "handshake_version": "TLS 1.2",
        "sni": "example.com",
        "cipher_suites": [{"code": 4865, "name": "TLS_AES_128_GCM_SHA256"}],
        "extensions": [{"code": 0, "name": "server_name", "length": 16}],
        "alpn": ["h2", "http/1.1"],
        "has_grease": True,
    },
    "raw": None,
}


def json_response(
    status: int, body: object, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode(), headers=headers)


def problem(status: int, **fields: object) -> httpx.Response:
    return json_response(
        status, {"status": status, **fields}, {"content-type": "application/problem+json"}
    )


def redirect(location: str, status: int = 307) -> httpx.Response:
    return httpx.Response(status, headers={"location": location})


class ChunkStream(httpx.SyncByteStream):
    """A response body that yields fixed chunks and records whether it was closed."""

    def __init__(self, chunks: list[bytes], error: Exception | None = None) -> None:
        self.chunks = chunks
        self.error = error
        self.closed = False
        self.sent = 0

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.chunks:
            self.sent += 1
            yield chunk
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace retry waits with recorders so tests never sleep."""
    recorded: list[float] = []

    async def async_sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(synthient._client, "sleep", recorded.append)
    monkeypatch.setattr(synthient._async_client, "sleep", async_sleep)
    return recorded


@pytest.fixture
def make_client(sleeps: list[float]) -> Iterator[MakeClient]:
    """Build clients whose requests go to a MockTransport handler."""
    http_clients: list[httpx.Client] = []

    def make(handler: Handler, **options: Any) -> Client:
        http = httpx.Client(transport=httpx.MockTransport(handler))
        http_clients.append(http)
        options.setdefault("base_url", BASE_URL)
        return Client(API_KEY, http_client=http, **options)

    yield make
    for http in http_clients:
        http.close()


@asynccontextmanager
async def async_client(handler: Handler, **options: Any) -> AsyncGenerator[AsyncClient, None]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        options.setdefault("base_url", BASE_URL)
        async with AsyncClient(API_KEY, http_client=http, **options) as client:
            yield client


@pytest.fixture(params=["asyncio", "trio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def serve() -> Iterator[Callable[[ServerHandler], str]]:
    """Start a loopback HTTP/1.1 server for behavior a mock transport cannot show."""
    servers: list[ThreadingHTTPServer] = []

    def start(handle: ServerHandler) -> str:
        class RequestHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                handle(self)

            def do_POST(self) -> None:
                handle(self)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), RequestHandler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}/api/v4"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def write_json(handler: BaseHTTPRequestHandler, status: int, body: object) -> None:
    data = json.dumps(body).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
