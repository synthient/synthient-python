# synthient-python

![Synthient](https://raw.githubusercontent.com/synthient/synthient-python/main/assets/readme-header.svg)

[![PyPI](https://img.shields.io/pypi/v/synthient)](https://pypi.org/project/synthient/)
[![test](https://github.com/synthient/synthient-python/actions/workflows/test.yml/badge.svg)](https://github.com/synthient/synthient-python/actions/workflows/test.yml)
[![lint](https://github.com/synthient/synthient-python/actions/workflows/lint.yml/badge.svg)](https://github.com/synthient/synthient-python/actions/workflows/lint.yml)

The official Python client for the [Synthient API](https://docs.synthient.com): IP and domain intelligence, account details, and real-time and bulk data feeds.

## Install

```sh
pip install synthient
```

Requires Python 3.11 or later.

## Usage

```python
import synthient

client = synthient.Client()  # reads SYNTHIENT_API_KEY

info = client.lookup_ip("8.8.8.8")
print(info.network.isp, info.intelligence.risk_score)
```

Look up many addresses in one request:

```python
results = client.lookup_ips(["8.8.8.8", "1.1.1.1"])
for result in results:
    print(result.ip, result.intelligence.risk_score)
```

Consume a real-time feed:

```python
with client.proxies.stream() as events:
    for event in events:
        print(event.ip, event.provider)
```

`AsyncClient` has the same API for asyncio and Trio:

```python
async with synthient.AsyncClient() as client:
    info = await client.lookup_ip("8.8.8.8")
```

## Documentation

Every class and method is documented in its docstring, including errors, retries, exports, and downloads. Runnable programs are in [examples](https://github.com/synthient/synthient-python/tree/main/examples), for example `uv run examples/lookup_ip.py 8.8.8.8`. Guides and field references are at [docs.synthient.com](https://docs.synthient.com). The `synthient.grpcschema` module fetches the protobuf schema of the Synthient gRPC API; install it with `pip install 'synthient[grpc]'`.

## License

[Apache 2.0](https://github.com/synthient/synthient-python/blob/main/LICENSE)
