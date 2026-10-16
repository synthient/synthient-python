from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, PlainValidator

# The anonymizer and Helios HTTP, HTTPS, and DNS streams send milliseconds. As seconds,
# values this large would be tens of thousands of years away.
_MILLISECOND_THRESHOLD = 1_000_000_000_000


def _unix_time(value: object) -> datetime:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected a Unix timestamp")  # noqa: TRY004
    try:
        if value >= _MILLISECOND_THRESHOLD:
            seconds, milliseconds = divmod(value, 1000)
            return datetime.fromtimestamp(seconds, UTC) + timedelta(milliseconds=milliseconds)
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError) as exc:
        raise ValueError("Unix timestamp out of range") from exc


def _to_unix(value: datetime) -> int:
    # Serialize back to the wire form so JSON dumps validate again. Whole seconds stay
    # seconds and anything finer becomes milliseconds, which reads back unchanged.
    elapsed = value - datetime(1970, 1, 1, tzinfo=UTC)
    if value.microsecond == 0:
        return elapsed // timedelta(seconds=1)
    return elapsed // timedelta(milliseconds=1)


UnixTime = Annotated[
    datetime, PlainValidator(_unix_time), PlainSerializer(_to_unix, when_used="json")
]
"""A Unix time in seconds, or milliseconds for values of 1e12 or more, as a UTC datetime."""


def _ip_address(value: object) -> IPv4Address | IPv6Address:
    if isinstance(value, IPv4Address | IPv6Address):
        return value
    if not isinstance(value, str):
        raise ValueError("expected an IP address string")  # noqa: TRY004
    return ip_address(value)


IPAddress = Annotated[
    IPv4Address | IPv6Address, PlainValidator(_ip_address), PlainSerializer(str, when_used="json")
]
"""An IPv4 or IPv6 address, sent by the API as a string."""


class _Model(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        strict=True,
        validate_by_name=True,
        use_attribute_docstrings=True,
    )


class Network(_Model):
    """The network that announces an address."""

    asn: int
    """The autonomous system number, or 0 if the address is not routed."""
    isp: str
    type: str
    """The network type, such as "RESIDENTIAL", "MOBILE", or "DATACENTER".

    New values may be added.
    """
    org: str | None = None
    """The organization the address is registered to."""
    domain: str | None = None
    """The network operator's domain."""
    abuse_email: str | None = None
    abuse_phone: str | None = None


class Location(_Model):
    """The geolocation of an address."""

    country: str
    """The ISO 3166-1 alpha-2 country code."""
    state: str
    """The state or region."""
    city: str
    timezone: str
    """The IANA time zone name."""
    latitude: float
    """The latitude in degrees."""
    longitude: float
    """The longitude in degrees."""
    geo_hash: str


class Provider(_Model):
    """A proxy, VPN, or anonymizer service seen using an address."""

    name: str = Field(alias="provider")
    """The service tag, such as "BRIGHTDATA"."""
    type: str
    """The service category."""
    last_seen: UnixTime


class Device(_Model):
    """A device type seen behind an address."""

    os: str
    """The operating system, such as "Android", "iOS", or "Windows".

    New values may be added.
    """
    version: str
    """The operating system version."""
    last_seen: UnixTime


class Intelligence(_Model):
    """Synthient's assessment of an address."""

    risk_score: int
    """The risk of the address, from 0 to 100."""
    categories: list[str]
    """The kinds of services seen on the address, such as "RESIDENTIAL_PROXY".

    New values may be added.
    """
    behavior: list[str]
    """Behaviors observed from the address. New values may be added."""
    providers: list[Provider]
    """The proxy, VPN, and anonymizer services seen using the address.

    An empty list does not mean the address is clean.
    """
    devices: list[Device]
    """The device types seen behind the address."""


class IPInfo(_Model):
    """The intelligence Synthient holds for an IP address.

    ``network``, ``location``, and ``intelligence`` are None when the API has no data for them.
    """

    ip: IPAddress
    """The address that was looked up."""
    network: Network | None = None
    """The network that announces the address."""
    location: Location | None = None
    intelligence: Intelligence | None = None
    """The risk score and the proxy, VPN, and device observations for the address."""


class IPBatchResponse(_Model):
    results: list[IPInfo]


class HealthResponse(_Model):
    pass


class DomainStats(_Model):
    """Aggregate event counts for a domain."""

    events_24h: int
    total_events_30d: int


class UniqueIPs(_Model):
    """The distinct source addresses seen for a domain."""

    value_24h: int
    value_30d: int
    sparkline_24h: list[int]
    """Hourly counts for the last 24 hours."""


class TimeSeriesPoint(_Model):
    """The event counts for one day."""

    date: UnixTime
    """The start of the day."""
    events: int
    unique_ips: int
    """The number of distinct source addresses that day."""


class ASNCount(_Model):
    """An autonomous system and its event count."""

    asn: int
    events: int


class SubdomainCount(_Model):
    """A subdomain and its event count."""

    subdomain: str
    count: int
    """The number of events that targeted it."""


class PortCount(_Model):
    """A port and its event count."""

    port: int
    count: int
    """The number of events that targeted it."""


class CountryCount(_Model):
    """A source country and its counts."""

    country_code: str
    """The ISO 3166-1 alpha-2 country code."""
    events: int
    unique_ips: int


class ActivityStats(_Model):
    """The hourly activity pattern of a domain."""

    cadence: str
    """A sentence describing how events are spread over time, such as
    "Activity is burst-driven with significant peaks."
    """
    median_per_hour: int
    p95_per_hour: int
    peak_hour: int
    """The busiest UTC hour of the day over the last 30 days."""
    quiet_hour: int
    """The quietest UTC hour of the day over the last 30 days."""


class DomainEvent(_Model):
    """A single honeypot event for a domain."""

    timestamp: UnixTime
    source_ip_masked: str
    """The proxy exit address that carried the event. Despite the name, it is not masked."""
    target_subdomain: str
    country_code: str
    """The source country."""
    port: int


class DomainInfo(_Model):
    """The honeypot traffic Synthient has observed for a domain."""

    domain: str
    """The domain that was looked up."""
    status: str
    """"active" when the domain had events in the last 24 hours, otherwise "inactive".

    A domain that has never been observed is "inactive" with zeroed stats.
    """
    stats: DomainStats
    """Aggregate event counts."""
    unique_ips: UniqueIPs
    """The distinct source addresses."""
    time_series: list[TimeSeriesPoint]
    """Daily event counts."""
    top_asn: ASNCount | None = None
    """The network that sent the most events, or None when there are none."""
    top_subdomains: list[SubdomainCount]
    """The most targeted subdomains."""
    top_ports: list[PortCount]
    """The most targeted ports."""
    geo_distribution: list[CountryCount]
    """Event counts by source country."""
    hour_dow_heatmap: list[list[int]]
    """Event counts over the last 30 days as 7 rows by 24 columns, all in UTC.

    Rows are days of the week from Monday (0) to Sunday (6), and columns are hours of the day.
    """
    activity_stats: ActivityStats
    """The hourly activity pattern."""
    recent_events: list[DomainEvent]
    """The most recent events, capped by the ``limit`` passed to ``lookup_domain``."""


class Organization(_Model):
    """The organization an account belongs to."""

    id: str
    name: str
    relation: str
    """The account's role, such as "OWNER", "ADMIN", or "MEMBER"."""


class LookupQuota(_Model):
    """The lookup credits available to an account."""

    credits: int
    """The number of lookup credits remaining."""
    resets_in: int
    """Seconds until the credits reset."""


class AccountAPIKey(_Model):
    """The API key a request was made with."""

    id: str
    """The key ID. It is not the secret key."""
    name: str
    created_at: Annotated[datetime, Field(strict=False)]


class Account(_Model):
    """The account that owns an API key."""

    email: str
    first_name: str
    last_name: str
    organization: Organization
    scopes: list[str]
    """The permissions granted to the key, such as "BASIC" or "PROXY_FEEDS".

    New values may be added.
    """
    lookup_quota: LookupQuota
    """The remaining lookup credits."""
    api_key: AccountAPIKey | None = None
    """The API key used for the request."""


class Export(_Model):
    """A parquet export of a feed."""

    id: str
    """Identifies the export as "YYYY-MM-DD", "YYYY-MM-DD/H", or "latest".

    Pass it to ``download``. On the first page of a listing, the newest hourly export is
    listed only as "latest", a moving alias for the freshest export; its ``date`` and
    ``hour`` give its stable ID.
    """
    kind: str
    """Either "hourly" or "daily"."""
    date: Annotated[date, Field(strict=False)]
    """The UTC day of the export."""
    hour: int | None = None
    """The UTC hour of an hourly export, or None for a daily export."""
    size_bytes: int
    row_count: int
    checksum: str
    """The hex-encoded SHA-256 of the parquet file."""
    created_at: UnixTime
    download_path: str
    """The API path of the export's download, including the version prefix."""


class ExportPage(_Model):
    """One page of a feed's exports, newest first."""

    stream: str
    """The feed's stream identifier, such as "proxies"."""
    exports: list[Export] = Field(alias="feeds")
    next_cursor: str | None = None
    """Fetches the next page. It is None or "" on the last page."""


class ParquetColumn(_Model):
    """A column of a parquet export."""

    name: str
    type: str
    """The column type, such as "string" or "int64"."""


class ParquetSchema(_Model):
    """The columns of a parquet export."""

    fields: list[ParquetColumn]
    """The columns in file order."""


class ExportMeta(_Model):
    """A parquet export's metadata, without its contents."""

    id: str
    stream: str
    kind: str
    """Either "hourly" or "daily"."""
    format: str
    """The file format, "parquet"."""
    date: UnixTime
    """The start of the export's UTC day or hour."""
    hour: int | None = None
    """The UTC hour of an hourly export, or None for a daily export."""
    rows: int
    size: int
    """The size of the parquet file in bytes."""
    checksum: str
    """The hex-encoded SHA-256 of the parquet file."""
    created_at: UnixTime
    parquet_schema: ParquetSchema = Field(alias="schema")
    """The parquet columns."""


class ProxyEvent(_Model):
    """A proxy sighting from the proxies feed."""

    ip: str | None = None
    """The proxy exit address."""
    provider: str | None = None
    """The proxy service tag."""
    type: str | None = None
    """The proxy category, such as "RESIDENTIAL_PROXY"."""
    country_code: str | None = None
    """The ISO 3166-1 alpha-2 country of the address."""
    asn: int | None = None
    timestamp: UnixTime | None = None


class AnonymizerEvent(_Model):
    """A VPN or anonymizer IP range from the anonymizers feed."""

    range_start: str | None = None
    range_end: str | None = None
    provider: str | None = None
    """The service tag."""
    type: str | None = None
    """The service category, such as "COMMERCIAL_VPN"."""
    country_code: str | None = None
    """The ISO 3166-1 alpha-2 country of ``range_start``."""
    asn: int | None = None
    """The autonomous system number of ``range_start``."""
    timestamp: UnixTime | None = None


class TorrentFile(_Model):
    """A file in a torrent."""

    path: str | None = None
    length: int | None = None
    """The file size in bytes."""


class TorrentPeer(_Model):
    """A peer seen sharing a torrent."""

    ip: str | None = None
    port: int | None = None
    source: str | None = None
    """Where the peer was found, such as "DHT", "PEX", or "tracker"."""
    encrypted: bool | None = None
    """Whether the peer supports encrypted connections."""


class TorrentEvent(_Model):
    """A torrent observed on the BitTorrent DHT or a tracker."""

    info_hash: str | None = None
    """The hex-encoded SHA-1 info hash."""
    name: str | None = None
    magnet_uri: str | None = None
    total_size: int | None = None
    """The total size of the torrent's files in bytes."""
    piece_length: int | None = None
    """The piece size in bytes."""
    file_count: int | None = None
    files: list[TorrentFile] | None = None
    peers: list[TorrentPeer] | None = None
    timestamp: UnixTime | None = None


class JA4TEvent(_Model):
    """A JA4T TCP fingerprint sighting."""

    ip: str | None = None
    """The address the fingerprint was seen from."""
    ja4t: str | None = None
    provider: str | None = None
    """The service tag of the address."""
    type: str | None = None
    """The service category, such as "DATACENTER_PROXY"."""
    country_code: str | None = None
    """The ISO 3166-1 alpha-2 country of the address."""
    asn: int | None = None
    timestamp: UnixTime | None = None


class HeliosMeta(_Model):
    """The Helios honeypot sensor that captured an event."""

    proxy_ip: str | None = None
    """The proxy exit address the traffic arrived through."""
    server: str | None = None
    """The sensor that captured the traffic."""
    pool_id: str | None = None
    """The sensor pool."""
    provider: str | None = None
    """The proxy service the traffic arrived through."""


class HTTPRequestDetails(_Model):
    """A parsed HTTP request."""

    method: str | None = None
    uri: str | None = None
    """The request target."""
    version: str | None = None
    """The HTTP version, such as "HTTP/1.1"."""
    headers: dict[str, str] | None = None


class HeliosHTTPEvent(_Model):
    """An HTTP request captured by a Helios honeypot."""

    timestamp: UnixTime
    tunnel_id: int
    """Identifies the proxy tunnel the request arrived through."""
    domain: str
    """The destination host the tunnel was opened to."""
    port: int
    """The destination port."""
    protocol: str
    """Always "http"."""
    meta: HeliosMeta
    """The capturing sensor."""
    details: HTTPRequestDetails | None = None
    """The parsed request."""
    raw: str | None = None
    """The base64-encoded raw request."""


class TLSCodeName(_Model):
    """A TLS registry code point and its name."""

    code: int | None = None
    name: str | None = None
    """The registered name. GREASE values read as "GREASE(0x....)"."""


class TLSExtension(_Model):
    """A TLS extension in a ClientHello."""

    code: int | None = None
    name: str | None = None
    length: int | None = None
    """The length of the extension body in bytes."""


class TLSClientHello(_Model):
    """A TLS ClientHello as parsed by a Helios sensor.

    Fields the sensor could not parse are None, and new fields may be added.
    """

    record_version: str | None = None
    handshake_version: str | None = None
    """The handshake version, such as "TLS 1.2"."""
    client_random: str | None = None
    """The hex-encoded 32-byte client random."""
    session_id: str | None = None
    """The hex-encoded session ID."""
    session_id_length: int | None = None
    """The session ID length in bytes."""
    sni: str | None = None
    """The server_name extension value."""
    alpn: list[str] | None = None
    """The offered application protocols, such as "h2"."""
    cipher_suites: list[TLSCodeName] | None = None
    compression_methods: list[int] | None = None
    extensions: list[TLSExtension] | None = None
    """The extensions in wire order."""
    supported_versions: list[str] | None = None
    supported_groups: list[TLSCodeName] | None = None
    """The supported key exchange groups."""
    signature_algorithms: list[TLSCodeName] | None = None
    ec_point_formats: list[str] | None = None
    key_share_groups: list[str] | None = None
    """The groups the client sent key shares for."""
    psk_key_exchange_modes: list[str] | None = None
    """The supported pre-shared key exchange modes."""
    compress_certificate: list[str] | None = None
    """The supported certificate compression algorithms."""
    has_grease: bool | None = None
    application_settings: bool | None = None
    delegated_credentials: bool | None = None
    encrypt_then_mac: bool | None = None
    extended_master_secret: bool | None = None
    post_handshake_auth: bool | None = None
    renegotiation_info: bool | None = None
    signed_certificate_timestamps: bool | None = None
    status_request: bool | None = None


class HeliosHTTPSEvent(_Model):
    """A TLS ClientHello captured by a Helios honeypot."""

    timestamp: UnixTime
    tunnel_id: int
    """Identifies the proxy tunnel the handshake arrived through."""
    domain: str
    """The destination host the tunnel was opened to."""
    port: int
    """The destination port."""
    protocol: str
    """Always "https"."""
    meta: HeliosMeta
    """The capturing sensor."""
    details: TLSClientHello | None = None
    """The parsed ClientHello, or None when the sensor could not parse the handshake."""
    raw: str | None = None
    """Always None. The HTTPS sensors do not capture handshake bytes."""


class HeliosDNSEvent(_Model):
    """A tunnel destination captured by the Helios DNS honeypot.

    ``domain`` is where the tunnel was opened to, not a DNS query name. Names resolved
    inside a tunneled DNS session are not visible.
    """

    timestamp: UnixTime
    tunnel_id: int
    """Identifies the proxy tunnel."""
    domain: str
    """The destination host, or an address literal, the tunnel was opened to."""
    port: int
    """The destination port."""
    protocol: str
    """Always "dns"."""
    meta: HeliosMeta
    """The capturing sensor."""


class HeliosADBEvent(_Model):
    """An Android Debug Bridge shell command captured by a Helios honeypot.

    Events carry no timestamp because the sensors send none.
    """

    session: str
    """Identifies the ADB session as 32 hex characters."""
    sequential_id: int
    """The 1-based position of the command within its session."""
    command: str
    """The shell command as plain text."""
    hash: str
    """The hex-encoded SHA-256 the sensor assigns to the session's payload.

    It is not a hash of ``command``: every command in a session shares it, and sessions
    that replay the same script share it too.
    """
