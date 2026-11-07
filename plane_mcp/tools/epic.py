"""Epics: work items of the project's epic type, and the work items under them.

Only the fields Plane's v1 API keeps are offered. The SDK's create model also
sends state_id, parent_id, assignee_ids and label_ids, which the server drops
silently, so those changes go through `workitem update` instead.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.enums import PriorityEnum
from plane.models.epics import AddEpicWorkItems, CreateEpic
from plane.models.query_params import PaginatedQueryParams

from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import (
    Action,
    as_params,
    build_annotations,
    build_description,
    coerce_list,
    envelope,
    missing,
    needs,
    one_of,
    opt,
)

NAME = "epic"
TITLE = "Epics"

PRIORITIES = get_args(PriorityEnum)

_UNAVAILABLE = "epics aren't available on this Plane: its API has no epic routes (stock Community Edition)"
_NO_EPICS = f"Error: Plane answered 404: {_UNAVAILABLE}."
_NOT_AN_EPIC = f"Error: Plane answered 404: that id is not an epic in this project, or {_UNAVAILABLE}."

ACTIONS = (
    Action("list", ("project_id",), ("cursor", "per_page"), read=True),
    Action("create", ("project_id", "name"), ("description_html", "priority", "start_date", "target_date")),
    Action("retrieve", ("project_id", "epic_id"), read=True),
    Action("children", ("project_id", "epic_id"), ("cursor", "per_page"), read=True),
    Action("add_children", ("project_id", "epic_id", "workitem_ids"), note="sets each work item's parent to the epic"),
)

FOOTER = (
    f"priority: {', '.join(PRIORITIES)}. An epic is a work item: its children are the work items "
    "whose parent is the epic, so `workitem update` with parent also adds one, and state, labels, "
    "assignees and nesting of the epic itself are changed with `workitem update` by its id."
)

LEGACY: dict[str, str] = {}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Epics and the work items under them.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def epic(
        action: Literal["list", "create", "retrieve", "children", "add_children"],
        project_id: str = "",
        epic_id: str = "",
        workitem_ids: list[str] | None = None,
        name: str = "",
        description_html: str = "",
        priority: str = "",
        start_date: str = "",
        target_date: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> Any:
        if not project_id:
            return missing(action, "project_id")
        client, workspace_slug = get_plane_client_context()
        scope = {"workspace_slug": workspace_slug, "project_id": project_id}

        if action in ("list", "create"):
            try:
                if action == "list":
                    params = as_params(PaginatedQueryParams, cursor=cursor, per_page=per_page)
                    return envelope(client.epics.list(**scope, params=params))
                if not name:
                    return missing(action, "name")
                if error := one_of("priority", priority, PRIORITIES):
                    return error
                data = CreateEpic(
                    name=name,
                    description_html=opt(description_html),
                    priority=opt(priority),
                    start_date=opt(start_date),
                    target_date=opt(target_date),
                )
                return client.epics.create(**scope, data=data)
            except HttpError as exc:
                if exc.status_code == 404:
                    return _NO_EPICS
                raise

        if error := needs(action, epic_id=epic_id):
            return error
        targets = coerce_list(workitem_ids)
        if action == "add_children" and not targets:
            return missing(action, "workitem_ids")

        try:
            if action == "retrieve":
                return client.epics.retrieve(**scope, epic_id=epic_id)
            if action == "children":
                params = as_params(PaginatedQueryParams, cursor=cursor, per_page=per_page)
                return envelope(client.epics.list_issues(**scope, epic_id=epic_id, params=params))
            return client.epics.add_issues(**scope, epic_id=epic_id, data=AddEpicWorkItems(work_item_ids=targets))
        except HttpError as exc:
            if exc.status_code == 404:
                return _NOT_AN_EPIC
            raise
