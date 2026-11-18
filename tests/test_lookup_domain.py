"""Domain lookup was removed from the API and is deprecated until the next major version."""
# pyright: reportDeprecated=false

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

import synthient
from synthient import APIConnectionError, AsyncClient, Client
from tests.conftest import MakeClient, async_client
from tests.test_lookup import recorder

pytestmark = pytest.mark.filterwarnings("ignore:Domain lookup was removed:DeprecationWarning")

DOMAIN_INFO: dict[str, Any] = {
    "domain": "example.com",
    "status": "active",
    "stats": {"total_events_30d": 900, "events_24h": 30},
    "time_series": [{"date": 1778112000, "events": 30, "unique_ips": 12}],
    "unique_ips": {"value_24h": 12, "value_30d": 240, "sparkline_24h": [0, 1, 2]},
    "top_subdomains": [{"subdomain": "www", "count": 20}],
    "top_ports": [{"port": 443, "count": 25}],
    "geo_distribution": [{"country_code": "US", "unique_ips": 5, "events": 10}],
    "hour_dow_heatmap": [[day + hour for hour in range(24)] for day in range(7)],
    "activity_stats": {
        "peak_hour": 14,
        "quiet_hour": 3,
        "median_per_hour": 1,
        "p95_per_hour": 4,
        "cadence": "Activity is evenly distributed across the day.",
    },
    "recent_events": [
        {
            "timestamp": 1778187600,
            "source_ip_masked": "203.0.113.7",
            "target_subdomain": "www",
            "port": 443,
            "country_code": "US",
        }
    ],
    "top_asn": {"asn": 15169, "events": 9},
}


def test_deprecation_marker(make_client: MakeClient) -> None:
    _, handler = recorder(DOMAIN_INFO)
    with pytest.deprecated_call(match="Domain lookup was removed"):
        make_client(handler).lookup_domain("example.com")
    message = vars(synthient.DomainInfo)["__deprecated__"]
    assert message.startswith("Domain lookup was removed from the Synthient API")
    assert vars(Client.lookup_domain)["__deprecated__"] == message
    assert vars(AsyncClient.lookup_domain)["__deprecated__"] == message


@pytest.mark.anyio
async def test_async_lookup_domain(sleeps: list[float]) -> None:
    requests, handler = recorder(DOMAIN_INFO)
    async with async_client(handler) as client:
        with pytest.deprecated_call(match="Domain lookup was removed"):
            info = await client.lookup_domain("example.com", limit=5)
    assert requests[0].url.raw_path == b"/api/v4/lookup/domain/example.com?limit=5"
    assert info.stats.events_24h == 30

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("reset", request=request)

    async with async_client(broken) as client:
        with pytest.raises(APIConnectionError):
            await client.lookup_domain("example.com")
    assert sleeps == []


def test_lookup_domain(make_client: MakeClient) -> None:
    requests, handler = recorder(DOMAIN_INFO)
    info = make_client(handler).lookup_domain("example.com", limit=5)

    assert requests[0].url.raw_path == b"/api/v4/lookup/domain/example.com?limit=5"
    assert info.stats.events_24h == 30
    assert info.time_series[0].date == datetime(2026, 5, 7, tzinfo=UTC)
    assert info.top_asn is not None
    assert info.top_asn.asn == 15169
    assert info.hour_dow_heatmap == DOMAIN_INFO["hour_dow_heatmap"]
    assert info.recent_events[0].timestamp == datetime(2026, 5, 7, 21, tzinfo=UTC)


def test_lookup_domain_without_top_asn(make_client: MakeClient) -> None:
    body = {key: value for key, value in DOMAIN_INFO.items() if key != "top_asn"}
    _, handler = recorder(body)
    assert make_client(handler).lookup_domain("example.com").top_asn is None


@pytest.mark.parametrize(
    ("domain", "path"),
    [
        ("example.com", b"/api/v4/lookup/domain/example.com"),
        ("xn--bcher-kva.example", b"/api/v4/lookup/domain/xn--bcher-kva.example"),
        ("a/b?c#d", b"/api/v4/lookup/domain/a%2Fb%3Fc%23d"),
        ("50%", b"/api/v4/lookup/domain/50%25"),
    ],
)
def test_lookup_domain_path(make_client: MakeClient, domain: str, path: bytes) -> None:
    requests, handler = recorder(DOMAIN_INFO)
    make_client(handler).lookup_domain(domain)
    assert requests[0].url.raw_path == path


@pytest.mark.parametrize("domain", ["", ".", ".."])
def test_lookup_domain_invalid(make_client: MakeClient, domain: str) -> None:
    requests, handler = recorder(DOMAIN_INFO)
    with pytest.raises(ValueError, match="invalid domain"):
        make_client(handler).lookup_domain(domain)
    assert requests == []


@pytest.mark.parametrize("limit", [0, -1, True])
def test_lookup_domain_invalid_limit(make_client: MakeClient, limit: int) -> None:
    requests, handler = recorder(DOMAIN_INFO)
    with pytest.raises(ValueError, match="limit must be a positive integer"):
        make_client(handler).lookup_domain("example.com", limit=limit)
    assert requests == []
