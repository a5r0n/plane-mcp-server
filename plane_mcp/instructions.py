"""Server instructions, sent to a client before it lists the tools.

They point at the tool for each topic, so `test_references.py` follows every
backticked name and fails if one stops resolving.
"""

from __future__ import annotations

SERVER_INSTRUCTIONS = """
## Epics

Use the `epic` tool. An epic's children are work items with parent set to the epic.
"""


def server_instructions() -> str:
    """The instructions, with the skills line only when this server serves skills."""
    from plane_mcp.skills import INSTRUCTIONS, skills_dir

    return SERVER_INSTRUCTIONS + INSTRUCTIONS if skills_dir() is not None else SERVER_INSTRUCTIONS
