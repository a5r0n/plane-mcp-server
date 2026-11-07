"""`api-key` mode serves the header endpoints and a health check, and nothing OAuth."""

from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

from plane_mcp.__main__ import api_key_app
from plane_mcp.auth import PlaneHeaderAuthProvider
from plane_mcp.auth import plane_header_auth_provider as header_auth

INITIALIZE = {"jsonrpc": "2.0", "method": "initialize", "id": 1}


@pytest.fixture(scope="module")
def client():
    with TestClient(api_key_app(), raise_server_exceptions=False) as c:
        yield c


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.text == "ok"


@pytest.mark.parametrize("path", ["/http/api-key/mcp", "/http/api-key/read-only/mcp"])
def test_both_endpoints_demand_a_key(client, path):
    assert client.post(path, json=INITIALIZE).status_code == 401


@pytest.mark.parametrize("path", ["/http/mcp", "/mcp", "/sse", "/register", "/authorize", "/token"])
def test_no_oauth_or_sse_surface(client, path):
    assert client.post(path, json=INITIALIZE).status_code == 404


def test_a_path_prefix_moves_every_route():
    with TestClient(api_key_app("/plane"), raise_server_exceptions=False) as c:
        assert c.get("/plane/healthz").status_code == 200
        assert c.post("/plane/http/api-key/read-only/mcp", json=INITIALIZE).status_code == 401
        assert c.get("/healthz").status_code == 404


# --- key validation cache ----------------------------------------------------


@pytest.fixture
def checks(monkeypatch):
    """Count round trips to Plane; `answers` decides each one."""
    seen = {"n": 0, "answers": [True]}

    async def fake_check(self, base_url, token):
        seen["n"] += 1
        return seen["answers"].pop(0) if len(seen["answers"]) > 1 else seen["answers"][0]

    monkeypatch.setattr(PlaneHeaderAuthProvider, "_check_api_key", fake_check)
    return seen


def _validate(provider, token):
    return asyncio.new_event_loop().run_until_complete(provider._validate_api_key(token))


def test_a_valid_key_is_checked_once_within_the_ttl(checks):
    provider = PlaneHeaderAuthProvider()
    assert _validate(provider, "k") and _validate(provider, "k")
    assert checks["n"] == 1


def test_a_failure_is_never_cached(checks):
    checks["answers"] = [False, True]
    provider = PlaneHeaderAuthProvider()
    assert not _validate(provider, "k")
    assert _validate(provider, "k")
    assert checks["n"] == 2


def test_keys_are_cached_apart(checks):
    provider = PlaneHeaderAuthProvider()
    _validate(provider, "a")
    _validate(provider, "b")
    assert checks["n"] == 2


def test_a_success_expires(checks, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(header_auth.time, "monotonic", lambda: now[0])
    provider = PlaneHeaderAuthProvider()
    _validate(provider, "k")
    now[0] += header_auth.VALID_KEY_TTL_SECONDS + 1
    _validate(provider, "k")
    assert checks["n"] == 2


def test_the_raw_key_is_not_stored(checks):
    provider = PlaneHeaderAuthProvider()
    _validate(provider, "secret-key")
    assert all("secret-key" not in k for k in provider._valid_until)
