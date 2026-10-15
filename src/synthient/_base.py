from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from http import HTTPStatus
from importlib.metadata import version
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import TypeVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError

from synthient._errors import APIConnectionError, APITimeoutError, SynthientError
from synthient._retry import RetryPolicy

DEFAULT_BASE_URL = "https://api.synthient.com/api/v4"
DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
DEFAULT_MAX_RETRIES = 2
MAX_RESPONSE_SIZE = 16 << 20
MAX_ERROR_SIZE = 64 << 10
MAX_REDIRECTS = 10
MAX_BATCH_SIZE = 1000

# Matches Go's url.PathEscape, so "/" and "?" are escaped and IPv6 colons are kept.
_PATH_SAFE = "$&+,:;=@"
_EXPORT_ID = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})(?:/([0-9]{1,2}))?")
_USER_AGENT = f"synthient-python/{version('synthient')}"

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class ResponseMetadata:
    """Describes one HTTP response from the Synthient API.

    Attributes:
        status_code: The HTTP status code.
        request_id: The X-Request-Id header, or "".
        headers: A copy of the response headers, including RateLimit-Limit,
            RateLimit-Remaining, and RateLimit-Reset when present. Headers can contain
            sensitive data, such as presigned download URLs.
    """

    status_code: int
    request_id: str
    headers: httpx.Headers


ResponseHook = Callable[[ResponseMetadata], None]


@dataclass(frozen=True, kw_only=True)
class Request:
    path: str
    method: str = "GET"
    params: dict[str, str] | None = None
    body: bytes | None = None
    accept: str = "application/json"
    policy: RetryPolicy = RetryPolicy.TRANSIENT


@dataclass(frozen=True, kw_only=True)
class Config:
    api_key: str = field(repr=False)
    base_url: str
    max_retries: int
    timeout: httpx.Timeout
    user_agent: str
    response_hook: ResponseHook | None

    @classmethod
    def create(
        cls,
        api_key: str | None,
        *,
        base_url: str,
        max_retries: int,
        timeout: float | httpx.Timeout | None,
        user_agent: str | None,
        response_hook: ResponseHook | None,
    ) -> Config:
        api_key = resolve_api_key(api_key)
        if not api_key:
            raise ValueError("missing API key: pass api_key or set SYNTHIENT_API_KEY")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        request_timeout = httpx.Timeout(timeout)
        for value in request_timeout.as_dict().values():
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("timeout values must be finite, non-negative numbers or None")
        return cls(
            api_key=api_key,
            base_url=_parse_base_url(base_url),
            max_retries=max_retries,
            timeout=request_timeout,
            user_agent=f"{_USER_AGENT} {user_agent}" if user_agent else _USER_AGENT,
            response_hook=response_hook,
        )


class BaseTransport:
    """Request building shared by the sync and async transports."""

    def __init__(self, config: Config) -> None:
        self.config: Config = config
        self.closed: bool = False

    def build_request(
        self, http: httpx.Client | httpx.AsyncClient, request: Request
    ) -> httpx.Request:
        if self.closed:
            raise RuntimeError("the client is closed")
        headers = {
            "X-Api-Key": self.config.api_key,
            "User-Agent": self.config.user_agent,
            "Accept": request.accept,
        }
        if request.body is not None:
            headers["Content-Type"] = "application/json"
        return http.build_request(
            request.method,
            f"{self.config.base_url}{request.path}",
            params=request.params,
            content=request.body,
            headers=headers,
            timeout=self.config.timeout,
        )

    def build_storage_request(self, url: httpx.URL) -> httpx.Request:
        # Client defaults can leak credentials or replace a presigned URL's query.
        return httpx.Request(
            "GET",
            url,
            headers={"User-Agent": self.config.user_agent},
            extensions={"timeout": self.config.timeout.as_dict()},
        )

    def notify(self, response: httpx.Response) -> None:
        if self.config.response_hook is not None:
            self.config.response_hook(
                ResponseMetadata(
                    status_code=response.status_code,
                    request_id=response.headers.get("x-request-id", ""),
                    headers=httpx.Headers(response.headers),
                )
            )

    def export_location(self, response: httpx.Response) -> str:
        if response.status_code < HTTPStatus.MULTIPLE_CHOICES:
            raise SynthientError(
                f"expected a redirect to the export, got status {response.status_code}"
            )
        return str(storage_redirect(response))


def resolve_api_key(api_key: str | None) -> str:
    if api_key is None:
        api_key = os.environ.get("SYNTHIENT_API_KEY", "")
    return api_key.strip()


def _parse_base_url(raw: str) -> str:
    base_url = raw.removesuffix("/")
    try:
        url = httpx.URL(base_url)
    except httpx.InvalidURL as exc:
        raise ValueError(f"invalid base URL {raw!r}") from exc
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.query
        or url.fragment
        or re.search(r"%(?![0-9A-Fa-f]{2})", base_url)
    ):
        raise ValueError(f"invalid base URL {raw!r}")
    return base_url


def _ip(value: str | IPv4Address | IPv6Address) -> str:
    if not isinstance(value, str | IPv4Address | IPv6Address):
        raise TypeError(f"expected an IP address, got {type(value).__name__}")
    return str(ip_address(value))


def _limit(limit: int | None) -> dict[str, str]:
    if limit is None:
        return {}
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    return {"limit": str(limit)}


def lookup_ip_request(ip: str | IPv4Address | IPv6Address) -> Request:
    return Request(
        path=f"/lookup/ip/{quote(_ip(ip), safe=_PATH_SAFE)}", policy=RetryPolicy.RATE_LIMIT
    )


def lookup_ips_request(ips: Iterable[str | IPv4Address | IPv6Address]) -> Request:
    addresses: list[str] = []
    for index, ip in enumerate(ips):
        try:
            addresses.append(_ip(ip))
        except ValueError as exc:
            raise ValueError(f"invalid IP address at index {index}") from exc
    if len(addresses) > MAX_BATCH_SIZE:
        raise ValueError(f"a batch holds at most {MAX_BATCH_SIZE} addresses, got {len(addresses)}")
    return Request(
        method="POST",
        path="/lookup/ips",
        body=json.dumps({"ips": addresses}).encode(),
        policy=RetryPolicy.RATE_LIMIT,
    )


def lookup_domain_request(domain: str, limit: int | None) -> Request:
    if domain in {"", ".", ".."}:
        raise ValueError(f"invalid domain {domain!r}")
    return Request(
        path=f"/lookup/domain/{quote(domain, safe=_PATH_SAFE)}",
        params=_limit(limit),
        policy=RetryPolicy.RATE_LIMIT,
    )


def list_exports_request(feed_path: str, limit: int | None, cursor: str | None) -> Request:
    params = _limit(limit)
    if cursor:
        params["cursor"] = cursor
    return Request(path=f"{feed_path}/export", params=params)


def export_path(export: str | date) -> str:
    """Return the URL path of an export ID.

    ``export`` is "latest", a day as "YYYY-MM-DD" or a ``date``, or an hour as
    "YYYY-MM-DD/H" or a timezone-aware ``datetime``. Export.id values work as they are.
    """
    if isinstance(export, datetime):
        if export.tzinfo is None:
            raise ValueError("export datetime must be timezone-aware")
        hour = export.astimezone(UTC)
        return f"{hour:%Y-%m-%d}/{hour.hour}"
    if isinstance(export, date):
        return export.isoformat()
    if export == "latest":
        return export
    invalid = ValueError(f"invalid export ID {export!r}")
    match = _EXPORT_ID.fullmatch(export)
    if match is None:
        raise invalid
    try:
        day = date.fromisoformat(match[1])
    except ValueError:
        raise invalid from None
    if match[2] is None:
        return day.isoformat()
    hour = int(match[2])
    if hour > 23:  # noqa: PLR2004
        raise invalid
    return f"{day.isoformat()}/{hour}"


def decode(body: bytes, model: type[ModelT]) -> ModelT:
    try:
        return model.model_validate_json(body)
    except ValidationError as exc:
        raise SynthientError(f"invalid {model.__name__} in response") from exc


def connection_error(exc: httpx.TransportError) -> APIConnectionError:
    if isinstance(exc, httpx.TimeoutException):
        return APITimeoutError("request timed out")
    return APIConnectionError("connection failed")


def storage_redirect(response: httpx.Response) -> httpx.URL:
    """Validate a storage redirect before following it.

    Only http(s) targets are allowed, and an https download never downgrades to http.
    """
    location = response.headers.get("location", "").strip()
    if not response.has_redirect_location or not location:
        raise SynthientError("export redirect has no valid location")
    try:
        target = response.url.join(location)
    except httpx.InvalidURL:
        raise SynthientError("export redirect has no valid location") from None
    if target.scheme not in {"http", "https"}:
        raise SynthientError("export redirect has an unsupported scheme")
    if response.url.scheme == "https" and target.scheme != "https":
        raise SynthientError("export redirect does not use https")
    if not target.host or target.userinfo:
        raise SynthientError("export redirect has no valid location")
    return target
