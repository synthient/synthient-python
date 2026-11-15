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

- [Installation](#installation)
- [Quick start](#quick-start)
- [Authentication](#authentication)
- [IP and domain lookups](#ip-and-domain-lookups)
- [Account](#account)
- [Feeds](#feeds)
- [Asyncio and Trio](#asyncio-and-trio)
- [Errors](#errors)
- [Retries and timeouts](#retries-and-timeouts)
- [Configuration](#configuration)
- [Response metadata](#response-metadata)
- [gRPC schema](#grpc-schema)
- [Requirements](#requirements)
- [Versioning](#versioning)
- [Documentation](#documentation)
- [Contributions](#contributions)
- [License](#license)

## Installation

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

## Quick start

Set the `SYNTHIENT_API_KEY` environment variable, then look up an address. Each lookup costs one credit; failed lookups are free.

```python
import synthient

with synthient.Client() as client:
    info = client.lookup_ip("8.8.8.8")
    if info.intelligence is not None:
        print(info.intelligence.risk_score, info.intelligence.categories)
```

A client pools connections and is safe to share across threads, so reuse one and close it with `close()` or a `with` block. The examples below reuse an open `client`, and runnable programs are in [examples](https://github.com/synthient/synthient-python/tree/main/examples). The full reference is in the [Python SDK docs](https://docs.synthient.com/sdks/python).

## Authentication

Every request needs a Synthient API key. Keys are issued from the Synthient dashboard; if you don't have one, [contact us](mailto:contact@synthient.com). A key's scopes decide what it can call: `BASIC` covers the lookups and `account()`, and each feed has its own scopes, listed under [Feeds](#feeds).

This SDK supports the following security scheme:

| Name      | Type    | Scheme             | Environment Variable |
| --------- | ------- | ------------------ | -------------------- |
| `api_key` | API key | `x-api-key` header | `SYNTHIENT_API_KEY`  |

A client created without `api_key` reads the key from the `SYNTHIENT_API_KEY` environment variable:

```python
import synthient

client = synthient.Client()
```

To read the key from somewhere else, such as a secret manager, pass it as `api_key`:

```python
client = synthient.Client(api_key=api_key)
```

Creating a client makes no network calls. To check a key, call `account()`, which costs no credits.

If a request fails even though you are sending a key:

- **401 Unauthorized**: the key is malformed, unknown, or revoked.
- **402 Payment Required**: the team is out of lookup credits.
- **403 Forbidden**: the key lacks the scope for that endpoint or feed.

`account()` shows the key's scopes and the team's remaining credits.

See [Authentication](https://docs.synthient.com/authentication) for how keys and scopes work.

## IP and domain lookups

`lookup_ip()`, `lookup_ips()`, and `lookup_domain()` are described in full under [Lookups](https://docs.synthient.com/sdks/python/lookups), and the data they return under [IP API](https://docs.synthient.com/ipapi).

### Look up an IP address

Get the network, location, and risk intelligence for an IPv4 or IPv6 address, given as a string or an `ipaddress` object.

```python
info = client.lookup_ip("8.8.8.8")
if info.network is not None:
    print(info.network.asn, info.network.isp, info.network.type)
if info.location is not None:
    print(info.location.country, info.location.city)
```

`network`, `location`, and `intelligence` are `None` when the API has no data for them. An invalid address raises `ValueError` before any request is sent, and a private or reserved address raises an `APIError` with status 422.

### Look up many IP addresses

Look up as many as 1000 addresses in one request for `ceil(n * 0.9)` credits. Private and reserved addresses are skipped and duplicates are merged, so match results by `ip`.

```python
results = client.lookup_ips(["8.8.8.8", "1.1.1.1"])
for result in results:
    if result.intelligence is not None:
        print(result.ip, result.intelligence.risk_score)
```

`ip` is an `ipaddress.IPv4Address` or `IPv6Address`, so compare it with `ipaddress.ip_address()`, not a string. An empty batch returns `[]` without a request, and more than 1000 addresses raise `ValueError`.

### Look up a domain

Get the honeypot traffic observed for a domain and its most recent events. Each lookup costs one credit.

```python
domain = client.lookup_domain("example.com", limit=10)
print(domain.status, domain.stats.events_24h)
for event in domain.recent_events:
    print(event.timestamp, event.target_subdomain, event.port)
```

`limit` caps the number of recent events, from 1 to 1000; without it the API returns 50. A domain that has never been observed returns `status` `"inactive"` with zeroed stats, not an error.

## Account

Get the key's account, scopes, and remaining lookup credits, at no cost.

```python
account = client.account()
print(account.organization.name, account.scopes, account.lookup_quota.credits)
```

`lookup_quota.resets_in` is the number of seconds until the credits reset.

`health()` checks that the API is available. It returns `None`, and raises an `APIError` if the API reports a problem:

```python
client.health()
```

## Feeds

Each feed has a real-time stream and hourly Parquet exports that roll up into daily exports. See [Feeds](https://docs.synthient.com/sdks/python/feeds) for the SDK reference, and [Firehose](https://docs.synthient.com/enterprise/firehose) and [Feeds](https://docs.synthient.com/enterprise/feeds) for the data behind them.

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

### Streaming

`stream()` is a context manager that yields an iterator over a feed's real-time events. Entering the `with` block opens a connection, and leaving it closes it:

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
        # Avoid reconnecting in a tight loop.
        time.sleep(2)
```

- The server closes each stream after up to 30 minutes. That ends the `for` loop without an error, and the `while` loop reconnects at once.
- A 401 or 403 stops the loop, because reconnecting can't fix a rejected key or a missing scope.
- Any other failure raises a `SynthientError`, and the loop waits 2 seconds before reconnecting.
- Events sent while disconnected are missed. The API has no resume cursor.

### Exports

`list_exports()` returns one page of a feed's exports, newest first. Pass `page.next_cursor` as `cursor` to get the next page; the last page has none.

```python
page = client.proxies.list_exports(limit=50)
for export in page.exports:
    print(export.id, export.kind, export.row_count)
```

`iter_exports()` fetches the pages for you as the loop needs them:

```python
for export in client.proxies.iter_exports():
    print(export.id, export.kind, export.row_count)
```

On the first page, the newest hourly export has the ID `"latest"`, a moving alias for the freshest export. Its `date` and `hour` give its stable ID, such as `"2026-10-14/21"`.

`limit` sets the page size, from 1 to 500; without it the API returns 100 exports per page.

### Downloads

Exports are Parquet files. `download()` is a context manager that yields a streaming `httpx.Response`:

```python
with (
    client.proxies.download("latest") as response,
    open("proxies.parquet", "wb") as file,
):
    for chunk in response.iter_bytes():
        file.write(chunk)
```

- Name an export by its `id` from a listing, by `"latest"`, by a `date` or `"YYYY-MM-DD"` for a UTC day, or by a timezone-aware `datetime` or `"YYYY-MM-DD/H"` for a UTC hour.
- `export_meta()` returns the file's SHA-256 checksum. Use a fixed ID for both calls, because `"latest"` can move between them.
- `download_url()` returns a presigned URL that works without a key for 24 hours, so treat it as a secret. Your API key is never sent to the storage host.
- Hourly exports roll into the daily export shortly after 00:30 UTC the next day. After that, the hour returns a 400.

## Asyncio and Trio

`AsyncClient` takes the same arguments as `Client` and has the same methods as coroutines, on asyncio or Trio. Use one client per event loop:

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

With Trio, use `trio.run(main)` instead. It differs from `Client` in a few ways:

- Close it with `aclose()` or an `async with` block.
- Enter `stream()` and `download()` with `async with`.
- Read events from `stream()` and exports from `iter_exports()` with `async for`, and a download's body with `aiter_bytes()`.
- `http_client` takes an `httpx.AsyncClient`.

```python
async with client.proxies.stream() as events:
    async for event in events:
        print(event.ip, event.provider, event.type)
```

## Errors

A request that fails with a non-success status raises an `APIError`:

| Attribute     | Type                   | Description                                     |
| ------------- | ---------------------- | ----------------------------------------------- |
| `status_code` | `int`                  | HTTP status code, such as `404`                 |
| `type`        | `str`                  | URI identifying the problem type                |
| `title`       | `str`                  | Short summary of the problem type               |
| `detail`      | `str`                  | Explanation of this occurrence                  |
| `errors`      | `dict[str, list[str]]` | Validation messages by request field            |
| `request_id`  | `str`                  | Request ID to give Synthient support            |
| `retry_after` | `float` or `None`      | Seconds the server asked you to wait, or `None` |

`str(error)` returns only the HTTP status, so it is safe to log. The attributes hold untrusted server data.

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

### Error types

- `SynthientError`: the base class of the SDK's errors, also raised for a response it can't use.
- `APIError`: the API, or the storage host during `download()`, returned a non-success status.
- `APIConnectionError`: the connection failed or broke, with the httpx exception as `__cause__`.
- `APITimeoutError`: a subclass of `APIConnectionError` for a request that timed out.
- `grpcschema.SchemaError`: fetching the [gRPC schema](#grpc-schema) failed.
- httpx exceptions: a failure while you read a `download()` body, such as `httpx.ReadTimeout`.
- `ValueError` or `TypeError`: an invalid argument, such as a missing API key, rejected before any request is sent.
- `RuntimeError`: the client was used after it was closed.

<details><summary>Status codes</summary>

<br />

| Status | Meaning                                                                                          |
| ------ | ------------------------------------------------------------------------------------------------ |
| 400    | The request is invalid, such as an hourly export that was rolled up                              |
| 401    | The API key is malformed, unknown, or revoked                                                    |
| 402    | The team is out of lookup credits                                                                |
| 403    | The key lacks the scope for the endpoint or feed                                                 |
| 404    | The export doesn't exist, or a daily export is still rolling up (`retry_after` is set)           |
| 422    | The input is invalid, such as a private address or a malformed domain                            |
| 429    | Rate limited if `retry_after` is set; otherwise the budget is spent or too many streams are open |
| 5xx    | The server failed                                                                                |

</details>

See [Errors and retries](https://docs.synthient.com/sdks/python/errors) for the SDK reference and [Errors](https://docs.synthient.com/errors) for what each status means.

## Retries and timeouts

### Retries

The client retries a failed request up to twice, backing off exponentially with jitter and honoring `Retry-After`:

| Requests                      | Retried on                                                                                   |
| ----------------------------- | -------------------------------------------------------------------------------------------- |
| Account and feed requests     | 500, 502, 503, and 504, most transport errors, and 429 with `Retry-After`                    |
| Billed lookups and `health()` | Only 429 with `Retry-After`, which comes before any work, so a lookup is never charged twice |

Waits start at about one second and double up to 30 seconds, and are never shorter than `Retry-After`. A `Retry-After` longer than a minute raises the error instead of retrying.

A 429 without `Retry-After` means the monthly request budget is spent or too many streams are open, so it is raised at once. Downloads from the storage host are never retried.

Set the number of retries with `max_retries`. Zero disables them:

```python
client = synthient.Client(max_retries=4)
```

See [Rate Limits](https://docs.synthient.com/rate-limits) for the limits behind a 429.

### Timeouts

The client allows 10 seconds to connect and 60 seconds for each read or write. Pass `timeout` as seconds or an `httpx.Timeout` to change it, or `None` to disable it:

```python
import httpx

client = synthient.Client(timeout=httpx.Timeout(30.0, connect=5.0))
```

`timeout` bounds each network operation, not a whole call. Open streams stay within the read timeout, because the API sends a keepalive line every 15 seconds. A timeout raises `APITimeoutError`, except while you read a `download()` body, where httpx raises `httpx.ReadTimeout`.

## Configuration

`Client` and `AsyncClient` take the same arguments. Every argument after `api_key` is keyword-only. See [Client](https://docs.synthient.com/sdks/python/client) for the full reference.

| Argument        | Default                              | Description                                                             |
| --------------- | ------------------------------------ | ----------------------------------------------------------------------- |
| `api_key`       | `SYNTHIENT_API_KEY`                  | The API key                                                             |
| `base_url`      | `https://api.synthient.com/api/v4`   | The API base URL, including the version path                            |
| `max_retries`   | `2`                                  | How many times a failed request is retried; `0` disables retries        |
| `timeout`       | `httpx.Timeout(60.0, connect=10.0)`  | Seconds, an `httpx.Timeout`, or `None` for no timeout                   |
| `http_client`   | `None`                               | Your own `httpx.Client`, or `httpx.AsyncClient` on `AsyncClient`        |
| `user_agent`    | `None`                               | A product token appended to the `User-Agent` header                     |
| `response_hook` | `None`                               | Called with the [metadata](#response-metadata) of every API response    |

An invalid argument raises `ValueError` when the client is created, and an API key that is not a string raises `TypeError`.

### Server URL

The default server is `https://api.synthient.com/api/v4`, available as `synthient.DEFAULT_BASE_URL`. Use `base_url` to send requests elsewhere, such as a mock server in tests:

```python
client = synthient.Client(base_url="http://localhost:8080/api/v4")
```

### Custom HTTP client

The client sends requests with [httpx](https://www.python-httpx.org). Pass your own `httpx.Client` as `http_client`, for example to use a proxy:

```python
import httpx

import synthient

with (
    httpx.Client(proxy="http://proxy.example.com:8080") as http_client,
    synthient.Client(http_client=http_client) as client,
):
    info = client.lookup_ip("8.8.8.8")
```

- The SDK never closes your client, so close it yourself.
- The SDK's `timeout` replaces your client's, and it turns off redirects for API requests.
- Downloads from the storage host leave out your client's default headers, cookies, query parameters, and authentication.
- `AsyncClient` takes an `httpx.AsyncClient`.

`user_agent="my-app/1.2"` adds your product token to the `User-Agent` header. The header starts with `synthient-python/` and the SDK version, which is also available as `synthient.__version__`.

## Response metadata

`response_hook` calls a function after every API response, including retries, pages, and stream handshakes, with a `ResponseMetadata` holding the status, request ID, and headers:

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

The hook runs synchronously, also on `AsyncClient`, and an exception it raises reaches the caller. It also sees error responses, but not download responses from the storage host. `ResponseMetadata` has no rate limit fields: read the `RateLimit-Limit`, `RateLimit-Remaining`, and `RateLimit-Reset` headers from `headers` when they are present.

> [!WARNING]
> Response headers can contain secrets, such as presigned download URLs. Avoid logging `meta.headers` in production.

## gRPC schema

The `synthient.grpcschema` module fetches the protobuf schema of the [Synthient gRPC API](https://docs.synthient.com/grpc) through server reflection. It needs the `grpc` extra:

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

`fetch()` reads the key from `SYNTHIENT_API_KEY` unless you pass one. `schema.descriptor_set` is a protobuf `FileDescriptorSet`. `grpcschema.explain()` describes common gRPC errors in plain language.

`fetch()` also takes keyword arguments:

| Argument    | Default                  | Description                                                                 |
| ----------- | ------------------------ | --------------------------------------------------------------------------- |
| `endpoint`  | `grpc.synthient.com:443` | The server as `host:port`, a bare host, or a `grpc://` or `https://` URL    |
| `plaintext` | `False`                  | Disables TLS, for local development servers                                 |
| `symbols`   | `()`                     | Service or message names to resolve; empty resolves every listed service    |
| `timeout`   | `30.0`                   | Seconds allowed for the whole fetch, or `None` for no limit                 |

See [gRPC schema](https://docs.synthient.com/sdks/python/grpc-schema) for the full reference.

## Requirements

Python 3.11 or later. The SDK depends on `httpx`, `pydantic` 2, and `anyio`.

## Versioning

This SDK follows [Semantic Versioning](https://semver.org). Breaking changes ship only in a new major version, so a requirement such as `synthient>=1,<2` avoids them. Modules whose names start with an underscore are internal.

## Documentation

- [Python SDK](https://docs.synthient.com/sdks/python): getting started
- [Client](https://docs.synthient.com/sdks/python/client): the constructor, its arguments, and response metadata
- [Lookups](https://docs.synthient.com/sdks/python/lookups): IP and domain lookups, account, and health
- [Feeds](https://docs.synthient.com/sdks/python/feeds): streams and Parquet exports
- [Errors and retries](https://docs.synthient.com/sdks/python/errors): error classes, retries, and timeouts
- [Types](https://docs.synthient.com/sdks/python/types): the fields of every response and event type
- [gRPC schema](https://docs.synthient.com/sdks/python/grpc-schema): the `synthient.grpcschema` module

Responses and events are frozen [pydantic](https://docs.pydantic.dev) models, exported from the `synthient` package for type annotations.

## Contributions

Bug reports and feature requests are welcome as [GitHub issues](https://github.com/synthient/synthient-python/issues). For questions about the API or your account, contact [Synthient support](https://docs.synthient.com/misc/support).

## License

[Apache 2.0](https://github.com/synthient/synthient-python/blob/main/LICENSE)
