import hashlib
import os
import time

import httpx
from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.utilities.logging import get_logger

from plane_mcp.client import scope_extra_headers_hook

logger = get_logger(__name__)

DEFAULT_PLANE_BASE_URL = "https://api.plane.so"
VALID_KEY_TTL_SECONDS = 60  # a revoked key keeps working this long, at most


class PlaneHeaderAuthProvider(TokenVerifier):
    def __init__(self, required_scopes: list[str] | None = None, timeout_seconds: int = 10):
        super().__init__(required_scopes=required_scopes)
        self.timeout_seconds = timeout_seconds
        # sha256(base_url, key) -> monotonic expiry. Only successes are kept.
        self._valid_until: dict[str, float] = {}

    async def _validate_api_key(self, token: str) -> bool:
        """Validate the API key against the Plane API, reusing a recent success."""
        base_url = (os.getenv("PLANE_INTERNAL_BASE_URL") or os.getenv("PLANE_BASE_URL", DEFAULT_PLANE_BASE_URL)).rstrip(
            "/"
        )
        cache_key = hashlib.sha256(f"{base_url}\n{token}".encode()).hexdigest()
        now = time.monotonic()
        if self._valid_until.get(cache_key, 0.0) > now:
            return True
        if await self._check_api_key(base_url, token):
            self._valid_until = {k: t for k, t in self._valid_until.items() if t > now}
            self._valid_until[cache_key] = now + VALID_KEY_TTL_SECONDS
            return True
        return False

    async def _check_api_key(self, base_url: str, token: str) -> bool:
        """One round trip to Plane: does this key authenticate?"""
        user_url = f"{base_url}/api/v1/users/me/"

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, event_hooks={"request": [scope_extra_headers_hook]}
            ) as client:
                response = await client.get(
                    user_url,
                    headers={
                        "x-api-key": token,
                        "Content-Type": "application/json",
                    },
                )
                if response.status_code != 200:
                    logger.warning("API key validation failed: %s", response.status_code)
                    return False
                return True
        except httpx.RequestError as e:
            logger.warning("API key validation request failed: %s", e)
            return False

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            from fastmcp.server.dependencies import get_http_headers

            headers = get_http_headers()

            if token:
                workspace_slug = headers.get("x-workspace-slug")
                if not workspace_slug:
                    logger.warning("x-api-key header found but x-workspace-slug is missing")
                    return None

                if not await self._validate_api_key(token):
                    logger.warning("API key validation against Plane API failed")
                    return None

                logger.info("API key validated successfully via Plane API")
                expires_at = int(time.time() + 3600)
                return AccessToken(
                    token=token,
                    client_id="api_key_header_user",
                    scopes=["read", "write"],
                    expires_at=expires_at,
                    claims={
                        "auth_method": "api_key_header",
                        "workspace_slug": workspace_slug,
                    },
                )
        except RuntimeError:
            # No active HTTP request available (e.g., stdio transport)
            logger.debug("No active HTTP request available for header check")
