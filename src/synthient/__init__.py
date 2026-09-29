"""The official Python client for the Synthient API: IP and domain intelligence, account
details, and real-time and bulk data feeds.

Create a client and reuse it for every request::

    import synthient

    with synthient.Client() as client:
        info = client.lookup_ip("8.8.8.8")
        if info.intelligence is not None:
            print(info.intelligence.risk_score)

``AsyncClient`` offers the same API for asyncio and Trio.
"""

from importlib.metadata import version

from synthient._async_client import AsyncClient, AsyncFeed
from synthient._base import DEFAULT_BASE_URL, ResponseHook, ResponseMetadata
from synthient._client import Client, Feed
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
    DomainInfo,
    DomainStats,
    Export,
    ExportMeta,
    ExportPage,
    HeliosADBEvent,
    HeliosDNSEvent,
    HeliosHTTPEvent,
    HeliosHTTPSEvent,
    HeliosMeta,
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
    TorrentEvent,
    TorrentFile,
    TorrentPeer,
    UniqueIPs,
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
    "AsyncFeed",
    "Client",
    "CountryCount",
    "Device",
    "DomainEvent",
    "DomainInfo",
    "DomainStats",
    "Export",
    "ExportMeta",
    "ExportPage",
    "Feed",
    "HTTPRequestDetails",
    "HeliosADBEvent",
    "HeliosDNSEvent",
    "HeliosHTTPEvent",
    "HeliosHTTPSEvent",
    "HeliosMeta",
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
    "TimeSeriesPoint",
    "TorrentEvent",
    "TorrentFile",
    "TorrentPeer",
    "UniqueIPs",
    "__version__",
]
