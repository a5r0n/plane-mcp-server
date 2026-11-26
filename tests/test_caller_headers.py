"""X-Client-Session and X-Client-Agent: forwarded from the caller's MCP request to Plane, and nowhere else.

Plane records them as an activity's provenance. They come per request because one HTTP
server serves many agents, so PLANE_EXTRA_HEADERS (one value per process) cannot carry them.
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
import requests
from starlette.testclient import TestClient

import plane_mcp.client as client_module
from plane_mcp.__main__ import api_key_app
from plane_mcp.auth import PlaneHeaderAuthProvider
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


def test_the_callers_headers_go_to_plane_beside_its_own_auth(monkeypatch, routed):
    _incoming(monkeypatch, CALLER)
    _get_me()
    (request,) = routed[0]
    assert _caller(request) == CALLER
    assert request.headers["X-Api-Key"] == "the-key"


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


def test_one_header_alone_is_forwarded(monkeypatch, routed):
    _incoming(monkeypatch, {"X-Client-Agent": "pilot"})
    _get_me()
    (request,) = routed[0]
    assert _caller(request) == {"X-Client-Agent": "pilot"}


@pytest.mark.parametrize(
    "value",
    ["a" * 256, "line\nbreak", "carriage\rreturn", "", "   "],
    ids=["overlong", "lf", "cr", "empty", "blank"],
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


def _call(client: TestClient, workitem_id: str, headers: dict[str, str]) -> dict:
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
