"""Squadron skills, served read-only from the set `<PLANE_MCP_SKILLS_DIR>/current` points to.

`current` is a symlink a deploy swaps to a new set and then deletes the old one, so it is
resolved on every request: a provider rooted at the link itself would keep the startup set.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from fastmcp.resources.base import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.providers.aggregate import AggregateProvider
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.server.providers.skills._common import parse_frontmatter
from fastmcp.utilities.versions import VersionSpec

ENV = "PLANE_MCP_SKILLS_DIR"
MAIN_FILE = "SKILL.md"

INSTRUCTIONS = """
## Skills

Before filing or changing work, read `squadron-plane`: `skill://squadron-plane/SKILL.md`,
or `skill get` with name `squadron-plane`.
"""


def skills_dir() -> Path | None:
    """The configured skills folder, or None when serving skills is off."""
    raw = os.getenv(ENV, "").strip()
    return Path(raw) if raw else None


class SkillsUnavailable(Exception):
    """The configured folder has no readable current set."""


class SkillSet(NamedTuple):
    path: Path  # the resolved set folder, never the `current` link
    version: str


def current_set(root: Path) -> SkillSet:
    """Resolve `<root>/current` once, so everything a caller reads comes from one set."""
    link = root / "current"
    try:
        path = link.resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        raise SkillsUnavailable(f"no skill set at {link}: the folder is missing") from exc
    if not path.is_dir():
        raise SkillsUnavailable(f"no skill set at {link}: it is not a folder")
    try:
        version = (path / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        version = ""
    return SkillSet(path, version)


def skill_names(skill_set: SkillSet) -> list[str]:
    return sorted(p.name for p in skill_set.path.iterdir() if (p / MAIN_FILE).is_file())


def describe(skill_set: SkillSet, name: str) -> str:
    frontmatter, _ = parse_frontmatter((skill_set.path / name / MAIN_FILE).read_text(encoding="utf-8"))
    return str(frontmatter.get("description", ""))


def skill_file(skill_set: SkillSet, name: str, path: str = MAIN_FILE) -> Path:
    """The file `path` inside skill `name`; refuses anything that leaves the skill folder."""
    folder = (skill_set.path / name).resolve()
    if folder.parent != skill_set.path or not (folder / MAIN_FILE).is_file():
        raise SkillsUnavailable(f"no skill named {name!r}; skills: {', '.join(skill_names(skill_set))}")
    target = (folder / path).resolve()
    if not target.is_relative_to(folder):
        raise SkillsUnavailable(f"path {path!r} leaves the {name} skill folder")
    if not target.is_file():
        raise SkillsUnavailable(f"no file {path!r} in the {name} skill")
    return target


class CurrentSkills(AggregateProvider):
    """Skill resources for whichever set `current` points to at the start of each request."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self._root = root
        self._target: Path | None = None

    def _follow(self) -> None:
        try:
            target = current_set(self._root).path
        except SkillsUnavailable:
            target = None
        if target == self._target:
            return
        # Build the new provider before swapping, so a concurrent request sees the old set or the new one.
        self.providers = [SkillsDirectoryProvider(roots=target)] if target else []
        self._target = target

    async def _list_resources(self) -> Sequence[Resource]:
        self._follow()
        return await super()._list_resources()

    async def _list_resource_templates(self) -> Sequence[ResourceTemplate]:
        self._follow()
        return await super()._list_resource_templates()

    async def _get_resource(self, uri: str, version: VersionSpec | None = None) -> Resource | None:
        self._follow()
        return await super()._get_resource(uri, version)

    async def _get_resource_template(self, uri: str, version: VersionSpec | None = None) -> ResourceTemplate | None:
        self._follow()
        return await super()._get_resource_template(uri, version)
