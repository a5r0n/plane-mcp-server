"""Squadron skills: the deployed set, read-only, for agents with no checkout.

Registered only when `PLANE_MCP_SKILLS_DIR` is set; never hidden by `PLANE_MCP_DISABLED_TOOLS`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from plane_mcp import skills
from plane_mcp.toolkit import Action, build_description, missing

NAME = "skill"
TITLE = "Squadron skills"

ACTIONS = (
    Action("list", read=True, note="each skill's name and description, and the set's version"),
    Action("get", ("name",), ("path",), read=True, note="path is a file inside the skill; SKILL.md by default"),
)

FOOTER = "Skills are also resources: skill://<name>/SKILL.md, skill://<name>/_manifest, skill://<name>/<path>."

LEGACY: dict[str, str] = {}


def available() -> bool:
    return skills.skills_dir() is not None


def register(mcp: FastMCP) -> None:
    root = skills.skills_dir()

    @mcp.tool(
        name=NAME,
        description=build_description(
            "Squadron's skills, as last deployed: the rules for filing and working Plane items.",
            ACTIONS,
            FOOTER,
        ),
        annotations=ToolAnnotations(
            title=TITLE,
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def skill(action: Literal["list", "get"], name: str = "", path: str = "") -> dict | str:
        if action == "get" and not name:
            return missing("get", "name")
        answer, used = _answer(action, name, path)
        if _target() != used:
            # current moved while this answer was read: answer again from the new set.
            answer, _ = _answer(action, name, path)
        return answer

    def _target() -> Path | None:
        try:
            return skills.current_set(root).path
        except skills.SkillsUnavailable:
            return None

    def _answer(action: str, name: str, path: str) -> tuple[dict | str, Path | None]:
        current = None
        try:
            current = skills.current_set(root)
            if action == "list":
                found = skills.readable_skills(current)
                listed = [{"name": n, "description": d} for n, d in found.items()]
                return {"version": current.version, "skills": listed}, current.path
            file = skills.skill_file(current, name, path or skills.MAIN_FILE)
            try:
                text = file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return f"Error: {file.name} in the {name} skill is not UTF-8 text.", current.path
            return {
                "name": name,
                "path": path or skills.MAIN_FILE,
                "version": current.version,
                "text": text,
            }, current.path
        except (skills.SkillsUnavailable, OSError) as exc:
            return f"Error: {exc}.", current.path if current else None
