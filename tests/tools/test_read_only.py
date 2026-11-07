"""The read-only server lets through declared reads only, and fails closed on everything else."""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client, FastMCP

import plane_mcp.server as server_module
from plane_mcp.middleware import ReadOnly
from plane_mcp.tools.registry import RESOURCES, alias_table


@pytest.fixture(scope="module")
def rejection():
    return ReadOnly().rejection


def _every_action():
    return [(mod.NAME, action) for mod in RESOURCES for action in mod.ACTIONS]


@pytest.mark.parametrize(("tool", "action"), _every_action(), ids=lambda v: getattr(v, "name", v))
def test_exactly_the_declared_reads_pass(rejection, tool, action):
    message = rejection(tool, {"action": action.name})
    if action.read:
        assert message is None
    else:
        assert message and "read-only" in message


def test_a_refusal_names_the_reads_it_would_allow(rejection):
    message = rejection("workitem", {"action": "create"})
    for read in ("list", "retrieve", "search", "count"):
        assert read in message


def test_retired_names_follow_their_action(rejection):
    reads = {mod.NAME: {a.name for a in mod.ACTIONS if a.read} for mod in RESOURCES}
    for legacy, (tool, action) in alias_table().items():
        assert (rejection(legacy, {}) is None) == (action in reads[tool]), legacy


def test_a_single_operation_tool_needs_no_action(rejection):
    assert rejection("get_pql_reference", {"detail": "brief"}) is None


@pytest.mark.parametrize(
    "tool, arguments",
    [
        ("no_such_tool", {"action": "list"}),
        ("workitem", {}),
        ("workitem", {"action": "no_such_action"}),
    ],
)
def test_anything_unrecognised_is_refused(rejection, tool, arguments):
    assert rejection(tool, arguments)


@pytest.mark.parametrize("action", [["delete"], {"name": "delete"}, 3, None])
def test_a_non_string_action_is_refused_as_missing(rejection, action):
    message = rejection("workitem", {"action": action})
    assert message and "read-only" in message and "no action" in message


def test_a_non_string_action_is_refused_end_to_end(spy):
    answer = _call("workitem", {"action": ["delete"], "project_id": "p", "workitem_id": "w"})
    assert "read-only" in answer
    assert spy.recorder.calls == []


def _call(tool: str, arguments: dict) -> str:
    server = server_module._configured(FastMCP("read-only"), read_only=True)

    async def run():
        async with Client(server) as client:
            result = await client.call_tool(tool, arguments, raise_on_error=False)
            return str(result.content[0].text if result.content else "")

    return asyncio.new_event_loop().run_until_complete(run())


def test_a_refused_write_never_reaches_plane(spy):
    answer = _call("workitem", {"action": "create", "project_id": "p", "name": "x"})
    assert "read-only" in answer
    assert spy.recorder.calls == []


def test_a_refused_retired_write_never_reaches_plane(spy):
    answer = _call("create_work_item", {"project_id": "p", "name": "x"})
    assert "read-only" in answer
    assert spy.recorder.calls == []


def test_a_read_goes_through(spy):
    answer = _call("label", {"action": "list", "project_id": "p"})
    assert "read-only" not in answer
    assert spy.recorder.calls, "the read did not reach the client"


def test_only_the_read_only_header_server_has_the_check():
    def stack(mcp):
        return [type(m).__name__ for m in mcp.middleware]

    assert "ReadOnly" in stack(server_module.get_header_mcp(read_only=True))
    assert "ReadOnly" not in stack(server_module.get_header_mcp())
    assert "ReadOnly" not in stack(server_module.get_stdio_mcp())
