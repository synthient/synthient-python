from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from synthient._retry import parse_retry_after

if TYPE_CHECKING:
    import httpx


class SynthientError(Exception):
    """Base class for the errors the SDK raises for failed requests and unusable responses.

    It is raised directly for an unusable response, such as a body of the wrong shape or
    an invalid export redirect. Invalid arguments raise ValueError or TypeError instead.
    """


class APIConnectionError(SynthientError):
    """The connection failed, or broke while an API response or stream was being read.

    Causes include DNS, connection, and TLS failures. The underlying httpx exception is
    chained as ``__cause__``.
    """


class APITimeoutError(APIConnectionError):
    """The request timed out while connecting, sending, or waiting for data."""


class APIError(SynthientError):
    """The API answered with a non-success HTTP status.

    ``str(error)`` contains only the HTTP status. The other attributes come from the
    server's problem details and can contain sensitive data, so log them deliberately.

    Attributes:
        status_code: The HTTP status code, such as 402 when the account is out of credits.
        type: The problem type URI, or "" when the body had none.
        title: A short summary of the problem.
        detail: An explanation of this occurrence. For a plain-text error, such as the
            edge's 401 for a malformed key, it is the response body.
        errors: Validation messages by field, set on 422 responses.
        request_id: The X-Request-Id of the response, useful for support requests.
        retry_after: Seconds the server asked the caller to wait, or None.
    """

    def __init__(
        self,
        *,
        status_code: int,
        type: str = "",
        title: str = "",
        detail: str = "",
        errors: dict[str, list[str]] | None = None,
        request_id: str = "",
        retry_after: float | None = None,
    ) -> None:
        try:
            message = f"{status_code} {HTTPStatus(status_code).phrase}"
        except ValueError:
            message = str(status_code)
        super().__init__(message)
        self.status_code: int = status_code
        self.type: str = type
        self.title: str = title
        self.detail: str = detail
        self.errors: dict[str, list[str]] = errors or {}
        self.request_id: str = request_id
        self.retry_after: float | None = retry_after


def api_error(response: httpx.Response, body: bytes) -> APIError:
    """Build an APIError from a response and its bounded body.

    Plain text, HTML, and malformed problem details still keep the status, request ID,
    and Retry-After, and a plain-text body becomes the detail. A member of the wrong type
    is ignored without affecting the others.
    """
    try:
        problem: dict[str, Any] | None = json.loads(body)
    except (ValueError, RecursionError):
        problem = None
    if not isinstance(problem, dict):
        media_type = response.headers.get("content-type", "").partition(";")[0].strip().lower()
        text = body.decode("utf-8", "replace").strip() if media_type == "text/plain" else ""
        problem = {"detail": text}
    strings = {name: value for name, value in problem.items() if isinstance(value, str)}
    errors: dict[str, list[str]] | None = problem.get("errors")
    if not isinstance(errors, dict) or not all(
        isinstance(field, str)
        and isinstance(messages, list)
        and all(isinstance(message, str) for message in messages)
        for field, messages in errors.items()
    ):
        errors = {}
    return APIError(
        status_code=response.status_code,
        type=strings.get("type", ""),
        title=strings.get("title", ""),
        detail=strings.get("detail", ""),
        errors=errors,
        request_id=response.headers.get("x-request-id") or strings.get("instance", ""),
        retry_after=parse_retry_after(response.headers.get("retry-after")),
    )
