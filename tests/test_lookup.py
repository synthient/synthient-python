from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv6Address, ip_address

import httpx
import pytest

from tests.conftest import IP_INFO, MakeClient, json_response


def recorder(body: object) -> tuple[list[httpx.Request], Callable[[httpx.Request], httpx.Response]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return json_response(200, body)

    return requests, handler


def test_lookup_ip(make_client: MakeClient) -> None:
    requests, handler = recorder(IP_INFO)
    info = make_client(handler).lookup_ip("8.8.8.8")

    assert requests[0].method == "GET"
    assert requests[0].url.raw_path == b"/api/v4/lookup/ip/8.8.8.8"
    assert info.ip == IPv4Address("8.8.8.8")
    assert info.network is not None
    assert info.network.asn == 15169
    assert info.network.abuse_phone is None
    assert info.location is not None
    assert info.location.latitude == 37.4056
    assert info.intelligence is not None
    assert info.intelligence.risk_score == 12
    provider = info.intelligence.providers[0]
    assert provider.name == "BRIGHTDATA"
    assert provider.last_seen == datetime(2026, 5, 7, 21, tzinfo=UTC)
    assert info.intelligence.devices[0].os == "Android"


@pytest.mark.parametrize(
    ("address", "path"),
    [
        ("2001:db8::1", b"/api/v4/lookup/ip/2001:db8::1"),
        (IPv6Address("2001:0db8:0000::0001"), b"/api/v4/lookup/ip/2001:db8::1"),
        (ip_address("1.1.1.1"), b"/api/v4/lookup/ip/1.1.1.1"),
    ],
)
def test_lookup_ip_path(
    make_client: MakeClient, address: str | IPv4Address | IPv6Address, path: bytes
) -> None:
    requests, handler = recorder({"ip": str(address)})
    make_client(handler).lookup_ip(address)
    assert requests[0].url.raw_path == path


@pytest.mark.parametrize(
    "body",
    [
        {"ip": "8.8.8.8"},
        {"ip": "8.8.8.8", "network": None, "location": None, "intelligence": None},
    ],
)
def test_lookup_ip_absent_sections(make_client: MakeClient, body: object) -> None:
    _, handler = recorder(body)
    info = make_client(handler).lookup_ip("8.8.8.8")
    assert info.network is None
    assert info.location is None
    assert info.intelligence is None


@pytest.mark.parametrize("address", ["", "8.8.8", "not-an-ip", "1.1.1.1/32"])
def test_lookup_ip_invalid(make_client: MakeClient, address: str) -> None:
    requests, handler = recorder(IP_INFO)
    with pytest.raises(ValueError, match="does not appear to be an IPv4 or IPv6 address"):
        make_client(handler).lookup_ip(address)
    assert requests == []


def test_lookup_ip_rejects_integers(make_client: MakeClient) -> None:
    requests, handler = recorder(IP_INFO)
    with pytest.raises(TypeError, match="expected an IP address"):
        make_client(handler).lookup_ip(134744072)  # pyright: ignore[reportArgumentType]
    assert requests == []


def test_lookup_ips(make_client: MakeClient) -> None:
    requests, handler = recorder({"results": [IP_INFO, {"ip": "2001:db8::1"}]})
    results = make_client(handler).lookup_ips(["8.8.8.8", IPv6Address("2001:db8::1")])

    request = requests[0]
    assert request.method == "POST"
    assert request.url.raw_path == b"/api/v4/lookup/ips"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {"ips": ["8.8.8.8", "2001:db8::1"]}
    assert [str(result.ip) for result in results] == ["8.8.8.8", "2001:db8::1"]
    assert results[1].intelligence is None


def test_lookup_ips_empty(make_client: MakeClient) -> None:
    requests, handler = recorder({"results": []})
    assert make_client(handler).lookup_ips([]) == []
    assert requests == []


def test_lookup_ips_invalid(make_client: MakeClient) -> None:
    requests, handler = recorder({"results": []})
    with pytest.raises(ValueError, match="invalid IP address at index 1"):
        make_client(handler).lookup_ips(["8.8.8.8", "bogus"])
    assert requests == []


def test_lookup_ips_batch_limit(make_client: MakeClient) -> None:
    requests, handler = recorder({"results": []})
    client = make_client(handler)
    addresses = [f"203.0.{index // 256}.{index % 256}" for index in range(1001)]

    client.lookup_ips(addresses[:1000])
    assert len(requests) == 1
    with pytest.raises(ValueError, match="at most 1000 addresses, got 1001"):
        client.lookup_ips(addresses)
    assert len(requests) == 1
