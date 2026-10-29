"""Consolidated tool surface: one action-dispatch tool per Plane resource."""

from __future__ import annotations

import os
from collections.abc import Iterable

from fastmcp import FastMCP
from fastmcp.utilities.logging import get_logger

from plane_mcp.toolkit import StripOutputSchemas
from plane_mcp.tools.legacy import LegacyNames

logger = get_logger(__name__)


def disabled_from_env() -> frozenset[str]:
    """Tool names listed in `PLANE_MCP_DISABLED_TOOLS`, comma-separated."""
    raw = os.getenv("PLANE_MCP_DISABLED_TOOLS", "")
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


def register_tools(mcp: FastMCP, *, legacy_names: bool = True, disabled: Iterable[str] | None = None) -> int:
    """Register every resource tool not in `disabled`. Returns the number advertised."""
    # Imported here, not at module scope: `registry` imports the resource modules
    # out of this package, so a top-level import would run while this module is
    # still initialising.
    from plane_mcp.tools.registry import RESOURCES, alias_table

    skip = disabled_from_env() if disabled is None else frozenset(disabled)
    if unknown := skip - {mod.NAME for mod in RESOURCES}:
        raise ValueError(f"PLANE_MCP_DISABLED_TOOLS names unknown tools: {', '.join(sorted(unknown))}")
    active = [mod for mod in RESOURCES if mod.NAME not in skip]

    for mod in active:
        mod.register(mcp)

    mcp.add_transform(StripOutputSchemas())
    if legacy_names:
        aliases = {legacy: target for legacy, target in alias_table().items() if target[0] not in skip}
        mcp.add_transform(LegacyNames(aliases))

    # Several clients cap how many tools they load and drop the rest silently.
    # One number in the log lets an operator compare against their client's limit.
    if skip:
        logger.info("Plane MCP: disabled %s", ", ".join(sorted(skip)))
    logger.info("Plane MCP: advertising %d tools", len(active))
    return len(active)
