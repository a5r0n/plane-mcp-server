"""Steps: a work item's ordered path, on the steps routes of Plane builds that have them.

The SDK has no steps resource, so this calls the v1 routes through the work items
resource's raw helpers. Stock Community Edition has no such routes.
"""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import FastMCP
from plane.errors.errors import HttpError

from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import Action, build_annotations, build_description, coerce_list, missing, needs, one_of

NAME = "workitem_step"
TITLE = "Work item steps"

STATUSES = ("pending", "done", "failed", "skipped")
FINISHED = ("done", "failed", "skipped")
ENTRY_KEYS = ("id", "title", "detail", "actor", "status", "note", "not_before")

_UNAVAILABLE = "this Plane has no step routes (stock Community Edition)"
_NOT_FOUND = f"Error: Plane answered 404: no such work item or step in this project, or {_UNAVAILABLE}."
_NO_STEPS = f"Error: steps aren't available: {_UNAVAILABLE}."

ACTIONS = (
    Action("list", ("project_id", "workitem_id"), read=True, note="the path, and its current step"),
    Action(
        "set_path",
        ("project_id", "workitem_id", "steps"),
        note="replaces the whole path: an entry with id updates that step, one without adds one, "
        "and steps left out are deleted",
        destructive=True,
    ),
    Action(
        "add",
        ("project_id", "workitem_id", "title"),
        ("detail", "actor", "status", "note", "not_before", "position"),
        note="position is 0-based; the end by default",
    ),
    Action(
        "update",
        ("project_id", "workitem_id", "step_id"),
        ("title", "detail", "actor", "status", "note", "not_before"),
    ),
    Action(
        "finish",
        ("project_id", "workitem_id", "step_id"),
        ("status", "note"),
        note="status done by default, or failed or skipped",
    ),
    Action("reorder", ("project_id", "workitem_id", "step_ids"), note="every step of the item, in the new order"),
    Action("delete", ("project_id", "workitem_id", "step_id"), destructive=True),
)

FOOTER = (
    f"status: {', '.join(STATUSES)}. The current step is the first pending or failed one. actor is a "
    "workspace member's user id; not_before is an ISO datetime; null clears either on update. A steps "
    f"entry takes {', '.join(ENTRY_KEYS)}. Steps never change the item's state or assignees."
)

LEGACY: dict[str, str] = {}


def _entries(steps: Any) -> list[dict[str, Any]] | str:
    """The set_path entries, or an error naming what's wrong with them."""
    entries = coerce_list(steps, split=False)
    if entries is None:
        return missing("set_path", "steps")
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            return f"Error: steps[{index}] must be an object with keys from: {', '.join(ENTRY_KEYS)}."
        unknown = sorted(set(entry) - set(ENTRY_KEYS))
        if unknown:
            return f"Error: steps[{index}] has unknown keys {', '.join(unknown)}; allowed: {', '.join(ENTRY_KEYS)}."
        if error := one_of(f"steps[{index}].status", entry.get("status"), STATUSES):
            return error
    return entries


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("A work item's ordered path of steps.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def workitem_step(
        action: Literal["list", "set_path", "add", "update", "finish", "reorder", "delete"],
        project_id: str = "",
        workitem_id: str = "",
        step_id: str = "",
        title: str = "",
        detail: str = "",
        actor: str | None = "",
        status: str = "",
        note: str = "",
        not_before: str | None = "",
        position: int | None = None,
        steps: list[dict[str, Any]] | None = None,
        step_ids: list[str] | None = None,
    ) -> Any:
        if error := needs(action, project_id=project_id, workitem_id=workitem_id):
            return error
        if action in ("update", "finish", "delete") and not step_id:
            return missing(action, "step_id")
        allowed = FINISHED if action == "finish" else STATUSES
        if error := one_of("status", status, allowed):
            return error

        client, workspace_slug = get_plane_client_context()
        raw = client.work_items
        item = f"{workspace_slug}/projects/{project_id}/work-items/{workitem_id}"
        path = f"{item}/steps"

        # "" leaves a field alone; None (null) clears actor or not_before
        body: dict[str, Any] = {key: value for key, value in (("title", title), ("detail", detail)) if value}
        body.update({key: value for key, value in (("actor", actor), ("not_before", not_before)) if value != ""})
        if status:
            body["status"] = status
        if note:
            body["note"] = note

        try:
            if action == "list":
                found = raw._get(item, params={"fields": "steps,current_step"})
                if not isinstance(found, dict) or "steps" not in found:
                    return _NO_STEPS
                return {"steps": found["steps"], "current_step": found.get("current_step")}

            if action == "set_path":
                entries = _entries(steps)
                if isinstance(entries, str):
                    return entries
                return raw._post(f"{path}/set-path", {"steps": entries})

            if action == "add":
                if not title:
                    return missing(action, "title")
                if position is not None:
                    body["position"] = position
                return raw._post(path, body)

            if action == "update":
                if not body:
                    return "Error: action 'update' needs at least one field to change."
                return raw._patch(f"{path}/{step_id}", body)

            if action == "finish":
                return raw._patch(f"{path}/{step_id}", {**body, "status": status or "done"})

            if action == "reorder":
                order = coerce_list(step_ids)
                if not order:
                    return missing(action, "step_ids")
                return raw._post(f"{path}/reorder", {"step_ids": order})

            raw._delete(f"{path}/{step_id}")
            return None
        except HttpError as exc:
            if exc.status_code == 404:
                return _NOT_FOUND
            raise
