from __future__ import annotations

import enum
import random
import re
import ssl
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http import HTTPStatus

import httpx

MAX_BACKOFF = 30.0
MAX_RETRY_AFTER = 60.0
_MAX_RETRY_AFTER_HEADER = float(2**31)
_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})


class RetryPolicy(enum.Enum):
    # Free requests retry transient statuses and transport failures.
    TRANSIENT = enum.auto()
    # Billed requests retry only 429, which the API returns before doing any work.
    RATE_LIMIT = enum.auto()


def backoff(attempt: int) -> float:
    """Return the jittered delay before retry number ``attempt``, counted from zero."""
    delay = min(2.0 ** min(attempt, 5), MAX_BACKOFF)
    return delay * random.uniform(0.75, 1.25)  # noqa: S311


def parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header as seconds or an HTTP date.

    Returns None when the header is absent or malformed. Negative and past values
    become 0, and very large values are clamped.
    """
    if value is None:
        return None
    value = value.strip()
    if re.fullmatch(r"-?[0-9]+", value):
        return min(max(float(value), 0.0), _MAX_RETRY_AFTER_HEADER)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - datetime.now(UTC)).total_seconds(), 0.0)


def retry_delay(policy: RetryPolicy, response: httpx.Response, attempt: int) -> float | None:
    """Return how long to wait before retrying ``response``, or None to stop.

    Retry-After is a minimum, and one over a minute stops retries. A 429 without
    Retry-After is not retried, since the rate limiter always sends one.
    """
    status = response.status_code
    if policy is RetryPolicy.RATE_LIMIT and status != HTTPStatus.TOO_MANY_REQUESTS:
        return None
    if status not in _TRANSIENT_STATUSES:
        return None
    retry_after = parse_retry_after(response.headers.get("retry-after"))
    if status == HTTPStatus.TOO_MANY_REQUESTS and retry_after is None:
        return None
    retry_after = retry_after or 0.0
    if retry_after > MAX_RETRY_AFTER:
        return None
    return max(backoff(attempt), retry_after)


def is_transient(exc: httpx.TransportError) -> bool:
    """Report whether a transport failure is worth retrying on a free request.

    Certificate verification failures are permanent even though httpx reports them
    as connection errors.
    """
    if not isinstance(exc, httpx.TimeoutException | httpx.NetworkError | httpx.RemoteProtocolError):
        return False
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, ssl.SSLCertVerificationError):
            return False
        cause = cause.__cause__ or cause.__context__
    return True
