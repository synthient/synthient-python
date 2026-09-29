from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from synthient import APIConnectionError, APIError, Client, SynthientError
from synthient._ndjson import LineBuffer
from tests.conftest import ChunkStream, MakeClient, json_response, problem

PROXY = {
    "ip": "203.0.113.7",
    "provider": "BRIGHTDATA",
    "type": "RESIDENTIAL_PROXY",
    "country_code": "US",
    "asn": 7922,
    "timestamp": 1778187600,
}


def stream_client(make_client: MakeClient, body: ChunkStream) -> tuple[Client, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=body, headers={"content-type": "application/x-ndjson"})

    return make_client(handler), requests


def test_stream(make_client: MakeClient) -> None:
    record = json.dumps(PROXY).encode()
    body = ChunkStream([record[:10], record[10:] + b"\n", b"\n", b"  \r\n", record + b"\r\n"])
    client, requests = stream_client(make_client, body)

    with client.proxies.stream() as events:
        received = list(events)

    assert requests[0].url.path == "/api/v4/feeds/proxies/stream"
    assert requests[0].headers["accept"] == "application/x-ndjson"
    assert len(received) == 2
    assert received[0].ip == "203.0.113.7"
    assert received[0].timestamp == datetime(2026, 5, 7, 21, tzinfo=UTC)
    assert body.closed


def test_stream_splits_utf8_across_chunks(make_client: MakeClient) -> None:
    record = json.dumps({"name": "café ☕"}, ensure_ascii=False).encode()
    split = record.index("☕".encode()) + 1
    body = ChunkStream([record[:split], record[split:] + b"\n"])
    client, _ = stream_client(make_client, body)
    with client.torrents.stream() as events:
        assert [event.name for event in events] == ["café ☕"]


def test_stream_final_line_without_newline(make_client: MakeClient) -> None:
    client, _ = stream_client(make_client, ChunkStream([json.dumps(PROXY).encode()]))
    with client.proxies.stream() as events:
        assert len(list(events)) == 1


def test_stream_helios_https(make_client: MakeClient) -> None:
    lines = [
        {
            "timestamp": 1778200137487,
            "protocol": "https",
            "details": {"sni": "example.com"},
            "raw": None,
        },
        {"timestamp": 1778200137487, "details": '{"sni": "example.com"}'},
    ]
    body = ChunkStream([b"".join(json.dumps(line).encode() + b"\n" for line in lines)])
    client, _ = stream_client(make_client, body)
    with client.helios_https.stream() as events:
        first, second = events
    assert first.timestamp == datetime(2026, 5, 8, 0, 28, 57, 487000, tzinfo=UTC)
    assert first.details == {"sni": "example.com"}
    assert second.details == '{"sni": "example.com"}'


def test_stream_break_closes_response(make_client: MakeClient) -> None:
    record = json.dumps(PROXY).encode() + b"\n"
    body = ChunkStream([record] * 5)
    client, _ = stream_client(make_client, body)
    with client.proxies.stream() as events:
        for _ in events:
            break
    assert body.closed
    assert body.sent == 1


@pytest.mark.parametrize(
    ("chunks", "message"),
    [
        ([b'{"ip": "1.2.3.4"}\n{"ip": \n'], "invalid ProxyEvent"),
        ([b'{"asn": "7922"}\n'], "invalid ProxyEvent"),
        ([b'{"ip": "1.2.3.4"'], "invalid ProxyEvent"),
    ],
)
def test_stream_invalid_events(make_client: MakeClient, chunks: list[bytes], message: str) -> None:
    body = ChunkStream(chunks)
    client, _ = stream_client(make_client, body)
    with pytest.raises(SynthientError, match=message), client.proxies.stream() as events:
        list(events)
    assert body.closed


def test_stream_read_error(make_client: MakeClient) -> None:
    body = ChunkStream([json.dumps(PROXY).encode() + b"\n"], httpx.ReadError("reset"))
    client, _ = stream_client(make_client, body)
    received: list[object] = []
    with pytest.raises(APIConnectionError) as caught, client.proxies.stream() as events:
        received.extend(events)
    assert len(received) == 1
    assert isinstance(caught.value.__cause__, httpx.ReadError)
    assert body.closed


def test_stream_rejected(make_client: MakeClient) -> None:
    client = make_client(lambda _: problem(403, title="Forbidden", detail="needs PROXY_FIREHOSE"))
    with pytest.raises(APIError) as caught, client.proxies.stream():
        pass
    assert caught.value.status_code == 403
    assert caught.value.detail == "needs PROXY_FIREHOSE"


def test_stream_connect_retry(make_client: MakeClient, sleeps: list[float]) -> None:
    responses = iter([problem(503), httpx.Response(200, content=json.dumps(PROXY).encode())])
    client = make_client(lambda _: next(responses))
    with client.proxies.stream() as events:
        assert len(list(events)) == 1
    assert len(sleeps) == 1


def test_stream_requires_200(make_client: MakeClient) -> None:
    client = make_client(lambda _: json_response(204, {}))
    with pytest.raises(APIError) as caught, client.proxies.stream():
        pass
    assert caught.value.status_code == 204


def test_line_buffer_limit() -> None:
    lines = LineBuffer(limit=8)
    assert lines.feed(b"12345678\n1234") == [b"12345678"]
    with pytest.raises(SynthientError, match="exceeds 8 bytes"):
        lines.feed(b"56789")
    with pytest.raises(SynthientError, match="exceeds 8 bytes"):
        LineBuffer(limit=8).feed(b"123456789\n")


def test_line_buffer_chunk_boundaries() -> None:
    data = b'{"a":1}\r\n\n  \n{"b":2}\n{"c":3}'
    for size in range(1, len(data) + 1):
        lines = LineBuffer()
        records = [
            record for i in range(0, len(data), size) for record in lines.feed(data[i : i + size])
        ]
        records.append(lines.finish() or b"")
        assert records == [b'{"a":1}', b'{"b":2}', b'{"c":3}'], size
