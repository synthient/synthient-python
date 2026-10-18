<p align="center">
  <a href="https://synthient.com">
    <img src="https://raw.githubusercontent.com/synthient/synthient-python/main/assets/logo.svg" height="96" alt="Synthient logo">
  </a>
</p>
<h3 align="center">Synthient</h3>
<p align="center">High-fidelity data on residential proxies, VPNs, and other anonymized IPs.</p>
<p align="center">
  <a href="https://docs.synthient.com">Documentation</a> ·
  <a href="https://pypi.org/project/synthient/">PyPI</a> ·
  <a href="https://docs.synthient.com/misc/support">Support</a>
</p>

# synthient-python

`synthient-python` is the official Python SDK for the [Synthient API](https://docs.synthient.com): IP and domain intelligence, account details, and real-time and bulk data feeds. It has a sync client and an async client for asyncio and Trio.

[![PyPI](https://img.shields.io/pypi/v/synthient)](https://pypi.org/project/synthient/)
[![test](https://github.com/synthient/synthient-python/actions/workflows/test.yml/badge.svg)](https://github.com/synthient/synthient-python/actions/workflows/test.yml)
[![lint](https://github.com/synthient/synthient-python/actions/workflows/lint.yml/badge.svg)](https://github.com/synthient/synthient-python/actions/workflows/lint.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/synthient/synthient-python/blob/main/LICENSE)

## Table of Contents

- [synthient-python](#synthient-python)
  - [SDK Installation](#sdk-installation)
  - [Requirements](#requirements)
  - [API Keys](#api-keys)
  - [Authentication](#authentication)
  - [SDK Example Usage](#sdk-example-usage)
  - [Available Resources and Operations](#available-resources-and-operations)
  - [Streaming](#streaming)
  - [Pagination](#pagination)
  - [Downloads](#downloads)
  - [Retries](#retries)
  - [Timeouts](#timeouts)
  - [Error Handling](#error-handling)
  - [Server Selection](#server-selection)
  - [Custom HTTP Client](#custom-http-client)
  - [Debugging](#debugging)
  - [gRPC Schema](#grpc-schema)
- [Development](#development)
  - [Maturity](#maturity)
  - [Contributions](#contributions)
  - [License](#license)

## SDK Installation

The package is published to [PyPI](https://pypi.org/project/synthient/) as `synthient`.

### pip

```sh
pip install synthient
```

### uv

```sh
uv add synthient
```

### Poetry

```sh
poetry add synthient
```

The [gRPC schema fetcher](#grpc-schema) needs the `grpc` extra, such as `pip install 'synthient[grpc]'`.

## Requirements

Python 3.11 or later. The SDK depends on `httpx`, `pydantic` 2, and `anyio`. The `grpc` extra adds `grpcio`, `grpcio-reflection`, and `protobuf`.

## API Keys

Every request to the HTTP API needs a Synthient API key. Keys are issued from the Synthient dashboard; if you don't have one yet, [contact us](mailto:contact@synthient.com). Each key carries scopes that decide what it can call. `BASIC` covers the lookups and `account()`, and each feed has one scope for its real-time stream and one for its exports, listed under [Feeds](#feeds). See [Authentication](https://docs.synthient.com/authentication) for how scopes work.

If a request fails even though you are sending a key, it is usually one of these:

- **401 Unauthorized**: the key is malformed, unknown, or revoked. Check that it was loaded from the environment and isn't truncated.
- **402 Payment Required**: the team is out of lookup credits. `account()` reports the remaining balance and when it resets.
- **403 Forbidden**: the key lacks the scope for that endpoint or feed. `account()` lists the key's scopes.

## Authentication

This SDK supports the following security scheme:

| Name      | Type    | Scheme             | Environment Variable |
| --------- | ------- | ------------------ | -------------------- |
| `api_key` | API key | `x-api-key` header | `SYNTHIENT_API_KEY`  |

A client created without `api_key` reads the key from the `SYNTHIENT_API_KEY` environment variable:

```python
import synthient

client = synthient.Client()
```

To supply the key another way, such as from a secret manager, pass it as `api_key` rather than writing it into your code:

```python
client = synthient.Client(api_key=api_key)
```

A missing key raises `ValueError`. Creating a client makes no network calls, so a key the API rejects shows up as a 401 on the first request. The key is sent only to the Synthient API, never to the storage host that serves export downloads.

## SDK Example Usage

### Look up an IP address

Get the network, location, and risk intelligence for an IPv4 or IPv6 address. Each lookup costs one credit, and failed lookups are free.

```python
import synthient

with synthient.Client() as client:
    info = client.lookup_ip("8.8.8.8")
    if info.intelligence is not None:
        print(info.intelligence.risk_score, info.intelligence.categories)
```

`network`, `location`, and `intelligence` are `None` when the API has no data for that section, so check them before reading their fields. A client pools connections and is safe to share across threads, so create one, reuse it, and close it with `close()` or a `with` block. The examples below reuse an open `client`, so run them inside a `with` block like the one above. Runnable programs are in [examples](https://github.com/synthient/synthient-python/tree/main/examples), for example `uv run examples/lookup_ip.py 8.8.8.8`.

### Look up many IP addresses

Look up as many as 1000 addresses in one request, billed at `ceil(n * 0.9)` credits for the `n` distinct addresses looked up. Private and reserved addresses are skipped and duplicates collapse into one result, so match results by `ip` rather than by position.

```python
results = client.lookup_ips(["8.8.8.8", "1.1.1.1"])
for result in results:
    if result.intelligence is not None:
        print(result.ip, result.intelligence.risk_score)
```

`ip` is an `ipaddress.IPv4Address` or `IPv6Address`, so compare it with `ipaddress.ip_address()` of the address you sent, not with the string.

### Look up a domain

Get the honeypot traffic observed for a domain, with its most recent events. A domain that has never been observed returns the status `"inactive"` and zero counts rather than an error. Each lookup costs one credit.

```python
domain = client.lookup_domain("example.com", limit=10)
print(domain.status, domain.stats.events_24h)
for event in domain.recent_events:
    print(event.timestamp, event.target_subdomain, event.port)
```

### Check your account

Get the account that owns the API key, with the key's scopes and the team's remaining lookup credits. It costs no credits.

```python
account = client.account()
print(account.organization.name, account.scopes, account.lookup_quota.credits)
```

### Use asyncio or Trio

`AsyncClient` takes the same arguments as `Client` and has the same methods as coroutines. It runs on asyncio and Trio through AnyIO. Use one client per event loop:

```python
import asyncio

import synthient


async def main() -> None:
    async with synthient.AsyncClient() as client:
        info = await client.lookup_ip("8.8.8.8")
        if info.intelligence is not None:
            print(info.intelligence.risk_score)


asyncio.run(main())
```

With Trio, run `main` with `trio.run(main)` instead. A few things differ from `Client`:

- Close the client with `aclose()` or an `async with` block.
- `stream()` and `download()` are async context managers, entered with `async with`.
- Events from `stream()` and exports from `iter_exports()` are read with `async for`, and a download's body with `aiter_bytes()`.
- `http_client` takes an `httpx.AsyncClient`.

## Available Resources and Operations

<details open>
<summary>Available methods</summary>

### Client

- `lookup_ip` - Look up an IPv4 or IPv6 address
- `lookup_ips` - Look up as many as 1000 addresses in one request
- `lookup_domain` - Look up the honeypot traffic observed for a domain
- `account` - Get the account that owns the API key, the key's scopes, and the team's remaining lookup credits
- `health` - Check that the API and its dependencies are healthy

### Feeds

Each feed is an attribute of the client, such as `client.proxies`, and has the same methods:

- `stream` - Open the feed's real-time stream and iterate over its events
- `list_exports` - List one page of the feed's Parquet exports, newest first
- `iter_exports` - Iterate over every export, fetching pages as needed
- `export_meta` - Get an export's checksum, size, row count, and Parquet schema
- `download` - Download an export's Parquet file
- `download_url` - Get a presigned URL for an export's Parquet file, valid for 24 hours

| Attribute      | Data                                          | Event type         | Stream scope            | Export scope          |
| -------------- | --------------------------------------------- | ------------------ | ----------------------- | --------------------- |
| `proxies`      | Residential, mobile, and datacenter proxies   | `ProxyEvent`       | `PROXY_FIREHOSE`        | `PROXY_FEEDS`         |
| `anonymizers`  | VPN and anonymizer IP ranges                  | `AnonymizerEvent`  | `ANONYMIZERS_STREAM`    | `ANONYMIZERS_FEED`    |
| `torrents`     | BitTorrent DHT and tracker peers              | `TorrentEvent`     | `TORRENTS_STREAM`       | `TORRENTS_FEED`       |
| `ja4t`         | JA4T TCP fingerprints                         | `JA4TEvent`        | `JA4T_STREAM`           | `JA4T_FEED`           |
| `helios_http`  | Helios honeypot HTTP requests                 | `HeliosHTTPEvent`  | `HONEYPOT_HTTP_STREAM`  | `HONEYPOT_HTTP_FEED`  |
| `helios_https` | Helios honeypot TLS ClientHellos              | `HeliosHTTPSEvent` | `HONEYPOT_HTTPS_STREAM` | `HONEYPOT_HTTPS_FEED` |
| `helios_dns`   | Helios honeypot tunnel destinations           | `HeliosDNSEvent`   | `HONEYPOT_DNS_STREAM`   | `HONEYPOT_DNS_FEED`   |
| `helios_adb`   | Helios honeypot Android Debug Bridge commands | `HeliosADBEvent`   | `HONEYPOT_ADB_STREAM`   | `HONEYPOT_ADB_FEED`   |

</details>

## Streaming

`stream()` opens a feed's real-time stream as a context manager that yields an iterator over its events. The connection opens when the `with` block is entered and closes when it exits, even after a `break`. The server keeps a stream open for up to 30 minutes and then closes it, which ends the loop without an error. The API has no resume cursor, so open the stream again to reconnect:

```python
import time

while True:
    try:
        with client.proxies.stream() as events:
            for event in events:
                print(event.ip, event.provider, event.type)
    except synthient.SynthientError as error:
        if isinstance(error, synthient.APIError) and error.status_code in {401, 403}:
            raise
        print("proxy stream:", error)
        # Pause after a failure so a failing stream doesn't reconnect in a tight loop.
        time.sleep(2)
```

The loop stops on a 401 or 403, because reconnecting can't fix a rejected key or a missing scope. Any other failure, such as a dropped connection or another non-success response, raises a `SynthientError`, and the loop waits 2 seconds before reconnecting. After the server's routine close, it reconnects at once. A 429 `APIError` without `retry_after` means the key already has too many open streams.

## Pagination

`list_exports()` returns one page of a feed's exports, newest first:

```python
page = client.proxies.list_exports(limit=50)
for export in page.exports:
    print(export.id, export.kind, export.row_count)
```

To get the next page, pass `page.next_cursor` as `cursor`. The last page has no cursor. `iter_exports()` does the paging for you. It fetches pages as the loop needs them and stops fetching when the loop breaks:

```python
for export in client.proxies.iter_exports():
    print(export.id, export.kind, export.row_count)
```

On the first page, the newest hourly export has the ID `"latest"`, a moving alias for the freshest export. To keep a stable ID, build it from the export's `date` and `hour`, such as `"2026-10-14/21"`.

## Downloads

Exports are Parquet files. Identify one by the `id` of an `Export` from a listing, by `"latest"` for the freshest one, by a `date` or a `"YYYY-MM-DD"` string for a UTC day, or by a timezone-aware `datetime` or a `"YYYY-MM-DD/H"` string for a UTC hour. `download()` is a context manager that yields the streaming `httpx.Response`; read its body with `iter_bytes()`:

```python
with (
    client.proxies.download("latest") as response,
    open("proxies.parquet", "wb") as file,
):
    for chunk in response.iter_bytes():
        file.write(chunk)
```

The file comes from a presigned storage URL that is requested without your API key. Reading the body is plain httpx, so a failure partway through raises an httpx exception, such as `httpx.ReadTimeout` or `httpx.ReadError`, rather than a `SynthientError`, and leaves a partial file. `export_meta()` returns the SHA-256 checksum to verify the file against; ask both calls for the same stable ID, because `"latest"` can move to a newer export between them. `download_url()` returns the presigned URL itself for tools that read Parquet over HTTPS. The URL grants access without a key for 24 hours, so treat it as a secret.

An hourly export is available until its day's daily export is rolled up, shortly after 00:30 UTC the next day. After that, requests for the hour return a 400. While a daily export is being rolled up, requests for it return a 404 with `retry_after` set.

## Retries

The client retries a failed request up to twice by default, backing off exponentially with jitter and honoring `Retry-After`:

- Account and feed requests are retried on 500, 502, 503, and 504 responses, on 429 responses that carry `Retry-After`, and on network failures and timeouts before a response arrives (not on certificate or proxy errors). A failure while reading a response body or stream is not retried, and neither is the storage request that `download()` makes after the API's redirect.
- Billed lookups (`lookup_ip`, `lookup_ips`, and `lookup_domain`) are retried only on 429 responses that carry `Retry-After`, which the rate limiter returns before doing any work, so a lookup is never charged twice. `health()` is retried the same way, so an unhealthy API is reported at once.

A 429 without `Retry-After` means the key's monthly request budget is spent or it has too many open streams, so it is raised without a retry. A `Retry-After` longer than a minute is raised to you instead of waited out.

To change the number of retries, pass `max_retries` when creating the client. Zero disables retries:

```python
client = synthient.Client(max_retries=4)
```

## Timeouts

By default the client waits up to 10 seconds to connect and up to 60 seconds for each read or write. The API sends a keepalive line every 15 seconds, so open streams stay within the read timeout. Pass `timeout` as seconds or as an `httpx.Timeout` to change it, or `None` to disable it:

```python
import httpx

client = synthient.Client(timeout=httpx.Timeout(30.0, connect=5.0))
```

A timeout raises `APITimeoutError`, except while you read a `download()` body, where httpx raises `httpx.ReadTimeout`. `timeout` bounds each network operation rather than a whole call, and retries add to the total. To bound a whole call on `AsyncClient`, wrap it in `asyncio.timeout()` or `anyio.fail_after()`.

## Error Handling

A request that fails with a non-success HTTP status raises an `APIError`. It has the following attributes:

| Attribute     | Type                   | Description                                                                                 |
| ------------- | ---------------------- | ------------------------------------------------------------------------------------------- |
| `status_code` | `int`                  | HTTP response status code, such as `404`                                                    |
| `type`        | `str`                  | URI identifying the problem type, such as `https://docs.synthient.com/errors/validation-failed` |
| `title`       | `str`                  | Short summary of the problem type                                                           |
| `detail`      | `str`                  | Explanation of this occurrence. For a plain-text error, the response body                   |
| `errors`      | `dict[str, list[str]]` | Validation messages by request field                                                        |
| `request_id`  | `str`                  | Request ID to give Synthient support                                                        |
| `retry_after` | `float` or `None`      | Seconds the server asked you to wait before retrying, or `None`                             |

`str(error)` returns only the HTTP status. The other attributes hold untrusted server data that can be sensitive, so log them deliberately.

### Example

```python
try:
    client.lookup_ip("8.8.8.8")
except synthient.APIError as error:
    if error.status_code == 402:
        print("out of lookup credits")
    elif error.status_code == 429 and error.retry_after is not None:
        print("rate limited, retry after", error.retry_after)
    elif error.status_code == 429:
        print("monthly request budget spent")
    else:
        print("request", error.request_id, "failed:", error.detail or error.title)
```

### Error Types

- `SynthientError`: the base class of the errors the SDK raises for failed requests and unusable responses. A response it can't use, such as a body that doesn't match the expected shape or an invalid export redirect, raises it directly.
- `APIError`: the API, or the storage host during `download()`, returned a non-success status.
- `APIConnectionError`: the connection failed, such as after a DNS, connection, or TLS failure, or broke while an API response or stream was being read. The httpx exception is chained as `__cause__`.
- `APITimeoutError`: a subclass of `APIConnectionError` for a request that timed out.
- `grpcschema.SchemaError`: fetching the [gRPC schema](#grpc-schema) failed.
- `ValueError`: an argument is invalid, such as a missing API key or a malformed IP address. No request is sent. An API key, IP address, domain, or export ID of the wrong type raises `TypeError` instead.
- `RuntimeError`: the client was used after `close()` or `aclose()`.

<details><summary>Status codes</summary>

<br />

| Status | Meaning                                                                                                            |
| ------ | ------------------------------------------------------------------------------------------------------------------ |
| 400    | The request is invalid, such as asking for an hourly export that has been rolled up                                |
| 401    | The API key is malformed, unknown, or revoked                                                                      |
| 402    | The team is out of lookup credits                                                                                  |
| 403    | The key lacks the scope for the endpoint or feed                                                                   |
| 404    | The export does not exist, or a daily export is still being rolled up and `retry_after` is set                     |
| 422    | The input is invalid, such as a private or reserved address, a malformed domain, or a batch with no usable address |
| 429    | With `retry_after`, the rate limit was hit. Without it, the request budget is spent or too many streams are open   |
| 5xx    | The server failed. Account and feed requests are retried on 500, 502, 503, and 504                                 |

</details>

## Server Selection

### Override Server URL Per-Client

The default server is `https://api.synthient.com/api/v4`, available as `synthient.DEFAULT_BASE_URL`. To send requests elsewhere, such as through a gateway or to a mock server in tests, pass `base_url` when creating the client. The URL includes the version path:

```python
client = synthient.Client(base_url="http://localhost:8080/api/v4")
```

## Custom HTTP Client

The client sends requests with [httpx](https://www.python-httpx.org). Pass your own `httpx.Client` as `http_client`, for example to route requests through a proxy:

```python
import httpx

import synthient

with (
    httpx.Client(proxy="http://proxy.example.com:8080") as http_client,
    synthient.Client(http_client=http_client) as client,
):
    info = client.lookup_ip("8.8.8.8")
```

The SDK never closes or modifies your client, so close it yourself. It turns off redirects for API requests and applies its own `timeout` to every request. Requests to the storage host that serves downloads leave out your client's default headers, cookies, query parameters, and authentication, so they can't leak credentials. `AsyncClient` takes an `httpx.AsyncClient`.

To identify your application, `user_agent="my-app/1.2"` appends a product token to the `User-Agent` header.

## Debugging

Pass `response_hook` to call a function after every API response arrives, including each retry, page, and stream handshake. It receives a `ResponseMetadata` with the status code, the request ID to give Synthient support, and a copy of the response headers, which carry the operation's rate limit in `RateLimit-Limit`, `RateLimit-Remaining`, and `RateLimit-Reset`:

```python
import logging

import synthient

logger = logging.getLogger(__name__)


def log_response(meta: synthient.ResponseMetadata) -> None:
    logger.info("status=%d request_id=%s", meta.status_code, meta.request_id)
    remaining = meta.headers.get("ratelimit-remaining")
    if remaining is not None:
        logger.info("rate limit: %s of %s left", remaining, meta.headers.get("ratelimit-limit"))


client = synthient.Client(response_hook=log_response)
```

The hook runs synchronously, also on `AsyncClient`, and an exception it raises propagates to the caller. Storage download responses are not passed to it.

> [!WARNING]
> Response headers can contain secrets, such as presigned download URLs. Avoid logging `meta.headers` in production.

## gRPC Schema

The `synthient.grpcschema` module fetches the protobuf schema of the [Synthient gRPC API](https://docs.synthient.com/grpc) through gRPC server reflection. It needs the `grpc` extra:

```python
from synthient import grpcschema

try:
    schema = grpcschema.fetch()
except grpcschema.SchemaError as error:
    explanation = grpcschema.explain(error)
    raise SystemExit(f"{explanation} {error}" if explanation else str(error)) from error

for file in schema.descriptor_set.file:
    print(file.name, file.package)
```

`fetch()` reads the key from `SYNTHIENT_API_KEY` unless you pass one, and sends none when neither is set rather than raising. It allows 30 seconds by default; pass `timeout` to change it. `schema.descriptor_set` is a protobuf `FileDescriptorSet` with each file after the files it imports. `grpcschema.explain()` describes common gRPC errors in plain language, and returns an empty string for the rest.

# Development

## Maturity

This SDK follows [Semantic Versioning](https://semver.org). Breaking changes to its public API ship only in a new major version, so a requirement such as `synthient>=1,<2` gets fixes and new features without breaking changes. The public API is what the `synthient` package and `synthient.grpcschema` export; modules whose names start with an underscore are internal.

## Contributions

Bug reports and feature requests are welcome as [GitHub issues](https://github.com/synthient/synthient-python/issues). For questions about the API or your account, contact [Synthient support](https://docs.synthient.com/misc/support).

## License

[Apache 2.0](https://github.com/synthient/synthient-python/blob/main/LICENSE)
