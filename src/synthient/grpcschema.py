"""Fetch the protobuf schema of the Synthient gRPC API using gRPC server reflection.

Requires the ``grpc`` extra: ``pip install 'synthient[grpc]'``.
"""

from __future__ import annotations

import queue
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from ipaddress import IPv6Address
from urllib.parse import urlsplit

try:
    import grpc
    from google.protobuf.descriptor_pb2 import FileDescriptorProto, FileDescriptorSet
    from google.protobuf.message import DecodeError
    from grpc_reflection.v1alpha.reflection_pb2 import (
        ServerReflectionRequest,
        ServerReflectionResponse,
    )
except ImportError as _error:
    raise ImportError(
        "synthient.grpcschema requires the grpc extra: pip install 'synthient[grpc]'"
    ) from _error

from synthient._base import resolve_api_key
from synthient._errors import SynthientError

__all__ = ["DEFAULT_ENDPOINT", "Schema", "SchemaError", "explain", "fetch", "normalize_endpoint"]

DEFAULT_ENDPOINT = "grpc.synthient.com:443"
"""The address of the Synthient gRPC API."""

_MAX_MESSAGE_SIZE = 32 << 20

_EXPLANATIONS = {
    grpc.StatusCode.UNAUTHENTICATED: "Missing or invalid API key.",
    grpc.StatusCode.PERMISSION_DENIED: (
        "This API key does not have permission to inspect gRPC schemas."
    ),
    grpc.StatusCode.NOT_FOUND: (
        "No protobuf schema found for the requested symbol. "
        "Fetch without symbols to list the available services."
    ),
    grpc.StatusCode.UNIMPLEMENTED: "The gRPC endpoint does not expose server reflection.",
    grpc.StatusCode.UNAVAILABLE: (
        "Could not reach the gRPC endpoint. Check the address, network access, and TLS settings."
    ),
    grpc.StatusCode.DEADLINE_EXCEEDED: (
        "Timed out reading protobuf schemas from gRPC reflection. Use a longer timeout."
    ),
}


class SchemaError(SynthientError):
    """Fetching the schema failed.

    Attributes:
        code: The gRPC status code, or None when the failure was not a gRPC status.
    """

    def __init__(self, message: str, code: grpc.StatusCode | None = None) -> None:
        super().__init__(message)
        self.code: grpc.StatusCode | None = code


@dataclass(frozen=True)
class Schema:
    """The protobuf schema returned by ``fetch``.

    Attributes:
        endpoint: The normalized address that was queried.
        symbols: The service or message names that were resolved.
        descriptor_set: The resolved files in dependency order, each file after the
            files it imports.
    """

    endpoint: str
    symbols: list[str]
    descriptor_set: FileDescriptorSet


def fetch(
    api_key: str | None = None,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    plaintext: bool = False,
    include_reflection: bool = False,
    symbols: Iterable[str] = (),
    timeout: float | None = 30.0,
) -> Schema:
    """Fetch the protobuf files that define ``symbols`` and all of their imports.

    Args:
        api_key: Sent as x-api-key metadata when not empty. Defaults to the
            SYNTHIENT_API_KEY environment variable.
        endpoint: The server as host:port, a bare host (port 443 is assumed), or a
            grpc:// or https:// URL.
        plaintext: Disables TLS, for local development servers.
        include_reflection: Includes the reflection service itself when listing services.
        symbols: Fully qualified service or message names to resolve. Empty resolves
            every service the server lists.
        timeout: Seconds allowed for the whole fetch, or None for no limit.

    Raises:
        SchemaError: If reflection fails. ``explain`` describes common causes.
        ValueError: If the endpoint is invalid.
    """
    endpoint, host = normalize_endpoint(endpoint)
    api_key = resolve_api_key(api_key)
    metadata = (("x-api-key", api_key),) if api_key else ()
    options = [("grpc.max_receive_message_length", _MAX_MESSAGE_SIZE)]
    if plaintext:
        channel = grpc.insecure_channel(endpoint, options=options)
    else:
        channel = grpc.secure_channel(endpoint, grpc.ssl_channel_credentials(), options=options)

    requests: queue.SimpleQueue[ServerReflectionRequest | None] = queue.SimpleQueue()
    with channel:
        reflection_info = channel.stream_stream(
            "/grpc.reflection.v1alpha.ServerReflection/ServerReflectionInfo",
            request_serializer=ServerReflectionRequest.SerializeToString,
            response_deserializer=ServerReflectionResponse.FromString,
        )
        responses = reflection_info(iter(requests.get, None), metadata=metadata, timeout=timeout)
        reflector = _Reflector(requests, responses, host)
        try:
            resolved = list(dict.fromkeys(s.strip() for s in symbols if s.strip()))
            if not resolved:
                resolved = reflector.list_services(include_reflection=include_reflection)
            if not resolved:
                raise SchemaError("server lists no services")

            files: dict[str, FileDescriptorProto] = {}
            for symbol in resolved:
                _add_files(
                    files, reflector.files(ServerReflectionRequest(file_containing_symbol=symbol))
                )
            while missing := _missing_dependencies(files):
                for name in missing:
                    _add_files(
                        files, reflector.files(ServerReflectionRequest(file_by_filename=name))
                    )
                    if name not in files:
                        raise SchemaError(f"reflection did not return dependency {name}")
        except grpc.RpcError as exc:
            code = exc.code() if isinstance(exc, grpc.Call) else None
            details = exc.details() if isinstance(exc, grpc.Call) else None
            raise SchemaError(details or "reflection failed", code) from exc
        finally:
            requests.put(None)
            responses.cancel()

    return Schema(
        endpoint=endpoint,
        symbols=resolved,
        descriptor_set=FileDescriptorSet(file=_ordered_files(files)),
    )


def normalize_endpoint(raw: str) -> tuple[str, str]:
    """Convert ``raw`` to host:port form and return it with the host name for TLS.

    It accepts host:port, a bare host (port 443 is assumed), and grpc:// or https://
    URLs. An empty ``raw`` uses DEFAULT_ENDPOINT.
    """
    raw = raw.strip() or DEFAULT_ENDPOINT
    try:
        if "://" in raw:
            url = urlsplit(raw)
        else:
            authority = raw.removesuffix("/")
            if authority.count(":") > 1 and not authority.startswith("["):
                authority = f"[{IPv6Address(authority)}]"
            url = urlsplit(f"//{authority}")
        host = url.hostname
        port = url.port
    except ValueError:
        raise ValueError("invalid gRPC endpoint") from None
    if not host:
        raise ValueError("empty gRPC endpoint")
    if (
        url.scheme not in {"", "grpc", "https"}
        or url.username is not None
        or url.password is not None
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
        or any(character.isspace() for character in host)
        or port == 0
        or url.netloc.endswith(":")
    ):
        raise ValueError("invalid gRPC endpoint")
    port = 443 if port is None else port
    return (f"[{host}]:{port}" if ":" in host else f"{host}:{port}"), host


def explain(error: BaseException) -> str:
    """Return a human-readable explanation of a common fetch error, or ""."""
    code = error.code if isinstance(error, SchemaError) else None
    return _EXPLANATIONS.get(code, "") if code is not None else ""


class _Reflector:
    def __init__(
        self,
        requests: queue.SimpleQueue[ServerReflectionRequest | None],
        responses: Iterator[ServerReflectionResponse],
        host: str,
    ) -> None:
        self._requests = requests
        self._responses = responses
        self._host = host

    def do(self, request: ServerReflectionRequest) -> ServerReflectionResponse:
        request.host = self._host
        self._requests.put(request)
        try:
            response = next(self._responses)
        except StopIteration:
            raise SchemaError("reflection stream ended before returning a response") from None
        if response.HasField("error_response"):
            error = response.error_response
            code = next((c for c in grpc.StatusCode if c.value[0] == error.error_code), None)
            raise SchemaError(error.error_message, code)
        return response

    def list_services(self, *, include_reflection: bool) -> list[str]:
        response = self.do(ServerReflectionRequest(list_services=""))
        if not response.HasField("list_services_response"):
            raise SchemaError("reflection response has no service list")
        names = (service.name.strip() for service in response.list_services_response.service)
        return sorted(
            name
            for name in names
            if name and (include_reflection or not name.startswith("grpc.reflection."))
        )

    def files(self, request: ServerReflectionRequest) -> list[bytes]:
        response = self.do(request)
        if not response.HasField("file_descriptor_response"):
            raise SchemaError("reflection response has no file descriptors")
        return list(response.file_descriptor_response.file_descriptor_proto)


def _add_files(files: dict[str, FileDescriptorProto], raw: list[bytes]) -> None:
    for data in raw:
        try:
            file = FileDescriptorProto.FromString(data)
        except DecodeError as exc:
            raise SchemaError("reflection returned an invalid file descriptor") from exc
        if not file.name:
            raise SchemaError("reflection returned a file descriptor without a name")
        files.setdefault(file.name, file)


def _missing_dependencies(files: dict[str, FileDescriptorProto]) -> list[str]:
    return sorted({dep for file in files.values() for dep in file.dependency if dep not in files})


def _ordered_files(files: dict[str, FileDescriptorProto]) -> list[FileDescriptorProto]:
    ordered: list[FileDescriptorProto] = []
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name not in files or name in visited:
            return
        visited.add(name)
        for dep in sorted(files[name].dependency):
            visit(dep)
        ordered.append(files[name])

    for name in sorted(files):
        visit(name)
    return ordered
