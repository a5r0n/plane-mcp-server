"""X-Client-Session and X-Client-Agent: forwarded from the caller's MCP request to Plane, and nowhere else.

Plane records them as an activity's provenance. They come per request because one HTTP
server serves many agents, so PLANE_EXTRA_HEADERS (one value per process) cannot carry them.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import requests
from fastmcp.server.auth.auth import AccessToken
from starlette.testclient import TestClient

import plane_mcp.client as client_module
from plane_mcp.__main__ import api_key_app
from plane_mcp.auth import PlaneHeaderAuthProvider
from plane_mcp.auth.plane_oauth_provider import PlaneOAuthTokenVerifier
from plane_mcp.client import get_plane_client_context, plane_extra_headers

PLANE = "http://plane.test"
STORAGE = "https://storage.example.com"
CALLER = {"X-Client-Session": "sess-1", "X-Client-Agent": "pilot"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("PLANE_BASE_URL", PLANE)
    monkeypatch.delenv("PLANE_INTERNAL_BASE_URL", raising=False)
    monkeypatch.setenv("PLANE_API_KEY", "the-key")
    monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "acme")
    monkeypatch.delenv("PLANE_EXTRA_HEADERS", raising=False)
    plane_extra_headers.cache_clear()
    yield
    plane_extra_headers.cache_clear()


def _incoming(monkeypatch, headers: dict[str, str]) -> None:
    """Serve the next calls as if inside an MCP HTTP request carrying `headers`."""
    lowered = {k.lower(): v for k, v in headers.items()}
    monkeypatch.setattr(client_module, "get_http_headers", lambda: lowered)


@pytest.fixture
def routed(monkeypatch):
    """Every request that reaches the transport adapter; `routes` maps a URL to its redirect Location."""
    sent: list[requests.PreparedRequest] = []
    routes: dict[str, str] = {}

    def send(self, request, **kwargs):
        sent.append(request)
        response = requests.Response()
        response.status_code = 302 if request.url in routes else 200
        if request.url in routes:
            response.headers["Location"] = routes[request.url]
        response._content = b"{}"
        response.headers["Content-Type"] = "application/json"
        response.raw = SimpleNamespace(release_conn=lambda: None)
        response.request, response.url = request, request.url
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    return sent, routes


def _get_me() -> None:
    try:
        get_plane_client_context().client.users.get_me()
    except Exception:
        pass  # an empty body fails model validation; the request is what is under test


def _caller(request) -> dict[str, str]:
    return {k: request.headers[k] for k in CALLER if k in request.headers}


@pytest.fixture
def httpx_wire(monkeypatch):
    """Every request httpx sends, answered 200 `{}`."""
    sent: list[httpx.Request] = []

    async def handle(self, request):
        await request.aread()
        sent.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    return sent


def _token(**claims) -> AccessToken:
    return AccessToken(
        token="tok", client_id="c", scopes=["read", "write"], expires_at=int(time.time()) + 3600, claims=claims
    )


CONNECTIONS = {
    "oauth": (_token(auth_method="oauth", workspace_slug="acme"), "Authorization", "Bearer tok"),
    "header": (_token(auth_method="api_key_header", workspace_slug="acme"), "X-Api-Key", "tok"),
}


@pytest.mark.parametrize("kind", sorted(CONNECTIONS))
def test_the_callers_headers_go_to_plane_beside_its_own_auth(kind, monkeypatch, routed):
    token, auth_header, auth_value = CONNECTIONS[kind]
    monkeypatch.setattr(client_module, "get_access_token", lambda: token)
    _incoming(monkeypatch, CALLER)
    _get_me()
    (request,) = routed[0]
    assert _caller(request) == CALLER
    assert request.headers[auth_header] == auth_value


def test_a_request_without_them_sends_neither(monkeypatch, routed):
    _incoming(monkeypatch, {"X-Other": "x"})
    _get_me()
    (request,) = routed[0]
    assert _caller(request) == {}
    assert "X-Other" not in request.headers


def test_outside_an_http_request_plane_extra_headers_still_set_them(monkeypatch, routed):
    """stdio has no MCP request headers; PLANE_EXTRA_HEADERS is how it sets them."""
    monkeypatch.setenv("PLANE_EXTRA_HEADERS", "X-Client-Agent: from-env")
    _get_me()
    (request,) = routed[0]
    assert _caller(request) == {"X-Client-Agent": "from-env"}


def test_the_caller_wins_over_plane_extra_headers(monkeypatch, routed):
    monkeypatch.setenv("PLANE_EXTRA_HEADERS", "X-Client-Agent: from-env")
    _incoming(monkeypatch, CALLER)
    _get_me()
    (request,) = routed[0]
    assert _caller(request) == CALLER


def test_the_caller_wins_over_a_differently_cased_plane_extra_header(monkeypatch, routed):
    monkeypatch.setenv("PLANE_EXTRA_HEADERS", "x-client-agent: from-env")
    _incoming(monkeypatch, CALLER)
    _get_me()
    (request,) = routed[0]
    assert _caller(request) == CALLER


def test_an_httpx_call_sends_only_the_callers_value_over_a_differently_cased_env_one(monkeypatch, httpx_wire):
    monkeypatch.setenv("PLANE_EXTRA_HEADERS", "x-client-agent: from-env")
    _incoming(monkeypatch, CALLER)
    assert asyncio.run(PlaneHeaderAuthProvider()._check_api_key(PLANE, "callers-key"))
    (request,) = httpx_wire
    assert request.headers.get_list("X-Client-Agent") == ["pilot"]


def test_the_auth_providers_plane_calls_carry_them_too(monkeypatch, httpx_wire):
    _incoming(monkeypatch, CALLER)
    assert asyncio.run(PlaneHeaderAuthProvider()._check_api_key(PLANE, "callers-key"))
    asyncio.run(PlaneOAuthTokenVerifier(plane_base_url=PLANE).verify_token("oauth-tok"))

    assert len(httpx_wire) == 3
    for request in httpx_wire:
        assert _caller(request) == CALLER
    assert httpx_wire[0].headers["x-api-key"] == "callers-key"
    assert all(r.headers["authorization"] == "Bearer oauth-tok" for r in httpx_wire[1:])


def test_one_header_alone_is_forwarded(monkeypatch, routed):
    _incoming(monkeypatch, {"X-Client-Agent": "pilot"})
    _get_me()
    (request,) = routed[0]
    assert _caller(request) == {"X-Client-Agent": "pilot"}


@pytest.mark.parametrize(
    "value",
    ["a" * 256, "line\nbreak", "carriage\rreturn", "trailing\n", "tab\tbed", "caf\u00e9", "", "   "],
    ids=["overlong", "lf", "cr", "trailing-lf", "control", "non-ascii", "empty", "blank"],
)
def test_a_bad_value_is_dropped_and_the_good_one_kept(value, monkeypatch, routed):
    _incoming(monkeypatch, {"X-Client-Session": value, "X-Client-Agent": "pilot"})
    _get_me()
    (request,) = routed[0]
    assert _caller(request) == {"X-Client-Agent": "pilot"}


def test_a_value_of_exactly_255_characters_is_kept(monkeypatch, routed):
    _incoming(monkeypatch, {"X-Client-Session": "a" * 255})
    _get_me()
    (request,) = routed[0]
    assert _caller(request) == {"X-Client-Session": "a" * 255}


def test_a_redirect_off_the_plane_origin_drops_them(monkeypatch, routed):
    sent, routes = routed
    routes[f"{PLANE}/api/v1/users/me/"] = f"{PLANE}/api/v1/users/me/again/"
    routes[f"{PLANE}/api/v1/users/me/again/"] = f"{STORAGE}/elsewhere"
    _incoming(monkeypatch, CALLER)
    _get_me()
    assert [r.url for r in sent] == [
        f"{PLANE}/api/v1/users/me/",
        f"{PLANE}/api/v1/users/me/again/",
        f"{STORAGE}/elsewhere",
    ]
    assert [_caller(r) for r in sent] == [CALLER, CALLER, {}]


def test_a_session_never_sends_them_off_the_plane_origin(monkeypatch):
    _incoming(monkeypatch, CALLER)
    with client_module.PlaneSession() as session:
        assert _caller(session.prepare_request(requests.Request("GET", f"{PLANE}/x"))) == CALLER
        assert _caller(session.prepare_request(requests.Request("GET", f"{STORAGE}/x"))) == {}
        prepared = session.prepare_request(requests.Request("GET", f"{STORAGE}/x", headers=CALLER))
        assert _caller(prepared) == {}


# --- through the real HTTP server ---------------------------------------------


def _call(client: TestClient, workitem_id: str, headers: dict[str, str | bytes]) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "workitem",
            "arguments": {"action": "retrieve", "project_id": "p", "workitem_id": workitem_id},
        },
    }
    response = client.post(
        "/http/api-key/mcp",
        json=body,
        headers={
            "authorization": "Bearer k",
            "x-workspace-slug": "acme",
            "accept": "application/json, text/event-stream",
            "mcp-protocol-version": "2025-06-18",
            **headers,
        },
    )
    assert response.status_code == 200, response.text
    data = [line[5:] for line in response.text.splitlines() if line.startswith("data:")]
    return json.loads(data[-1]) if data else response.json()


def test_two_concurrent_requests_each_send_only_their_own_values(monkeypatch, routed):
    """Both calls are held inside Plane at once, so neither can finish before the other starts."""
    sent, _ = routed
    both_in_flight = threading.Barrier(2, timeout=10)
    original_send = requests.adapters.HTTPAdapter.send

    def held(self, request, **kwargs):
        both_in_flight.wait()
        return original_send(self, request, **kwargs)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", held)

    async def accept(self, base_url, token):
        return True

    monkeypatch.setattr(PlaneHeaderAuthProvider, "_check_api_key", accept)

    callers = {
        "w-one": {"X-Client-Session": "s-one", "X-Client-Agent": "agent-one"},
        "w-two": {"X-Client-Session": "s-two", "X-Client-Agent": "agent-two"},
    }
    errors: list[BaseException] = []
    with TestClient(api_key_app(), raise_server_exceptions=False) as client:

        def run(workitem_id: str) -> None:
            try:
                _call(client, workitem_id, callers[workitem_id])
            except BaseException as exc:  # surfaced below; a thread's exception is otherwise lost
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(w,)) for w in callers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

    assert not errors
    assert len(sent) == 2
    for request in sent:
        (workitem_id,) = [w for w in callers if f"/{w}/" in request.url]
        assert _caller(request) == callers[workitem_id]


def test_a_non_ascii_value_neither_breaks_auth_nor_reaches_plane(monkeypatch, routed, httpx_wire):
    """The key check runs unstubbed: httpx would raise on a non-ASCII header value."""
    sent, _ = routed
    with TestClient(api_key_app(), raise_server_exceptions=False) as client:
        _call(client, "w-one", {"X-Client-Agent": "caf\u00e9".encode("latin-1"), "X-Client-Session": "s-one"})

    (check,) = httpx_wire
    assert _caller(check) == {"X-Client-Session": "s-one"}
    (request,) = sent
    assert _caller(request) == {"X-Client-Session": "s-one"}
