from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from synthient import APIError
from tests.conftest import MakeClient, json_response

ACCOUNT = {
    "email": "ada@example.com",
    "first_name": "Ada",
    "last_name": "Lovelace",
    "organization": {
        "id": "0b7d6c1e-8f59-4f4b-9d5e-1f0a7d9c3b21",
        "name": "Example",
        "relation": "OWNER",
    },
    "scopes": ["BASIC", "PROXY_FEEDS"],
    "lookup_quota": {"credits": 9000000000, "resets_in": 3600},
    "api_key": {
        "id": "6f1c2b1e-8f59-4f4b-9d5e-1f0a7d9c3b21",
        "name": "ci",
        "created_at": "2026-05-01T12:00:00.123456Z",
    },
}


def test_account(make_client: MakeClient) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return json_response(200, ACCOUNT)

    account = make_client(handler).account()

    assert paths == ["/api/v4/account/me"]
    assert account.organization.relation == "OWNER"
    assert account.scopes == ["BASIC", "PROXY_FEEDS"]
    assert account.lookup_quota.credits == 9000000000
    assert account.api_key is not None
    assert account.api_key.created_at == datetime(2026, 5, 1, 12, 0, 0, 123456, tzinfo=UTC)


def test_account_without_api_key(make_client: MakeClient) -> None:
    body = {key: value for key, value in ACCOUNT.items() if key != "api_key"}
    account = make_client(lambda _: json_response(200, body)).account()
    assert account.api_key is None


@pytest.mark.parametrize(
    ("response", "status"),
    [
        (json_response(200, {"status": "ok"}), None),
        (json_response(503, {"status": "unhealthy", "problems": ["db"]}), 503),
        (httpx.Response(503, text="<html>down</html>"), 503),
        (httpx.Response(401, text="401 invalid api key"), 401),
    ],
)
def test_health(make_client: MakeClient, response: httpx.Response, status: int | None) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response

    client = make_client(handler)
    if status is None:
        assert client.health() is None
    else:
        with pytest.raises(APIError) as caught:
            client.health()
        assert caught.value.status_code == status
    assert len(calls) == 1
