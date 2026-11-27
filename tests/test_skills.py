"""Skills served from `<PLANE_MCP_SKILLS_DIR>/current`: resources, the skill tool, and following a deploy's swap."""

from __future__ import annotations

import asyncio
import json
import os
import shutil

import pytest
from fastmcp import Client

from plane_mcp.instructions import SERVER_INSTRUCTIONS
from plane_mcp.middleware import ReadOnly
from plane_mcp.server import get_stdio_mcp
from plane_mcp.skills import INSTRUCTIONS
from plane_mcp.tools import register_tools
from tests._skills import make_skill_set

OLD = "1" * 40
NEW = "2" * 40


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _session(mcp, fn):
    async with Client(mcp) as client:
        return await fn(client)


def _listing(mcp) -> list[str]:
    tools = _run(mcp.list_tools())
    return [json.dumps(t.to_mcp_tool().model_dump(exclude_none=True), sort_keys=True) for t in tools]


def _call(mcp, **arguments):
    return _run(_session(mcp, lambda c: c.call_tool("skill", arguments, raise_on_error=False)))


def _text(result) -> str:
    return result.content[0].text


@pytest.fixture
def root(tmp_path, monkeypatch):
    make_skill_set(tmp_path, OLD, {"squadron-plane": "Shared rules", "squadron-pilot": "Pilot"})
    (tmp_path / OLD[:12] / "squadron-plane" / "notes.md").write_text("old notes\n")
    monkeypatch.setenv("PLANE_MCP_SKILLS_DIR", str(tmp_path))
    return tmp_path


def _swap(root, sha, skills):
    """What a deploy does: write the new set, swap `current` with one rename, delete the old set."""
    old = (root / "current").resolve()
    (root / "current").rename(root / "previous")
    make_skill_set(root, sha, skills)
    (root / "previous").unlink()
    shutil.rmtree(old)


def test_unset_env_serves_no_skills_and_leaves_the_listing_unchanged(root, monkeypatch):
    with_skills = _listing(get_stdio_mcp())
    monkeypatch.delenv("PLANE_MCP_SKILLS_DIR")
    mcp = get_stdio_mcp()

    assert mcp.instructions == SERVER_INSTRUCTIONS
    assert _listing(mcp) == [t for t in with_skills if '"name": "skill"' not in t]
    assert _run(_session(mcp, lambda c: c.list_resources())) == []
    assert _run(_session(mcp, lambda c: c.list_resource_templates())) == []


def test_set_env_adds_the_instructions_line(root):
    assert get_stdio_mcp().instructions == SERVER_INSTRUCTIONS + INSTRUCTIONS


def test_resources_list_each_skill_and_read_from_the_current_set(root):
    mcp = get_stdio_mcp()
    uris = {str(r.uri) for r in _run(_session(mcp, lambda c: c.list_resources()))}
    templates = {t.uriTemplate for t in _run(_session(mcp, lambda c: c.list_resource_templates()))}

    assert {"skill://squadron-plane/SKILL.md", "skill://squadron-plane/_manifest"} <= uris
    assert "skill://squadron-pilot/SKILL.md" in uris
    assert "skill://squadron-plane/{path*}" in templates
    notes = _run(_session(mcp, lambda c: c.read_resource("skill://squadron-plane/notes.md")))
    assert notes[0].text == "old notes\n"


def test_skill_list_names_each_skill_with_the_version(root):
    result = json.loads(_text(_call(get_stdio_mcp(), action="list")))

    assert result["version"] == OLD
    assert result["skills"] == [
        {"name": "squadron-pilot", "description": "Pilot"},
        {"name": "squadron-plane", "description": "Shared rules"},
    ]


def test_skill_get_returns_the_text_and_version(root):
    result = json.loads(_text(_call(get_stdio_mcp(), action="get", name="squadron-plane")))

    assert result["version"] == OLD
    assert result["text"] == (root / "current" / "squadron-plane" / "SKILL.md").read_text()


def test_a_swap_serves_the_new_set_with_no_restart(root):
    mcp = get_stdio_mcp()
    _run(_session(mcp, lambda c: c.list_resources()))  # the server has followed the old set

    _swap(root, NEW, {"squadron-plane": "New rules"})

    uris = {str(r.uri) for r in _run(_session(mcp, lambda c: c.list_resources()))}
    assert "skill://squadron-pilot/SKILL.md" not in uris
    body = _run(_session(mcp, lambda c: c.read_resource("skill://squadron-plane/SKILL.md")))
    assert "New rules" in body[0].text
    assert json.loads(_text(_call(mcp, action="list")))["version"] == NEW


@pytest.mark.parametrize("uri", ["skill://squadron-plane/SKILL.md", "skill://squadron-plane/notes.md"])
def test_the_first_read_after_a_swap_serves_the_new_set(root, uri):
    mcp = get_stdio_mcp()
    _run(_session(mcp, lambda c: c.read_resource(uri)))  # the server has followed the old set

    _swap(root, NEW, {"squadron-plane": "New rules"})
    (root / NEW[:12] / "squadron-plane" / "notes.md").write_text("new notes\n")

    body = _run(_session(mcp, lambda c: c.read_resource(uri)))
    assert "New rules" in body[0].text or body[0].text == "new notes\n"
    assert json.loads(_text(_call(mcp, action="get", name="squadron-plane")))["version"] == NEW


def test_a_missing_current_is_an_error_naming_the_folder(root):
    (root / "current").unlink()
    mcp = get_stdio_mcp()

    result = _text(_call(mcp, action="list"))
    assert result.startswith("Error:") and str(root / "current") in result
    assert _run(_session(mcp, lambda c: c.list_resources())) == []


@pytest.mark.parametrize("path", ["../squadron-pilot/SKILL.md", "../../VERSION", "/etc/passwd"])
def test_a_path_leaving_the_skill_folder_is_refused(root, path):
    result = _text(_call(get_stdio_mcp(), action="get", name="squadron-plane", path=path))
    assert result.startswith("Error:") and "leaves" in result


@pytest.mark.parametrize("name", ["..", "../x", "VERSION", "missing"])
def test_a_name_that_is_not_a_skill_is_refused(root, name):
    result = _text(_call(get_stdio_mcp(), action="get", name=name))
    assert result.startswith("Error:")


def test_a_resource_path_leaving_the_skill_folder_is_refused(root):
    mcp = get_stdio_mcp()
    with pytest.raises(Exception, match="File not found|escapes"):
        _run(_session(mcp, lambda c: c.read_resource("skill://squadron-plane/../../VERSION")))


def test_the_read_only_server_allows_skill_and_still_refuses_writes(root):
    from fastmcp import FastMCP

    from plane_mcp.server import _configured

    guard = ReadOnly()
    assert guard.rejection("skill", {"action": "list"}) is None
    assert guard.rejection("workitem", {"action": "create"}) is not None

    server = _configured(FastMCP("read-only"), read_only=True)
    got = json.loads(_text(_call(server, action="get", name="squadron-plane")))
    assert got["version"] == OLD
    refused = _run(
        _session(server, lambda c: c.call_tool("workitem", {"action": "create", "project_id": "p", "name": "x"}))
    )
    assert "read-only" in _text(refused)


def test_disabled_tools_cannot_hide_skill(root):
    from fastmcp import FastMCP

    mcp = FastMCP("disabled")
    register_tools(mcp, disabled={"skill", "label"})
    names = {t.name for t in _run(mcp.list_tools())}
    assert "skill" in names and "label" not in names


@pytest.fixture
def outside(root, tmp_path_factory):
    """A skill folder outside the set, linked into it as `outside-skill`."""
    folder = tmp_path_factory.mktemp("outside") / "outside-skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\ndescription: Outside\n---\noutside text\n")
    (root / OLD[:12] / "outside-skill").symlink_to(folder, target_is_directory=True)
    return folder


def test_a_skill_linked_from_outside_the_set_is_not_served(root, outside):
    mcp = get_stdio_mcp()

    listed = json.loads(_text(_call(mcp, action="list")))["skills"]
    assert "outside-skill" not in {s["name"] for s in listed}
    result = _text(_call(mcp, action="get", name="outside-skill"))
    assert result.startswith("Error:") and "no skill named" in result
    uris = {str(r.uri) for r in _run(_session(mcp, lambda c: c.list_resources()))}
    assert not any(u.startswith("skill://outside-skill/") for u in uris)
    with pytest.raises(Exception, match="not found"):
        _run(_session(mcp, lambda c: c.read_resource("skill://outside-skill/SKILL.md")))


def _moves_after_first_resolve(monkeypatch, root, skills_after):
    """current is resolved once, then a deploy swaps it before the answer is read."""
    from plane_mcp import skills

    real = skills.current_set
    calls = []

    def resolve(r):
        result = real(r)
        if not calls:
            (root / "current").unlink()
            make_skill_set(root, NEW, skills_after)
        calls.append(result)
        return result

    monkeypatch.setattr(skills, "current_set", resolve)


def test_list_answers_from_the_new_set_when_current_moves_mid_answer(root, monkeypatch):
    _moves_after_first_resolve(monkeypatch, root, {"squadron-plane": "New rules", "demo": "Demo"})

    result = json.loads(_text(_call(get_stdio_mcp(), action="list")))
    assert result["version"] == NEW
    assert {s["name"] for s in result["skills"]} == {"squadron-plane", "demo"}


def test_get_answers_from_the_new_set_when_current_moves_mid_answer(root, monkeypatch):
    _moves_after_first_resolve(monkeypatch, root, {"demo": "Demo"})

    result = json.loads(_text(_call(get_stdio_mcp(), action="get", name="demo")))
    assert result["version"] == NEW and "Demo" in result["text"]


@pytest.fixture
def warnings(caplog):
    """Warnings from the skills logger, which fastmcp's logging setup keeps from propagating to root."""
    from plane_mcp import skills

    skills.logger.addHandler(caplog.handler)
    caplog.set_level("WARNING", logger=skills.logger.name)
    yield caplog
    skills.logger.removeHandler(caplog.handler)


def _served(mcp) -> tuple[list[str], set[str]]:
    listed = json.loads(_text(_call(mcp, action="list")))["skills"]
    uris = {str(r.uri) for r in _run(_session(mcp, lambda c: c.list_resources()))}
    return [s["name"] for s in listed], uris


def test_a_non_utf8_skill_is_left_out_with_a_warning_naming_it(root, warnings):
    bad = root / OLD[:12] / "squadron-pilot" / "SKILL.md"
    bad.write_bytes(b"---\ndescription: \xff\xfe\n---\n")
    mcp = get_stdio_mcp()

    names, uris = _served(mcp)

    assert names == ["squadron-plane"]
    assert "skill://squadron-plane/SKILL.md" in uris and "skill://squadron-pilot/SKILL.md" not in uris
    assert str(bad) in warnings.text
    assert "not UTF-8" in _text(_call(mcp, action="get", name="squadron-pilot"))


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads any file")
def test_a_skill_that_cannot_be_read_is_left_out_with_a_warning_naming_it(root, warnings):
    bad = root / OLD[:12] / "squadron-pilot" / "SKILL.md"
    bad.chmod(0)
    try:
        mcp = get_stdio_mcp()
        names, uris = _served(mcp)
        result = _text(_call(mcp, action="get", name="squadron-pilot"))
    finally:
        bad.chmod(0o644)

    assert names == ["squadron-plane"]
    assert "skill://squadron-plane/SKILL.md" in uris and "skill://squadron-pilot/SKILL.md" not in uris
    assert str(bad) in warnings.text
    assert result.startswith("Error:")


def _main_file_loops(folder):
    folder.mkdir()
    (folder / "SKILL.md").symlink_to("SKILL.md")


def _folder_loops(folder):
    folder.symlink_to(folder.name, target_is_directory=True)


def _folder_shut(folder):
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\ndescription: Broken\n---\n")
    folder.chmod(0)


@pytest.mark.parametrize("breaks", [_main_file_loops, _folder_loops, _folder_shut])
def test_a_skill_that_cannot_be_checked_is_left_out_with_a_warning_naming_it(root, warnings, breaks):
    if breaks is _folder_shut and hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root reads any folder")
    broken = root / OLD[:12] / "broken"
    breaks(broken)
    try:
        mcp = get_stdio_mcp()
        names, uris = _served(mcp)
    finally:
        if not broken.is_symlink():
            broken.chmod(0o755)

    assert names == ["squadron-pilot", "squadron-plane"]
    assert "skill://squadron-plane/SKILL.md" in uris and not any(u.startswith("skill://broken/") for u in uris)
    assert str(broken) in warnings.text


def test_a_skill_md_linked_from_outside_the_set_is_not_served(root, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "SKILL.md"
    elsewhere.write_text("---\ndescription: Elsewhere\n---\nelsewhere text\n")
    main = root / OLD[:12] / "squadron-pilot" / "SKILL.md"
    main.unlink()
    main.symlink_to(elsewhere)
    mcp = get_stdio_mcp()

    names, uris = _served(mcp)

    assert names == ["squadron-plane"]
    assert not any(u.startswith("skill://squadron-pilot/") for u in uris)
    assert "no skill named" in _text(_call(mcp, action="get", name="squadron-pilot"))
