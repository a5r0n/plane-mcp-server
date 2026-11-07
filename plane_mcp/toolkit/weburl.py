"""Web links for work items: `{web}/{workspace}/browse/{PROJECT}-{seq}/`.

Built only from what the result and the configuration carry. A link that cannot
be built is left out rather than guessed.
"""

from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

WEB_URL_ENV = "PLANE_WEB_URL"
# Plane Cloud serves its API and its web app from different hosts.
_CLOUD_API_HOST, _CLOUD_WEB_HOST = "api.plane.so", "app.plane.so"
_IDENTIFIER_TTL = 300.0

_identifiers: dict[tuple[str, str, str], tuple[float, str]] = {}


def web_base() -> str:
    """The web app's base URL: PLANE_WEB_URL, else PLANE_BASE_URL (cloud API host mapped to the app)."""
    override = os.getenv(WEB_URL_ENV, "").strip()
    if override:
        return override.rstrip("/")
    base = (os.getenv("PLANE_BASE_URL") or f"https://{_CLOUD_API_HOST}").strip().rstrip("/")
    parts = urlsplit(base)
    if (parts.hostname or "").lower() == _CLOUD_API_HOST:
        base = urlunsplit(parts._replace(netloc=_CLOUD_WEB_HOST))
    return base


def build_web_url(workspace_slug: Any, project_identifier: Any, sequence_id: Any) -> str | None:
    """The item's web link, or None when any part is missing."""
    if not (isinstance(workspace_slug, str) and workspace_slug):
        return None
    if not (isinstance(project_identifier, str) and project_identifier):
        return None
    if isinstance(sequence_id, bool) or not isinstance(sequence_id, int):
        return None
    return f"{web_base()}/{workspace_slug}/browse/{project_identifier}-{sequence_id}/"


def _field(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


class ProjectIdentifiers:
    """Resolves project ids to identifiers for one call, through a short process-wide cache."""

    def __init__(self, client: Any, workspace_slug: str) -> None:
        self._client = client
        self._workspace_slug = workspace_slug
        self._api = os.getenv("PLANE_INTERNAL_BASE_URL") or os.getenv("PLANE_BASE_URL") or ""

    def of(self, project: Any) -> str | None:
        if isinstance(project, dict):  # expand=project already carries it
            identifier = project.get("identifier")
            return identifier if isinstance(identifier, str) and identifier else None
        if not (isinstance(project, str) and project):
            return None
        key = (self._api, self._workspace_slug, project)
        cached = _identifiers.get(key)
        now = time.monotonic()
        if cached and now - cached[0] < _IDENTIFIER_TTL:
            return cached[1]
        try:
            identifier = self._client.projects.retrieve(workspace_slug=self._workspace_slug, project_id=project)
            identifier = getattr(identifier, "identifier", None)
        except Exception as exc:  # a link is a convenience; never fail the call for it
            logger.warning("web_url: project %s lookup failed: %s", project, exc)
            return None
        if not (isinstance(identifier, str) and identifier):
            return None
        _identifiers[key] = (now, identifier)
        return identifier

    def web_url(self, item: Any) -> str | None:
        sequence_id = _field(item, "sequence_id")
        project = _field(item, "project")
        if isinstance(sequence_id, bool) or not isinstance(sequence_id, int) or not project:
            return None
        return build_web_url(self._workspace_slug, self.of(project), sequence_id)


def with_web_url(item: Any, identifiers: ProjectIdentifiers) -> Any:
    """Attach `web_url` to a work item model or dict when it can be built; return the item."""
    url = identifiers.web_url(item)
    if url is None:
        return item
    if isinstance(item, dict):
        item["web_url"] = url
    else:
        try:
            item.web_url = url
        except (AttributeError, ValueError, TypeError):
            pass
    return item
