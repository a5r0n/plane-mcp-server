"""Squadron skills: the deployed set, read-only, for agents with no checkout.

Registered only when `PLANE_MCP_SKILLS_DIR` is set; never hidden by `PLANE_MCP_DISABLED_TOOLS`.
"""

from __future__ import annotations

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
    if root is None:
        return

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
        try:
            current = skills.current_set(root)
            if action == "list":
                return {
                    "version": current.version,
                    "skills": [
                        {"name": n, "description": skills.describe(current, n)} for n in skills.skill_names(current)
                    ],
                }
            if not name:
                return missing("get", "name")
            file = skills.skill_file(current, name, path or skills.MAIN_FILE)
            return {
                "name": name,
                "path": path or skills.MAIN_FILE,
                "version": current.version,
                "text": file.read_text(),
            }
        except skills.SkillsUnavailable as exc:
            return f"Error: {exc}."
