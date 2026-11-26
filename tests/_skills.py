"""A deployed skill set on disk, laid out as a deploy writes it."""

from __future__ import annotations


def make_skill_set(root, sha: str, skills: dict[str, str]):
    """A deployed skill set: `<root>/<sha>/<name>/SKILL.md` and `VERSION`, with `current` pointing at it."""
    folder = root / sha[:12]
    for name, description in skills.items():
        (folder / name).mkdir(parents=True)
        (folder / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n")
    (folder / "VERSION").write_text(sha + "\n")
    link = root / "current.tmp"
    link.symlink_to(folder.name)
    link.rename(root / "current")
    return folder
