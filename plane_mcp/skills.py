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
from fastmcp.server.providers.skills import SkillProvider
from fastmcp.server.providers.skills._common import parse_frontmatter
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.versions import VersionSpec

logger = get_logger(__name__)

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


def _contained(skill_set: SkillSet, name: str) -> Path | None:
    """The skill folder `name`, only if it and its SKILL.md resolve inside the set; one that can't be checked warns."""
    entry = skill_set.path / name
    try:
        folder = entry.resolve(strict=True)
        if folder.parent != skill_set.path or not folder.is_dir():
            return None
        main = (folder / MAIN_FILE).resolve(strict=True)
    except FileNotFoundError:
        return None
    except (OSError, RuntimeError) as exc:
        logger.warning("skipping skill %s: can't check %s: %s", name, entry, exc)
        return None
    if not main.is_relative_to(folder) or not main.is_file():
        return None
    return folder


def skill_names(skill_set: SkillSet) -> list[str]:
    return sorted(p.name for p in skill_set.path.iterdir() if _contained(skill_set, p.name))


def readable_skills(skill_set: SkillSet) -> dict[str, str]:
    """Each servable skill's description; a skill whose SKILL.md can't be read is left out, with a warning."""
    found = {}
    for name in skill_names(skill_set):
        main = skill_set.path / name / MAIN_FILE
        try:
            frontmatter, _ = parse_frontmatter(main.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            logger.warning("skipping skill %s: %s is not UTF-8 text", name, main)
            continue
        except OSError as exc:
            logger.warning("skipping skill %s: can't read %s: %s", name, main, exc)
            continue
        found[name] = str(frontmatter.get("description", ""))
    return found


def skill_file(skill_set: SkillSet, name: str, path: str = MAIN_FILE) -> Path:
    """The file `path` inside skill `name`; refuses anything that leaves the skill folder."""
    folder = _contained(skill_set, name)
    if folder is None:
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
            skill_set = current_set(self._root)
        except SkillsUnavailable:
            skill_set = None
        target = skill_set.path if skill_set else None
        if target == self._target:
            return
        # Build the new providers before swapping, so a concurrent request sees the old set or the new one.
        self.providers = _providers(skill_set) if skill_set else []
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


def _providers(skill_set: SkillSet) -> list[SkillProvider]:
    """One provider per servable skill; one that fails to load is left out, with a warning naming it."""
    providers = []
    for name in readable_skills(skill_set):
        try:
            providers.append(SkillProvider(skill_path=skill_set.path / name, main_file_name=MAIN_FILE))
        except (OSError, ValueError) as exc:
            logger.warning("skipping skill %s: %s", name, exc)
    return providers
