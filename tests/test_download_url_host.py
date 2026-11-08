"""download_url is signed for PLANE_WEB_URL's host when the API is reached on another origin.

Plane presigns for the request's Host, and a SigV4 GET signs the host, so the URL
cannot be rebased afterwards: the host has to be right when Plane signs it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import requests
from fastmcp import FastMCP

from plane_mcp.client import get_plane_client_context, plane_extra_headers
from plane_mcp.tools import workitem_attachment

INTERNAL = "http://api:8000"
WEB = "https://plane.example.com"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("PLANE_BASE_URL", INTERNAL)
    monkeypatch.delenv("PLANE_INTERNAL_BASE_URL", raising=False)
    monkeypatch.delenv("PLANE_WEB_URL", raising=False)
    monkeypatch.delenv("PLANE_EXTRA_HEADERS", raising=False)
    monkeypatch.setenv("PLANE_API_KEY", "the-key")
    monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "acme")
    plane_extra_headers.cache_clear()


@pytest.fixture
def plane(monkeypatch):
    """A Plane that redirects to a URL signed for the Host it was asked on; `refuse` makes it 400 an override."""
    sent: list[requests.PreparedRequest] = []
    state = {"refuse": False}

    def send(self, request, **kwargs):
        sent.append(request)
        host = request.headers.get("Host")
        response = requests.Response()
        response.request = request
        if host and state["refuse"]:
            response.status_code = 400
            return response
        scheme = request.headers.get("X-Forwarded-Proto", "http")
        response.status_code = 302
        response.headers["Location"] = f"{scheme}://{host or 'api:8000'}/uploads/key?X-Amz-Signature=sig"
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    return sent, state


def _download_url():
    client = get_plane_client_context().client
    return workitem_attachment._public_download_url(client, "acme", "p", "w", "a")


def test_signs_for_the_web_host_when_the_api_is_internal(plane, monkeypatch):
    monkeypatch.setenv("PLANE_WEB_URL", WEB)
    sent, _ = plane

    assert _download_url() == "https://plane.example.com/uploads/key?X-Amz-Signature=sig"
    (request,) = sent
    assert request.url.startswith(INTERNAL)
    assert request.headers["Host"] == "plane.example.com"
    assert request.headers["X-Forwarded-Proto"] == "https"
    assert request.headers["X-Api-Key"] == "the-key"


@pytest.mark.parametrize("web", [None, INTERNAL], ids=["web-url-unset", "same-origin"])
def test_leaves_the_host_alone_otherwise(web, plane, monkeypatch):
    if web:
        monkeypatch.setenv("PLANE_WEB_URL", web)
    sent, _ = plane

    assert _download_url() == "http://api:8000/uploads/key?X-Amz-Signature=sig"
    (request,) = sent
    assert "Host" not in request.headers


def test_falls_back_to_the_api_host_when_plane_refuses_the_web_host(plane, monkeypatch):
    monkeypatch.setenv("PLANE_WEB_URL", WEB)
    sent, state = plane
    state["refuse"] = True

    assert _download_url() == "http://api:8000/uploads/key?X-Amz-Signature=sig"
    assert [r.headers.get("Host") for r in sent] == ["plane.example.com", None]


def test_read_keeps_the_api_host(plane, monkeypatch):
    """read fetches the file from beside the server, so its link stays on the API host."""
    monkeypatch.setenv("PLANE_WEB_URL", WEB)
    sent, _ = plane
    client = get_plane_client_context().client

    workitem_attachment._download_url(client, "acme", "p", "w", "a")

    (request,) = sent
    assert "Host" not in request.headers


def test_the_download_url_action_returns_the_web_host_link(plane, monkeypatch):
    """Through the registered tool, so the action is held to the public signing, not only the helper."""
    monkeypatch.setenv("PLANE_WEB_URL", WEB)
    monkeypatch.setattr(workitem_attachment, "_find", lambda *a: SimpleNamespace(attributes={"name": "a.pdf"}))
    mcp = FastMCP("download-url")
    workitem_attachment.register(mcp)
    tool = asyncio.run(mcp.get_tool(workitem_attachment.NAME))

    result = tool.fn(action="download_url", project_id="p", workitem_id="w", attachment_id="a")

    assert result["download_url"] == "https://plane.example.com/uploads/key?X-Amz-Signature=sig"
