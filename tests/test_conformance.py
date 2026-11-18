"""Check the SDK against the OpenAPI spec it was last updated to, in .sdkgen/openapi.json."""

from __future__ import annotations

import json
import re
import types
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Union, get_args, get_origin

import httpx
import pytest
from pydantic import BaseModel, PlainValidator
from pydantic.fields import FieldInfo

import synthient
from synthient import SynthientError
from synthient._models import IPBatchResponse, _ip_address, _unix_time
from tests.conftest import MakeClient, async_client, problem

SPEC: dict[str, Any] = json.loads(
    (Path(__file__).parent.parent / ".sdkgen" / "openapi.json").read_text()
)
SCHEMAS: dict[str, dict[str, Any]] = SPEC["components"]["schemas"]

MODELS: dict[str, type[BaseModel]] = {
    "AccountInfoResponse": synthient.Account,
    "AccountAPIKey": synthient.AccountAPIKey,
    "Organization": synthient.Organization,
    "LookupQuota": synthient.LookupQuota,
    "IpBatchResponse": IPBatchResponse,
    "IpProvidersResponse": synthient.IPInfo,
    "IntelligenceInfo": synthient.Intelligence,
    "DeviceInfo": synthient.Device,
    "ProviderEntry": synthient.Provider,
    "LocationInfo": synthient.Location,
    "NetworkInfo": synthient.Network,
    "ExportListResponse": synthient.ExportPage,
    "ExportListItem": synthient.Export,
    "ExportMetaResponse": synthient.ExportMeta,
    "ExportFormatFile": synthient.ExportFile,
    "ExportEntityFile": synthient.ExportEntity,
    "ExportDailyResponse": synthient.ExportVolume,
    "ExportDailyItem": synthient.ExportVolumeDay,
    "Schema": synthient.ParquetSchema,
    "ProjectedColumn": synthient.ParquetColumn,
    "ProxyStreamEntry": synthient.ProxyEvent,
    "AnonymizerStreamEntry": synthient.AnonymizerEvent,
    "TorrentStreamEntry": synthient.TorrentEvent,
    "TorrentFileEntry": synthient.TorrentFile,
    "TorrentPeerEntry": synthient.TorrentPeer,
    "JA4TStreamEntry": synthient.JA4TEvent,
    "HoneypotMeta": synthient.HeliosMeta,
    "HoneypotHTTPStreamEntry": synthient.HeliosHTTPEvent,
    "HoneypotHTTPSStreamEntry": synthient.HeliosHTTPSEvent,
    "HoneypotDNSStreamEntry": synthient.HeliosDNSEvent,
    "HoneypotADBStreamEntry": synthient.HeliosADBEvent,
    "TLSClientHello": synthient.TLSClientHello,
    "TLSCodeName": synthient.TLSCodeName,
    "TLSExtension": synthient.TLSExtension,
}

UNMODELED = {
    "IpBatchRequest": "lookup_ips encodes the request body",
    "ProblemDetails": "parsed leniently into APIError",
    "ValidationProblemDetails": "parsed leniently into APIError",
}

# Required in the spec, but decoded with a default when absent or null.
LENIENT = {
    (synthient.ParquetSchema, "fields"): "the WHOIS meta once sent null (openapi-issues #42)"
}

# Removed from the spec, but kept in the SDK as deprecated until the next major version.
DEPRECATED_OPERATIONS = {"GET /lookup/domain/{domain}": "lookup_domain"}


def full_annotation(field: FieldInfo) -> Any:
    if field.metadata:
        return Annotated[field.annotation, *field.metadata]
    return field.annotation


def compatible(schema: dict[str, Any], annotation: Any) -> bool:  # noqa: PLR0911, PLR0912
    origin = get_origin(annotation)
    if origin is Annotated:
        base, *metadata = get_args(annotation)
        validators = {m.func for m in metadata if isinstance(m, PlainValidator)}
        if _unix_time in validators:
            return schema.get("type") == "integer" and schema.get("format") == "int64"
        if _ip_address in validators:
            return schema.get("type") == "string"
        return compatible(schema, base)
    if origin in {Union, types.UnionType}:
        return all(compatible(schema, arg) for arg in get_args(annotation) if arg is not type(None))
    if origin is Literal:
        # Some plain spec strings are typed with the values the API sends (openapi-issues #41, #47).
        values = schema.get("enum") or schema.get("x-extensible-enum")
        return schema.get("type") == "string" and (
            values is None or set(values) == set(get_args(annotation))
        )
    if "allOf" in schema:
        return all(compatible(part, annotation) for part in schema["allOf"])
    if "$ref" in schema:
        return annotation is MODELS[schema["$ref"].rsplit("/", 1)[1]]
    match schema.get("type"):
        case "integer":
            return annotation is int
        case "number":
            return annotation is float
        case "boolean":
            return annotation is bool
        case "string":
            return annotation in {str, date, datetime}
        case "array":
            return origin is list and compatible(schema["items"], get_args(annotation)[0])
        case "object" if "additionalProperties" in schema:
            return origin is dict and compatible(
                schema["additionalProperties"], get_args(annotation)[1]
            )
        case "object" if "properties" in schema:
            return (
                isinstance(annotation, type)
                and issubclass(annotation, BaseModel)
                and not (mismatches(schema, annotation))
            )
        case "object":
            return origin is dict
        case _:
            return False


def mismatches(schema: dict[str, Any], model: type[BaseModel]) -> list[str]:
    fields = {field.alias or name: field for name, field in model.model_fields.items()}
    properties: dict[str, Any] = schema.get("properties", {})
    required = set(schema.get("required", []))
    problems: list[str] = []
    for name, property_schema in properties.items():
        field = fields.get(name)
        if field is None:
            problems.append(f"{name} is not modeled")
        elif field.is_required() != (name in required) and (model, name) not in LENIENT:
            problems.append(f"{name} should be {'required' if name in required else 'optional'}")
        elif not compatible(property_schema, full_annotation(field)):
            problems.append(f"{name} has type {field.annotation}")
    unspecified = set(fields) - set(properties)
    problems.extend(f"{name} is not in the spec" for name in sorted(unspecified))
    return problems


def test_every_schema_is_accounted_for() -> None:
    assert set(SCHEMAS) == set(MODELS) | set(UNMODELED)


@pytest.mark.parametrize("name", sorted(MODELS))
def test_schema_matches_model(name: str) -> None:
    assert mismatches(SCHEMAS[name], MODELS[name]) == []


def spec_operations() -> set[str]:
    return {f"{method.upper()} {path}" for path, item in SPEC["paths"].items() for method in item}


def to_template(method: str, path: str) -> str:
    """Map a request path to the most specific spec path template that matches it."""
    path = path.removeprefix("/api/v4")
    templates = [*SPEC["paths"], *(operation.split()[1] for operation in DEPRECATED_OPERATIONS)]
    matches = [
        template
        for template in templates
        if re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), path)
    ]
    assert matches, f"{method} {path} matches no spec path"
    return f"{method} {min(matches, key=lambda template: template.count('{'))}"


def recorder() -> tuple[set[str], Callable[[httpx.Request], httpx.Response]]:
    seen: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.add(to_template(request.method, request.url.path))
        return problem(500)

    return seen, handler


@pytest.mark.filterwarnings("ignore:Domain lookup was removed:DeprecationWarning")
def test_sync_client_covers_every_operation(make_client: MakeClient) -> None:
    seen, handler = recorder()
    client = make_client(handler, max_retries=0)
    calls: list[Callable[[], object]] = [
        client.account,
        client.health,
        lambda: client.lookup_ip("8.8.8.8"),
        lambda: client.lookup_ips(["8.8.8.8"]),
        lambda: client.lookup_domain("example.com"),  # pyright: ignore[reportDeprecated]
    ]
    feeds = (
        client.proxies,
        client.anonymizers,
        client.torrents,
        client.ja4t,
        client.helios_http,
        client.helios_https,
        client.helios_dns,
        client.helios_adb,
    )
    for feed in feeds:
        calls.append(feed.list_exports)
        calls.append(feed.export_volume)
        calls.append(lambda feed=feed: feed.stream().__enter__())
        for export in ("2026-05-03", "2026-05-03/7"):
            calls.append(lambda feed=feed, export=export: feed.export_meta(export))
            calls.append(lambda feed=feed, export=export: feed.download_url(export))
    for dataset in (client.geolocation, client.whois):
        calls.append(dataset.list_exports)
        calls.append(dataset.export_volume)
        calls.append(lambda dataset=dataset: dataset.export_meta("2026-05-03"))
        calls.append(lambda dataset=dataset: dataset.download_url("2026-05-03"))
    for call in calls:
        with pytest.raises(SynthientError):
            call()
    assert seen == spec_operations() | set(DEPRECATED_OPERATIONS)


@pytest.mark.anyio
@pytest.mark.filterwarnings("ignore:Domain lookup was removed:DeprecationWarning")
async def test_async_client_covers_every_operation() -> None:
    seen, handler = recorder()
    async with async_client(handler, max_retries=0) as client:
        calls: list[Callable[[], Awaitable[object]]] = [
            client.account,
            client.health,
            lambda: client.lookup_ip("8.8.8.8"),
            lambda: client.lookup_ips(["8.8.8.8"]),
            lambda: client.lookup_domain("example.com"),  # pyright: ignore[reportDeprecated]
        ]
        feeds = (
            client.proxies,
            client.anonymizers,
            client.torrents,
            client.ja4t,
            client.helios_http,
            client.helios_https,
            client.helios_dns,
            client.helios_adb,
        )
        for feed in feeds:
            calls.append(feed.list_exports)
            calls.append(feed.export_volume)
            calls.append(lambda feed=feed: feed.stream().__aenter__())
            for export in ("2026-05-03", "2026-05-03/7"):
                calls.append(lambda feed=feed, export=export: feed.export_meta(export))
                calls.append(lambda feed=feed, export=export: feed.download_url(export))
        for dataset in (client.geolocation, client.whois):
            calls.append(dataset.list_exports)
            calls.append(dataset.export_volume)
            calls.append(lambda dataset=dataset: dataset.export_meta("2026-05-03"))
            calls.append(lambda dataset=dataset: dataset.download_url("2026-05-03"))
        for call in calls:
            with pytest.raises(SynthientError):
                await call()
    assert seen == spec_operations() | set(DEPRECATED_OPERATIONS)
