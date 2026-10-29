"""Plane client initialization for MCP server."""

import json
import os
from collections.abc import MutableMapping
from functools import cache
from typing import Any, NamedTuple
from urllib.parse import urlsplit

import requests
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.dependencies import get_access_token
from fastmcp.utilities.logging import get_logger
from plane import PlaneClient
from plane.api.work_items import attachments as _sdk_attachments

logger = get_logger(__name__)

EXTRA_HEADERS_ENV = "PLANE_EXTRA_HEADERS"
# The server sets these itself; letting config replace them would swap the caller's identity.
_RESERVED_HEADERS = frozenset({"authorization", "x-api-key"})


@cache
def plane_extra_headers() -> dict[str, str]:
    """Headers from PLANE_EXTRA_HEADERS, sent on every request to the Plane API.

    Accepts a JSON object or `Name: value` lines. Errors name the header or line, never a value.
    """
    raw = os.getenv(EXTRA_HEADERS_ENV, "").strip()
    if not raw:
        return {}
    if raw[0] in "{[":
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{EXTRA_HEADERS_ENV} is not valid JSON (line {exc.lineno}, column {exc.colno})") from None
        if not isinstance(parsed, dict):
            raise ValueError(f"{EXTRA_HEADERS_ENV} must be a JSON object of header names to string values")
        pairs = list(parsed.items())
    else:
        pairs = []
        for number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            name, sep, value = line.partition(":")
            if not sep:
                raise ValueError(f"{EXTRA_HEADERS_ENV} line {number} is not of the form `Name: value`")
            pairs.append((name.strip(), value.strip()))

    headers: dict[str, str] = {}
    for name, value in pairs:
        if not name or any(c.isspace() or c == ":" for c in name):
            raise ValueError(f"{EXTRA_HEADERS_ENV} has an invalid header name {name!r}")
        if not isinstance(value, str) or "\r" in value or "\n" in value:
            raise ValueError(f"{EXTRA_HEADERS_ENV} header {name!r} must have a single-line string value")
        if name.lower() in _RESERVED_HEADERS:
            raise ValueError(f"{EXTRA_HEADERS_ENV} may not set {name!r}; the server sets it from the credentials")
        headers[name] = value
    return headers


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> tuple[str, str, int | None] | None:
    """(scheme, lowercased host, effective port), or None if `url` has no usable origin."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if not scheme or not parts.hostname:
        return None
    return scheme, parts.hostname, port or _DEFAULT_PORTS.get(scheme)


def is_plane_origin(url: str) -> bool:
    """Whether `url` is on the origin of PLANE_BASE_URL or PLANE_INTERNAL_BASE_URL."""
    bases = (os.getenv("PLANE_BASE_URL") or "https://api.plane.so", os.getenv("PLANE_INTERNAL_BASE_URL"))
    origin = _origin(url)
    return origin is not None and origin in {_origin(base) for base in bases if base}


def scope_extra_headers(url: str, headers: MutableMapping[str, str]) -> None:
    """Add PLANE_EXTRA_HEADERS to `headers` if `url` is a Plane origin, else remove them."""
    extra = plane_extra_headers()
    if not extra:
        return
    if is_plane_origin(url):
        headers.update(extra)
    else:
        for name in extra:
            headers.pop(name, None)


async def scope_extra_headers_hook(request) -> None:
    """httpx request hook applying `scope_extra_headers`."""
    scope_extra_headers(str(request.url), request.headers)


class PlaneSession(requests.Session):
    """A session that sends PLANE_EXTRA_HEADERS to Plane origins only, redirects included."""

    def prepare_request(self, request: requests.Request) -> requests.PreparedRequest:
        prepared = super().prepare_request(request)
        scope_extra_headers(prepared.url, prepared.headers)
        return prepared

    def rebuild_auth(self, prepared_request: requests.PreparedRequest, response: requests.Response) -> None:
        # requests strips only Authorization on a cross-host redirect; the extra headers need the same.
        super().rebuild_auth(prepared_request, response)
        scope_extra_headers(prepared_request.url, prepared_request.headers)


def _use_plane_sessions(client: PlaneClient) -> None:
    """Make every requests session the SDK client owns a PlaneSession.

    plane-sdk has no hook for extra headers, and gives each resource its own plain session.
    """
    seen: set[int] = set()
    stack: list[Any] = [client]
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        if type(obj) is requests.Session:
            obj.__class__ = PlaneSession
        elif type(obj).__module__.startswith("plane."):
            stack.extend(getattr(obj, "__dict__", {}).values())


class _StorageRequests:
    """`requests` for the SDK's presigned upload, posting through a PlaneSession."""

    def __getattr__(self, name: str) -> Any:
        return getattr(requests, name)

    @staticmethod
    def post(url: str, **kwargs: Any) -> requests.Response:
        with PlaneSession() as session:
            return session.post(url, **kwargs)


# upload_from_bytes posts to storage with module-level requests.post, outside any session we can reach.
_sdk_attachments._requests = _StorageRequests()


class PlaneClientContext(NamedTuple):
    """Context containing Plane client and workspace information."""

    client: PlaneClient
    workspace_slug: str


def _workspace_slug(token: AccessToken | None) -> str:
    """The slug a connection is bound to: its token's claim, else the environment."""
    if token:
        return token.claims.get("workspace_slug", "")
    return os.getenv("PLANE_WORKSPACE_SLUG", "")


def _connected_via(token: AccessToken | None) -> str:
    """How a connection authenticated: its token's claim, else the environment."""
    if token:
        return token.claims.get("auth_method", "oauth")
    return "environment"


def current_workspace() -> dict[str, Any]:
    """The workspace this connection is bound to, as far as its credentials say."""
    token = get_access_token()
    detail = (token.claims.get("workspace") if token else None) or {}
    return {
        "slug": _workspace_slug(token),
        "id": detail.get("id"),
        "name": detail.get("name"),
        "connected_via": _connected_via(token),
    }


def get_plane_client_context() -> PlaneClientContext:
    """
    Initialize and return a PlaneClient instance with workspace context.

    Authentication is handled by the PlaneOAuthProvider, which supports:
    1. Environment variables (PLANE_API_KEY + PLANE_WORKSPACE_SLUG)
    2. HTTP headers (x-api-key + x-workspace-slug)
    3. OAuth access token

    Environment variables:
    - PLANE_INTERNAL_BASE_URL: Internal URL for Plane API (preferred for server-to-server calls)
    - PLANE_BASE_URL: Base URL for Plane API (fallback, default: https://api.plane.so)

    Returns:
        PlaneClientContext containing configured PlaneClient instance and workspace slug

    Raises:
        ConfigurationError: If access token is not available or workspace slug is missing
    """
    base_url = os.getenv("PLANE_INTERNAL_BASE_URL") or os.getenv("PLANE_BASE_URL", "https://api.plane.so")

    api_key = os.getenv("PLANE_API_KEY", "")
    access_token = None

    # Get access token from the OAuth provider (which handles all auth methods)
    stored_access_token: AccessToken | None = get_access_token()
    workspace_slug = _workspace_slug(stored_access_token)
    if stored_access_token:
        # Determine authentication method to use appropriate PlaneClient constructor
        auth_method = _connected_via(stored_access_token)
        token = stored_access_token.token

        # For API key auth methods, use api_key parameter; for OAuth, use access_token
        if auth_method in ("api_key_env", "api_key_header"):
            api_key = token
        else:
            access_token = token

    if access_token:
        client = PlaneClient(
            base_url=base_url,
            access_token=access_token,
        )
    else:
        client = PlaneClient(
            base_url=base_url,
            api_key=api_key,
        )
    if plane_extra_headers():
        _use_plane_sessions(client)

    return PlaneClientContext(
        client=client,
        workspace_slug=workspace_slug,
    )
