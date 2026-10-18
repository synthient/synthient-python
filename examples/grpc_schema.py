"""Fetch the protobuf schema of the Synthient gRPC API through server reflection and print
its services and files. It needs the grpc extra.

    SYNTHIENT_API_KEY=... uv run --extra grpc examples/grpc_schema.py
"""

import sys

from synthient import grpcschema


def main() -> None:
    try:
        schema = grpcschema.fetch()
    except grpcschema.SchemaError as error:
        explanation = grpcschema.explain(error)
        sys.exit(f"{explanation} {error}" if explanation else str(error))

    print("endpoint:", schema.endpoint)
    for symbol in schema.symbols:
        print("service:", symbol)
    for file in schema.descriptor_set.file:
        print(f"file: {file.name} (package {file.package})")


if __name__ == "__main__":
    main()
