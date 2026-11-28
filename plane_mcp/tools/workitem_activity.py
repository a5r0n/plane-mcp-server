"""Change history for a work item, and across the workspace on Plane builds with an activity feed."""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.work_items import PaginatedWorkItemActivityResponse, WorkItemActivity

from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import Action, build_annotations, build_description, envelope, missing, needs, page_params

NAME = "workitem_activity"
TITLE = "Work item activity"

ACTIONS = (
    Action("list", ("project_id", "workitem_id"), ("cursor", "per_page"), read=True),
    Action("retrieve", ("project_id", "workitem_id", "activity_id"), read=True),
    Action(
        "list_workspace",
        ("since",),
        ("project_id", "field", "agent", "per_page"),
        read=True,
        note="rows across the workspace created at or after since",
    ),
)

FOOTER = (
    "list_workspace: oldest first, per_page up to 100; project_id, field and agent take comma lists. "
    "When more is true, call again with since set to the last row's created_at and skip ids already seen."
)

LEGACY = {
    "list_work_item_activities": "list",
    "retrieve_work_item_activity": "retrieve",
}

_NO_FEED = "Error: this Plane has no workspace activity feed (stock Community Edition)."


def _list_workspace(since: str, project_id: str, field: str, agent: str, per_page: int) -> Any:
    client, workspace_slug = get_plane_client_context()
    params = {"since": since, "project": project_id, "field": field, "agent": agent, "per_page": per_page}
    try:
        return client.work_items._get(f"{workspace_slug}/activities", params={k: v for k, v in params.items() if v})
    except HttpError as exc:
        if exc.status_code == 404:
            return _NO_FEED
        raise


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Change history for work items.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def workitem_activity(
        action: Literal["list", "retrieve", "list_workspace"],
        project_id: str = "",
        workitem_id: str = "",
        activity_id: str = "",
        cursor: str = "",
        per_page: int = 0,
        since: str = "",
        field: str = "",
        agent: str = "",
    ) -> WorkItemActivity | dict[str, Any] | str:
        if action == "list_workspace":
            if not since:
                return missing(action, "since")
            return _list_workspace(since, project_id, field, agent, per_page)

        client, workspace_slug = get_plane_client_context()

        if error := needs(action, project_id=project_id, workitem_id=workitem_id):
            return error

        if action == "list":
            response: PaginatedWorkItemActivityResponse = client.work_items.activities.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=workitem_id,
                params=page_params(cursor, per_page),
            )
            return envelope(response)

        if not activity_id:
            return missing(action, "activity_id")
        return client.work_items.activities.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=workitem_id,
            activity_id=activity_id,
        )
