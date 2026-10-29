"""`PLANE_MCP_DISABLED_TOOLS` drops resource tools, and their retired names, from the surface."""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP

from plane_mcp.tools import register_tools
from plane_mcp.tools.registry import RESOURCES, alias_table


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _surface(**kwargs) -> tuple[FastMCP, int]:
    mcp = FastMCP("disabled")
    return mcp, register_tools(mcp, **kwargs)


def _retired_name_of(tool: str) -> str:
    return next(legacy for legacy, (target, _) in alias_table().items() if target == tool)


def test_unset_advertises_every_resource(monkeypatch):
    monkeypatch.delenv("PLANE_MCP_DISABLED_TOOLS", raising=False)
    mcp, count = _surface()
    assert count == len(RESOURCES)
    assert [t.name for t in _run(mcp.list_tools())] == [mod.NAME for mod in RESOURCES]


def test_env_drops_the_named_tools_and_keeps_the_order(monkeypatch):
    monkeypatch.setenv("PLANE_MCP_DISABLED_TOOLS", " customer, release ,,")
    mcp, count = _surface()
    expected = [mod.NAME for mod in RESOURCES if mod.NAME not in {"customer", "release"}]
    assert count == len(expected)
    assert [t.name for t in _run(mcp.list_tools())] == expected


def test_a_disabled_tool_cannot_be_reached_by_name_or_retired_name():
    mcp, _ = _surface(disabled={"label"})
    assert _run(mcp.get_tool("label")) is None
    assert _run(mcp.get_tool(_retired_name_of("label"))) is None
    assert _run(mcp.get_tool(_retired_name_of("state"))) is not None


def test_an_unknown_name_fails_at_startup():
    with pytest.raises(ValueError, match="no_such_tool"):
        _surface(disabled={"label", "no_such_tool"})
