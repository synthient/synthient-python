from __future__ import annotations

import queue
import time
from collections.abc import Callable, Iterator
from concurrent import futures
from typing import Any

import grpc
import pytest
from google.protobuf.descriptor_pb2 import FileDescriptorProto
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection
from grpc_reflection.v1alpha.reflection_pb2 import (
    ServerReflectionRequest,
    ServerReflectionResponse,
)

from synthient import grpcschema
from synthient.grpcschema import SchemaError, explain, fetch, normalize_endpoint

HEALTH = health_pb2.DESCRIPTOR.services_by_name["Health"].full_name


class KeyRecorder(grpc.ServerInterceptor):
    def __init__(self, delay: float) -> None:
        self.keys: queue.SimpleQueue[list[str]] = queue.SimpleQueue()
        self.delay = delay

    def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], grpc.RpcMethodHandler[Any, Any] | None],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler[Any, Any] | None:
        metadata = handler_call_details.invocation_metadata or ()
        self.keys.put([str(value) for key, value in metadata if key == "x-api-key"])
        time.sleep(self.delay)
        return continuation(handler_call_details)


@pytest.fixture
def start_server() -> Iterator[Callable[..., tuple[str, KeyRecorder]]]:
    servers: list[grpc.Server] = []

    def start(delay: float = 0.0) -> tuple[str, KeyRecorder]:
        recorder = KeyRecorder(delay)
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=4), interceptors=[recorder])
        health_pb2_grpc.add_HealthServicer_to_server(health.HealthServicer(), server)  # pyright: ignore[reportUnknownMemberType]
        reflection.enable_server_reflection([HEALTH, reflection.SERVICE_NAME], server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        servers.append(server)
        return f"127.0.0.1:{port}", recorder

    yield start
    for server in servers:
        server.stop(None).wait()


def test_fetch_all_services(start_server: Callable[..., tuple[str, KeyRecorder]]) -> None:
    address, recorder = start_server()
    schema = fetch("test-key", endpoint=address, plaintext=True)

    assert schema.endpoint == address
    assert schema.symbols == [HEALTH]
    assert "grpc_health/v1/health.proto" in [file.name for file in schema.descriptor_set.file]
    assert recorder.keys.get(timeout=1) == ["test-key"]


def test_fetch_options(
    start_server: Callable[..., tuple[str, KeyRecorder]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SYNTHIENT_API_KEY", raising=False)
    address, recorder = start_server()

    schema = fetch(endpoint=f"grpc://{address}", plaintext=True, include_reflection=True)
    assert reflection.SERVICE_NAME in schema.symbols
    assert recorder.keys.get(timeout=1) == []

    request = health_pb2.HealthCheckRequest.DESCRIPTOR.full_name
    schema = fetch("test-key", endpoint=address, plaintext=True, symbols=[f" {request} ", request])
    assert schema.symbols == [request]


def test_fetch_unknown_symbol(start_server: Callable[..., tuple[str, KeyRecorder]]) -> None:
    address, _ = start_server()
    with pytest.raises(SchemaError) as caught:
        fetch("test-key", endpoint=address, plaintext=True, symbols=["synthient.v1.Missing"])
    assert caught.value.code == grpc.StatusCode.NOT_FOUND
    assert explain(caught.value) != ""


def test_fetch_timeout(start_server: Callable[..., tuple[str, KeyRecorder]]) -> None:
    address, _ = start_server(delay=0.5)
    with pytest.raises(SchemaError) as caught:
        fetch("test-key", endpoint=address, plaintext=True, timeout=0.05)
    assert caught.value.code == grpc.StatusCode.DEADLINE_EXCEEDED
    assert "timeout" in explain(caught.value)


@pytest.mark.parametrize(
    ("raw", "endpoint", "host"),
    [
        ("", grpcschema.DEFAULT_ENDPOINT, "grpc.synthient.com"),
        ("grpc.synthient.com", "grpc.synthient.com:443", "grpc.synthient.com"),
        ("localhost:50051/", "localhost:50051", "localhost"),
        ("grpc://localhost:50051", "localhost:50051", "localhost"),
        ("https://grpc.synthient.com", "grpc.synthient.com:443", "grpc.synthient.com"),
        ("[::1]:9000", "[::1]:9000", "::1"),
        ("[::1]", "[::1]:443", "::1"),
        ("::1", "[::1]:443", "::1"),
    ],
)
def test_normalize_endpoint(raw: str, endpoint: str, host: str) -> None:
    assert normalize_endpoint(raw) == (endpoint, host)


def test_normalize_endpoint_empty() -> None:
    with pytest.raises(ValueError, match="empty gRPC endpoint"):
        normalize_endpoint("grpc://")


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://grpc.synthient.com",
        "https://user:password@grpc.synthient.com",
        "https://grpc.synthient.com/service",
        "https://grpc.synthient.com?key=value",
        "https://grpc.synthient.com#fragment",
        "localhost:notaport",
        "localhost:",
        "localhost:0",
        "localhost:65536",
        "[::1",
        "localhost/path",
        "invalid host",
    ],
)
def test_normalize_endpoint_invalid(endpoint: str) -> None:
    with pytest.raises(ValueError, match="invalid gRPC endpoint"):
        normalize_endpoint(endpoint)


def test_reflection_unexpected_eof() -> None:
    requests: queue.SimpleQueue[ServerReflectionRequest | None] = queue.SimpleQueue()
    responses: Iterator[ServerReflectionResponse] = iter(())
    reflector = grpcschema._Reflector(requests, responses, "localhost")
    with pytest.raises(SchemaError, match="stream ended"):
        reflector.list_services(include_reflection=False)


def test_reflection_invalid_descriptor() -> None:
    with pytest.raises(SchemaError, match="invalid file descriptor"):
        grpcschema._add_files({}, [b"not a protobuf message"])


@pytest.mark.parametrize(
    "code",
    [
        grpc.StatusCode.UNAUTHENTICATED,
        grpc.StatusCode.PERMISSION_DENIED,
        grpc.StatusCode.NOT_FOUND,
        grpc.StatusCode.UNIMPLEMENTED,
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DEADLINE_EXCEEDED,
    ],
)
def test_explain(code: grpc.StatusCode) -> None:
    assert explain(SchemaError("x", code)) != ""


@pytest.mark.parametrize(
    "error", [SchemaError("x", grpc.StatusCode.INTERNAL), SchemaError("x"), ValueError("x")]
)
def test_explain_without_explanation(error: Exception) -> None:
    assert explain(error) == ""


def test_ordered_files() -> None:
    def proto(name: str, *dependencies: str) -> FileDescriptorProto:
        return FileDescriptorProto(name=name, dependency=list(dependencies))

    files = {
        "a.proto": proto("a.proto", "b.proto", "c.proto"),
        "b.proto": proto("b.proto", "c.proto"),
        "c.proto": proto("c.proto"),
        "d.proto": proto("d.proto", "missing.proto"),
    }
    ordered = grpcschema._ordered_files(files)
    assert [file.name for file in ordered] == ["c.proto", "b.proto", "a.proto", "d.proto"]
    assert grpcschema._missing_dependencies(files) == ["missing.proto"]
