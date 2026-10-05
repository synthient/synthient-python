from __future__ import annotations

import json
from datetime import UTC, datetime

import pydantic
import pytest

from synthient import (
    Account,
    Export,
    ExportMeta,
    ExportPage,
    HeliosADBEvent,
    HeliosDNSEvent,
    HeliosHTTPEvent,
    HeliosHTTPSEvent,
    IPInfo,
    Network,
    ProxyEvent,
)
from tests.conftest import EXPORT, HELIOS_HTTPS, IP_INFO
from tests.test_account import ACCOUNT


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, datetime(1970, 1, 1, tzinfo=UTC)),
        (1778187600, datetime(2026, 5, 7, 21, tzinfo=UTC)),
        (253_402_300_799, datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)),
        (1_000_000_000_000, datetime(2001, 9, 9, 1, 46, 40, tzinfo=UTC)),
        (1778200137487, datetime(2026, 5, 8, 0, 28, 57, 487000, tzinfo=UTC)),
    ],
)
def test_unix_timestamps(value: int, expected: datetime) -> None:
    assert ProxyEvent.model_validate_json(f'{{"timestamp": {value}}}').timestamp == expected


@pytest.mark.parametrize(
    "value", ["true", '"1778187600"', "1.5", "999999999999", "99999999999999999999"]
)
def test_invalid_timestamps(value: str) -> None:
    with pytest.raises(pydantic.ValidationError):
        ProxyEvent.model_validate_json(f'{{"timestamp": {value}}}')


@pytest.mark.parametrize(
    "body",
    [
        '{"asn": "15169", "isp": "x", "type": "y"}',
        '{"asn": 15169.0, "isp": "x", "type": "y"}',
        '{"asn": true, "isp": "x", "type": "y"}',
        '{"asn": 15169, "isp": null, "type": "y"}',
        '{"asn": 15169, "type": "y"}',
    ],
)
def test_malformed_known_fields_are_rejected(body: str) -> None:
    with pytest.raises(pydantic.ValidationError):
        Network.model_validate_json(body)


def test_unknown_fields_are_ignored() -> None:
    info = IPInfo.model_validate_json('{"ip": "8.8.8.8", "new_section": {"a": 1}}')
    assert "new_section" not in info.model_dump()


def test_models_are_frozen() -> None:
    event = ProxyEvent(ip="1.2.3.4")
    with pytest.raises(pydantic.ValidationError):
        event.ip = "5.6.7.8"  # pyright: ignore[reportAttributeAccessIssue]


def test_renamed_fields() -> None:
    page = ExportPage.model_validate({"stream": "proxies", "feeds": [EXPORT], "next_cursor": "c"})
    assert page.exports == [Export.model_validate(EXPORT)]
    meta = ExportMeta.model_validate_json(
        '{"id": "2026-05-03", "stream": "proxies", "kind": "daily", "format": "parquet",'
        ' "date": 1777766400, "rows": 3, "size": 9, "checksum": "ab", "created_at": 1777770000,'
        ' "schema": {"fields": [{"name": "ip", "type": "string"}]}}'
    )
    assert meta.parquet_schema.fields[0].name == "ip"
    assert meta.date == datetime(2026, 5, 3, tzinfo=UTC)
    assert meta.hour is None


@pytest.mark.parametrize(
    ("model", "body"),
    [
        (IPInfo, IP_INFO),
        (Export, EXPORT),
        (Account, ACCOUNT),
        (HeliosHTTPSEvent, HELIOS_HTTPS),
        (HeliosHTTPSEvent, {**HELIOS_HTTPS, "timestamp": 1778200137000, "details": None}),
    ],
)
def test_json_round_trip(model: type[pydantic.BaseModel], body: dict[str, object]) -> None:
    value = model.model_validate_json(json.dumps(body))
    assert model.model_validate_json(value.model_dump_json(by_alias=True)) == value
    assert model.model_validate(value.model_dump(mode="json", by_alias=True)) == value
    assert model.model_validate(json.loads(json.dumps(body))) == value


def test_helios_https_details() -> None:
    body = {**HELIOS_HTTPS, "details": {**HELIOS_HTTPS["details"], "new_field": 1}}
    event = HeliosHTTPSEvent.model_validate_json(json.dumps(body))
    assert event.details is not None
    assert event.details.sni == "example.com"
    assert event.details.cipher_suites is not None
    assert event.details.cipher_suites[0].name == "TLS_AES_128_GCM_SHA256"
    assert event.details.extensions is not None
    assert event.details.extensions[0].length == 16
    assert event.details.session_id is None
    assert event.raw is None


def test_helios_https_details_unparsed() -> None:
    event = HeliosHTTPSEvent.model_validate_json(json.dumps({**HELIOS_HTTPS, "details": None}))
    assert event.details is None
    with pytest.raises(pydantic.ValidationError):
        HeliosHTTPSEvent.model_validate_json(
            json.dumps({**HELIOS_HTTPS, "details": '{"sni": "example.com"}'})
        )


def test_helios_http_details() -> None:
    event = HeliosHTTPEvent.model_validate_json(
        '{"timestamp": 1778200137487, "tunnel_id": 7, "domain": "example.com", "port": 80,'
        ' "protocol": "http", "meta": {"proxy_ip": "203.0.113.1"}, "details":'
        ' {"method": "GET", "uri": "/", "version": "HTTP/1.1", "headers": {"Host": "x"}},'
        ' "raw": "R0VUIC8gSFRUUC8xLjENCg=="}'
    )
    assert event.details is not None
    assert event.details.headers == {"Host": "x"}
    assert event.meta.proxy_ip == "203.0.113.1"
    assert event.raw == "R0VUIC8gSFRUUC8xLjENCg=="


def test_helios_dns() -> None:
    event = HeliosDNSEvent.model_validate_json(
        '{"timestamp": 1778200137487, "tunnel_id": 42, "domain": "api.ipify.org", "port": 443,'
        ' "protocol": "dns", "meta": {"server": "hp-eu-1"}}'
    )
    assert event.protocol == "dns"
    assert event.domain == "api.ipify.org"
    assert event.timestamp == datetime(2026, 5, 8, 0, 28, 57, 487000, tzinfo=UTC)


@pytest.mark.parametrize(
    ("model", "body"),
    [
        (HeliosHTTPSEvent, {key: value for key, value in HELIOS_HTTPS.items() if key != "meta"}),
        (HeliosADBEvent, {"session": "00075b3d", "sequential_id": 3, "command": "id"}),
    ],
)
def test_helios_required_fields(model: type[pydantic.BaseModel], body: dict[str, object]) -> None:
    with pytest.raises(pydantic.ValidationError):
        model.model_validate_json(json.dumps(body))
