"""The official Python client for the Synthient API: IP intelligence, account details,
and real-time and bulk data feeds.

Create a client and reuse it for every request::

    import synthient

    with synthient.Client() as client:
        info = client.lookup_ip("8.8.8.8")
        if info.intelligence is not None:
            print(info.intelligence.risk_score)

``AsyncClient`` offers the same API for asyncio and Trio.
"""

from importlib.metadata import version

from synthient._async_client import AsyncClient, AsyncDataset, AsyncFeed
from synthient._base import DEFAULT_BASE_URL, ResponseHook, ResponseMetadata
from synthient._client import Client, Dataset, Feed
from synthient._errors import APIConnectionError, APIError, APITimeoutError, SynthientError
from synthient._models import (
    Account,
    AccountAPIKey,
    ActivityStats,
    AnonymizerEvent,
    ASNCount,
    CountryCount,
    Device,
    DomainEvent,
    DomainInfo,  # pyright: ignore[reportDeprecated]
    DomainStats,
    Export,
    ExportEntity,
    ExportFile,
    ExportFormat,
    ExportMeta,
    ExportPage,
    ExportVariant,
    ExportVolume,
    ExportVolumeDay,
    HeliosADBEvent,
    HeliosDNSEvent,
    HeliosHTTPEvent,
    HeliosHTTPSEvent,
    HeliosMeta,
    HeliosTransport,
    HTTPRequestDetails,
    Intelligence,
    IPInfo,
    JA4TEvent,
    Location,
    LookupQuota,
    Network,
    Organization,
    ParquetColumn,
    ParquetSchema,
    PortCount,
    Provider,
    ProxyEvent,
    SubdomainCount,
    TimeSeriesPoint,
    TLSClientHello,
    TLSCodeName,
    TLSExtension,
    TorrentEvent,
    TorrentFile,
    TorrentPeer,
    UniqueIPs,
    WhoisEntity,
)

__version__ = version("synthient")

__all__ = [
    "DEFAULT_BASE_URL",
    "APIConnectionError",
    "APIError",
    "APITimeoutError",
    "ASNCount",
    "Account",
    "AccountAPIKey",
    "ActivityStats",
    "AnonymizerEvent",
    "AsyncClient",
    "AsyncDataset",
    "AsyncFeed",
    "Client",
    "CountryCount",
    "Dataset",
    "Device",
    "DomainEvent",
    "DomainInfo",
    "DomainStats",
    "Export",
    "ExportEntity",
    "ExportFile",
    "ExportFormat",
    "ExportMeta",
    "ExportPage",
    "ExportVariant",
    "ExportVolume",
    "ExportVolumeDay",
    "Feed",
    "HTTPRequestDetails",
    "HeliosADBEvent",
    "HeliosDNSEvent",
    "HeliosHTTPEvent",
    "HeliosHTTPSEvent",
    "HeliosMeta",
    "HeliosTransport",
    "IPInfo",
    "Intelligence",
    "JA4TEvent",
    "Location",
    "LookupQuota",
    "Network",
    "Organization",
    "ParquetColumn",
    "ParquetSchema",
    "PortCount",
    "Provider",
    "ProxyEvent",
    "ResponseHook",
    "ResponseMetadata",
    "SubdomainCount",
    "SynthientError",
    "TLSClientHello",
    "TLSCodeName",
    "TLSExtension",
    "TimeSeriesPoint",
    "TorrentEvent",
    "TorrentFile",
    "TorrentPeer",
    "UniqueIPs",
    "WhoisEntity",
    "__version__",
]
