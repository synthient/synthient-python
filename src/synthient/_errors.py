from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from synthient._retry import parse_retry_after

if TYPE_CHECKING:
    import httpx


class SynthientError(Exception):
    """Base class for the errors the SDK raises. Invalid arguments raise ValueError instead."""


class APIConnectionError(SynthientError):
    """The request got no response, for example because of a DNS, connection, or TLS failure.

    The underlying httpx exception is chained as ``__cause__``.
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
        detail: An explanation of this occurrence.
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


class _Problem(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)

    type: str | None = None
    title: str | None = None
    detail: str | None = None
    instance: str | None = None
    errors: object = None


_problem_errors = TypeAdapter(dict[str, list[str]], config=ConfigDict(strict=True))


def api_error(response: httpx.Response, body: bytes) -> APIError:
    """Build an APIError from a response and its bounded body.

    Plain text, HTML, and malformed problem details still keep the status, request ID,
    and Retry-After. A malformed ``errors`` member is ignored on its own.
    """
    try:
        problem = _Problem.model_validate_json(body)
    except ValidationError:
        problem = _Problem()
    try:
        errors = _problem_errors.validate_python(problem.errors)
    except ValidationError:
        errors = {}
    return APIError(
        status_code=response.status_code,
        type=problem.type or "",
        title=problem.title or "",
        detail=problem.detail or "",
        errors=errors,
        request_id=response.headers.get("x-request-id") or problem.instance or "",
        retry_after=parse_retry_after(response.headers.get("retry-after")),
    )
