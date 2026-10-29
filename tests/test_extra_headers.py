"""PLANE_EXTRA_HEADERS: sent to the Plane origins, and to nothing else.

Its use is an access gateway in front of Plane that admits the server by a pair of
service-token headers. The rule is the destination's origin: PLANE_BASE_URL's or
PLANE_INTERNAL_BASE_URL's gets them, redirect hops included; any other origin does
not. Missing one gateway-bound call means it is refused at the gateway; sending them
elsewhere (S3 storage, a redirect target) hands the gateway secret to a third party.
upload_from_url never gets them, even to a Plane origin. Every case is tested against
the wire -- the request the HTTP library would actually send.
"""

from __future__ import annotations

import asyncio
import socket
import time
from types import SimpleNamespace

import httpx
import pytest
import requests
import urllib3
from fastmcp.server.auth.auth import AccessToken
from key_value.aio.stores.memory import MemoryStore

import plane_mcp.client as client_module
from plane_mcp.auth.plane_header_auth_provider import PlaneHeaderAuthProvider
from plane_mcp.auth.plane_oauth_provider import PlaneOAuthProvider, PlaneOAuthTokenVerifier
from plane_mcp.client import get_plane_client_context, is_plane_origin, plane_extra_headers
from plane_mcp.tools import workitem_attachment

PLANE = "http://plane.test"
STORAGE = "https://storage.example.com"
SECRET = "s3cr3t-value"
EXTRA = {"CF-Access-Client-Id": "id-123", "CF-Access-Client-Secret": SECRET}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """A clean parse per test: the result is cached for the process."""
    monkeypatch.setenv("PLANE_BASE_URL", PLANE)
    monkeypatch.delenv("PLANE_INTERNAL_BASE_URL", raising=False)
    monkeypatch.setenv("PLANE_API_KEY", "the-key")
    monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "acme")
    monkeypatch.delenv("PLANE_EXTRA_HEADERS", raising=False)
    plane_extra_headers.cache_clear()
    yield
    plane_extra_headers.cache_clear()


@pytest.fixture
def extra(monkeypatch):
    monkeypatch.setenv("PLANE_EXTRA_HEADERS", f"CF-Access-Client-Id: id-123\nCF-Access-Client-Secret: {SECRET}")


@pytest.fixture
def wire(monkeypatch):
    """Every request `requests` sends, answered with an empty JSON object."""
    sent: list[requests.PreparedRequest] = []

    def send(self, request, **kwargs):
        sent.append(request)
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        response.headers["Content-Type"] = "application/json"
        response.request = request
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    return sent


@pytest.fixture
def routed(monkeypatch):
    """Every request that reaches the transport adapter, so requests' own redirect handling runs.

    `routes` maps a URL to the Location it redirects to; anything else answers 200 `{}`.
    """
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


def _carries(request) -> bool:
    """True if every extra header went out; False if none did; fails on a partial set."""
    present = {k: request.headers.get(k) for k in EXTRA if k in request.headers}
    assert present in ({}, EXTRA)
    return bool(present)


@pytest.fixture
def httpx_wire(monkeypatch):
    """Every request httpx sends, answered with a fixed JSON body."""
    sent: list[httpx.Request] = []
    body: dict = {}

    async def handle(self, request):
        await request.aread()
        sent.append(request)
        return httpx.Response(200, json=body.get("json", {}))

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    return sent, body


# --- parsing -----------------------------------------------------------------


def test_unset_means_no_headers():
    assert plane_extra_headers() == {}


@pytest.mark.parametrize(
    "raw",
    [
        f'{{"CF-Access-Client-Id": "id-123", "CF-Access-Client-Secret": "{SECRET}"}}',
        f"CF-Access-Client-Id: id-123\nCF-Access-Client-Secret: {SECRET}",
        f"\n  CF-Access-Client-Id:id-123  \n\nCF-Access-Client-Secret:   {SECRET}\n",
    ],
    ids=["json", "lines", "lines-with-blank-lines-and-padding"],
)
def test_both_forms_parse_to_the_same_headers(raw, monkeypatch):
    monkeypatch.setenv("PLANE_EXTRA_HEADERS", raw)
    assert plane_extra_headers() == EXTRA


def test_a_value_may_itself_contain_a_colon(monkeypatch):
    monkeypatch.setenv("PLANE_EXTRA_HEADERS", "X-Thing: a:b:c")
    assert plane_extra_headers() == {"X-Thing": "a:b:c"}


@pytest.mark.parametrize(
    ("raw", "names"),
    [
        (f'{{"CF-Access-Client-Secret": "{SECRET}"', "not valid JSON"),
        (f'["CF-Access-Client-Secret", "{SECRET}"]', "JSON object"),
        ('{"X-Count": 5}', "'X-Count'"),
        (f'{{"X-Bad": "{SECRET}\\r\\nInjected: 1"}}', "'X-Bad'"),
        (f"CF-Access-Client-Id: id-123\n{SECRET}", "line 2"),
        (f"Bad Name: {SECRET}", "'Bad Name'"),
        (f": {SECRET}", "invalid header name"),
        (f"Authorization: Bearer {SECRET}", "'Authorization'"),
        (f'{{"x-api-key": "{SECRET}"}}', "'x-api-key'"),
    ],
    ids=["bad-json", "json-array", "non-string", "crlf", "no-colon", "space-in-name", "empty-name", "auth", "api-key"],
)
def test_malformed_input_is_refused_naming_the_variable_but_not_the_secret(raw, names, monkeypatch):
    monkeypatch.setenv("PLANE_EXTRA_HEADERS", raw)
    with pytest.raises(ValueError) as caught:
        plane_extra_headers()
    message = str(caught.value)
    assert "PLANE_EXTRA_HEADERS" in message
    assert names in message
    assert SECRET not in message
    assert caught.value.__cause__ is None


def test_a_malformed_value_stops_the_server_at_startup(monkeypatch):
    """Not on the first tool call, minutes later, as an opaque tool error."""
    from plane_mcp.server import get_stdio_mcp

    monkeypatch.setenv("PLANE_EXTRA_HEADERS", "no colon here")
    with pytest.raises(ValueError, match="PLANE_EXTRA_HEADERS"):
        get_stdio_mcp()


# --- origin matching ---------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{PLANE}/api/v1/", True),
        ("http://PLANE.Test/x", True),
        ("HTTP://plane.test/x", True),
        ("http://plane.test:80/x", True),
        ("https://plane.test/x", False),
        ("http://plane.test:8080/x", False),
        ("http://plane.test.evil.example/x", False),
        ("http://plane.test@evil.example/x", False),
        (f"http://evil.example/?next={PLANE}", False),
        ("http://plane.test:notaport/x", False),
        ("/relative/path", False),
        ("", False),
    ],
)
def test_origin_is_scheme_host_and_effective_port(url, expected):
    assert is_plane_origin(url) is expected


def test_https_default_port_and_host_case_are_normalised_on_the_base_too(monkeypatch):
    monkeypatch.setenv("PLANE_BASE_URL", "https://Plane.Example:443/some/path/")
    assert is_plane_origin("https://plane.example/api/")
    assert not is_plane_origin("http://plane.example/api/")


def test_the_internal_base_url_is_a_plane_origin_too(monkeypatch):
    monkeypatch.setenv("PLANE_INTERNAL_BASE_URL", "http://plane-api.internal:8000")
    assert is_plane_origin("http://plane-api.internal:8000/api/v1/")
    assert not is_plane_origin("http://plane-api.internal/api/v1/")
    assert is_plane_origin(f"{PLANE}/api/v1/")


def test_without_plane_base_url_the_default_is_the_plane_origin(monkeypatch):
    monkeypatch.delenv("PLANE_BASE_URL")
    assert is_plane_origin("https://api.plane.so/api/v1/")
    assert not is_plane_origin(f"{PLANE}/api/v1/")


# --- the plane-sdk client, every transport -----------------------------------


def _token(**claims) -> AccessToken:
    return AccessToken(
        token="tok", client_id="c", scopes=["read", "write"], expires_at=int(time.time()) + 3600, claims=claims
    )


CONNECTIONS = {
    "oauth": (_token(auth_method="oauth", workspace_slug="acme"), "Authorization", "Bearer tok"),
    "header": (_token(auth_method="api_key_header", workspace_slug="acme"), "X-Api-Key", "tok"),
    "stdio": (None, "X-Api-Key", "the-key"),
}


@pytest.mark.parametrize("kind", sorted(CONNECTIONS))
def test_every_session_the_sdk_client_creates_carries_the_headers(kind, extra, monkeypatch):
    """The oracle is independent of the walk that applies them: record each session
    as it is constructed, so a resource the walk cannot reach still fails here."""
    token, _, _ = CONNECTIONS[kind]
    monkeypatch.setattr(client_module, "get_access_token", lambda: token)
    created: list[requests.Session] = []
    original = requests.Session.__init__

    def record(self, *args, **kwargs):
        original(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(requests.Session, "__init__", record)
    get_plane_client_context()

    assert len(created) > 30
    for session in created:
        assert _carries(session.prepare_request(requests.Request("GET", f"{PLANE}/api/v1/x")))
        assert not _carries(session.prepare_request(requests.Request("GET", f"{STORAGE}/x")))


@pytest.mark.parametrize("kind", sorted(CONNECTIONS))
def test_v1_and_v2_calls_send_the_headers_and_keep_their_own_auth(kind, extra, wire, monkeypatch):
    token, auth_header, auth_value = CONNECTIONS[kind]
    monkeypatch.setattr(client_module, "get_access_token", lambda: token)
    client = get_plane_client_context().client

    try:
        client.users.get_me()
    except Exception:
        pass  # an empty body fails model validation; the request is what is under test
    try:
        client.v2.users.me()
    except Exception:
        pass

    assert [r.url.split("/api/")[1][:2] for r in wire] == ["v1", "v2"]
    for request in wire:
        assert request.url.startswith(PLANE)
        assert {k: request.headers.get(k) for k in EXTRA} == EXTRA
        assert request.headers[auth_header] == auth_value


def test_sdk_calls_through_the_internal_base_url_send_the_headers(extra, wire, monkeypatch):
    monkeypatch.setenv("PLANE_INTERNAL_BASE_URL", "http://plane-api.internal:8000")
    try:
        get_plane_client_context().client.users.get_me()
    except Exception:
        pass
    (request,) = wire
    assert request.url.startswith("http://plane-api.internal:8000/")
    assert _carries(request)


def test_without_the_variable_the_sdk_sends_nothing_extra(wire):
    try:
        get_plane_client_context().client.users.get_me()
    except Exception:
        pass
    assert not any(k in wire[0].headers for k in EXTRA)


# --- storage and user URLs: by origin, except upload_from_url ----------------


@pytest.mark.parametrize(
    ("storage", "carried"), [(STORAGE, False), (PLANE, True)], ids=["other-origin", "plane-origin"]
)
def test_the_sdk_upload_sends_the_headers_to_storage_only_on_a_plane_origin(storage, carried, extra, wire, monkeypatch):
    """upload_from_bytes posts the file to a presigned storage URL between two Plane calls."""
    responses = iter(
        [
            {"upload_data": {"url": f"{storage}/bucket", "fields": {}}, "asset_id": "a1", "attachment": {}},
            {},
            {},
        ]
    )
    original_send = requests.Session.send

    def send(self, request, **kwargs):
        response = original_send(self, request, **kwargs)
        response._content = requests.compat.json.dumps(next(responses)).encode()
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    monkeypatch.setattr("plane.api.work_items.attachments.WorkItemAttachment.model_validate", lambda raw: raw)
    client = get_plane_client_context().client

    client.work_items.attachments.upload_from_bytes("acme", "p", "w", b"data", "f.txt", "text/plain")

    plane, upload, done = wire
    assert upload.url == f"{storage}/bucket"
    assert _carries(upload) is carried
    for request in (plane, done):
        assert request.url.startswith(PLANE)
        assert _carries(request)


def _read_from(url, monkeypatch):
    monkeypatch.setattr(
        workitem_attachment, "_find", lambda *a: SimpleNamespace(attributes={"name": "a.txt", "type": "text/plain"})
    )
    monkeypatch.setattr(workitem_attachment, "_download_url", lambda *a: url)
    workitem_attachment._read(get_plane_client_context().client, "acme", "p", "w", "a")


@pytest.mark.parametrize(
    ("storage", "carried"), [(STORAGE, False), (PLANE, True)], ids=["other-origin", "plane-origin"]
)
def test_reading_an_attachment_sends_the_headers_only_to_a_plane_origin(storage, carried, extra, wire, monkeypatch):
    _read_from(f"{storage}/a.txt", monkeypatch)

    (fetch,) = wire
    assert fetch.url == f"{storage}/a.txt"
    assert _carries(fetch) is carried


def test_upload_from_url_never_sends_the_headers_even_to_a_plane_origin(extra, monkeypatch):
    """Otherwise any caller could use the server as a credentialed proxy into the gateway."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", port))],
    )
    sent: list[dict] = []

    def urlopen(self, method, url, *args, headers=None, **kwargs):
        sent.append(dict(headers or {}))
        return SimpleNamespace(
            status=200, headers={"Content-Type": "text/plain"}, read=lambda amt=None: b"x", release_conn=lambda: None
        )

    monkeypatch.setattr(urllib3.HTTPConnectionPool, "urlopen", urlopen)
    monkeypatch.setattr(workitem_attachment, "attachment_to_dict", lambda attachment, slug: {})
    client = SimpleNamespace(
        work_items=SimpleNamespace(attachments=SimpleNamespace(upload_from_bytes=lambda **kwargs: None))
    )
    assert is_plane_origin(f"{PLANE}/f.txt")

    workitem_attachment._upload(client, "acme", "p", "w", f"{PLANE}/f.txt", "")

    assert sent == [{"Host": "plane.test"}]


# --- redirects ---------------------------------------------------------------


def test_a_redirect_off_the_plane_origin_drops_the_headers_and_one_on_it_keeps_them(extra, routed):
    sent, routes = routed
    routes[f"{PLANE}/api/v1/users/me/"] = f"{PLANE}/api/v1/users/me/again/"
    routes[f"{PLANE}/api/v1/users/me/again/"] = f"{STORAGE}/elsewhere"
    try:
        get_plane_client_context().client.users.get_me()
    except Exception:
        pass

    assert [r.url for r in sent] == [
        f"{PLANE}/api/v1/users/me/",
        f"{PLANE}/api/v1/users/me/again/",
        f"{STORAGE}/elsewhere",
    ]
    assert [_carries(r) for r in sent] == [True, True, False]


def test_a_redirect_to_another_scheme_on_the_same_host_drops_them(extra, routed):
    sent, routes = routed
    routes[f"{PLANE}/api/v1/users/me/"] = "https://plane.test/api/v1/users/me/"
    try:
        get_plane_client_context().client.users.get_me()
    except Exception:
        pass
    assert [_carries(r) for r in sent] == [True, False]


def test_an_attachment_read_redirected_off_the_plane_origin_drops_them(extra, routed, monkeypatch):
    sent, routes = routed
    routes[f"{PLANE}/a.txt"] = f"{STORAGE}/a.txt"
    _read_from(f"{PLANE}/a.txt", monkeypatch)
    assert [(r.url, _carries(r)) for r in sent] == [(f"{PLANE}/a.txt", True), (f"{STORAGE}/a.txt", False)]


# --- the auth providers' own calls to Plane ----------------------------------


def test_header_auth_validation_sends_the_headers_and_the_callers_key(extra, httpx_wire):
    sent, _ = httpx_wire
    assert asyncio.run(PlaneHeaderAuthProvider()._validate_api_key("callers-key"))

    (request,) = sent
    assert str(request.url) == f"{PLANE}/api/v1/users/me/"
    assert {k: request.headers.get(k) for k in EXTRA} == EXTRA
    assert request.headers["x-api-key"] == "callers-key"


def test_oauth_verification_sends_the_headers_on_both_calls(extra, httpx_wire):
    sent, body = httpx_wire
    body["json"] = {}
    asyncio.run(PlaneOAuthTokenVerifier(plane_base_url=PLANE).verify_token("oauth-tok"))

    assert [str(r.url) for r in sent] == [f"{PLANE}/api/v1/users/me/", f"{PLANE}/auth/o/app-installation/"]
    for request in sent:
        assert _carries(request)
        assert request.headers["authorization"] == "Bearer oauth-tok"


def test_the_oauth_token_exchange_sends_the_headers_and_keeps_client_auth(extra, httpx_wire):
    sent, body = httpx_wire
    body["json"] = {"access_token": "a", "token_type": "Bearer"}
    provider = PlaneOAuthProvider(
        client_id="cid",
        client_secret="csecret",
        base_url="http://localhost:8211",
        plane_base_url=PLANE,
        plane_internal_base_url=PLANE,
        client_storage=MemoryStore(),
        jwt_signing_key="k" * 32,
    )

    async def exchange():
        async with provider._create_upstream_oauth_client() as oauth:
            await oauth.fetch_token(f"{PLANE}/auth/o/token/", code="c", redirect_uri="http://localhost:8211/cb")

    asyncio.run(exchange())

    (request,) = sent
    assert str(request.url) == f"{PLANE}/auth/o/token/"
    assert {k: request.headers.get(k) for k in EXTRA} == EXTRA
    assert request.headers["authorization"].startswith("Basic ")


def test_an_auth_call_to_a_non_plane_origin_gets_no_headers(extra, httpx_wire):
    """The httpx clients apply the same origin rule as the requests sessions."""
    sent, _ = httpx_wire
    asyncio.run(PlaneOAuthTokenVerifier(plane_base_url="http://other.test").verify_token("oauth-tok"))

    assert str(sent[0].url) == "http://other.test/api/v1/users/me/"
    assert not any(_carries(r) for r in sent)
