"""Custom fields: a project's property definitions, on the properties routes of Plane builds that have them.

The SDK has no such resource, so this calls the v1 routes through the work items
resource's raw helpers. Values are read and written on `workitem` as `custom_fields`.
Stock Community Edition has no such routes.
"""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import FastMCP
from plane.errors.errors import HttpError

from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import Action, build_annotations, build_description, coerce_list, missing, needs, one_of

NAME = "custom_field"
TITLE = "Custom fields"

TYPES = ("text", "number", "boolean", "date", "datetime", "choice", "member", "url")
WRITE_ROLES = (20, 15, 5)
EDITABLE = ("name", "description", "options", "multiple", "visible", "write_role", "sort_order")

_UNAVAILABLE = "this Plane has no custom fields (stock Community Edition)"
_NOT_FOUND = f"Error: Plane answered 404: no such project or custom field, or {_UNAVAILABLE}."

ACTIONS = (
    Action("list", ("project_id",), read=True),
    Action("retrieve", ("project_id", "field_id"), read=True),
    Action(
        "create",
        ("project_id", "name", "key", "property_type"),
        ("description", "options", "multiple", "visible", "write_role", "sort_order"),
    ),
    Action(
        "update",
        ("project_id", "field_id"),
        EDITABLE,
        note="key and property_type never change",
    ),
    Action("delete", ("project_id", "field_id"), destructive=True, note="its values stop showing"),
)

FOOTER = (
    f"property_type: {', '.join(TYPES)}. key: ^[a-z][a-z0-9_]{{0,63}}$, never reused. options (choice "
    "only): [{label, id?, disabled?}]; disable an option items hold, don't remove it. multiple: choice "
    "and member. write_role: 20 admin, 15 member (default), 5 guest. Project admins only.\n"
    "Values go in workitem custom_fields: choice takes option ids, member a user id."
)

LEGACY: dict[str, str] = {}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description(
            "A project's custom field definitions: the keys of workitem custom_fields.", ACTIONS, FOOTER
        ),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def custom_field(
        action: Literal["list", "retrieve", "create", "update", "delete"],
        project_id: str = "",
        field_id: str = "",
        name: str = "",
        key: str = "",
        property_type: str = "",
        description: str = "",
        options: list[dict[str, Any]] | None = None,
        multiple: bool | None = None,
        visible: bool | None = None,
        write_role: int = 0,
        sort_order: float | None = None,
    ) -> Any:
        if error := needs(action, project_id=project_id):
            return error
        if action in ("retrieve", "update", "delete") and not field_id:
            return missing(action, "field_id")
        if error := one_of("property_type", property_type, TYPES):
            return error
        if write_role and write_role not in WRITE_ROLES:
            return "Error: write_role must be one of: 20 (admin), 15 (member), 5 (guest)."

        body: dict[str, Any] = {
            field: value
            for field, value in (("name", name), ("description", description), ("write_role", write_role))
            if value
        }
        body.update(
            {
                field: value
                for field, value in (("multiple", multiple), ("visible", visible), ("sort_order", sort_order))
                if value is not None
            }
        )
        if options is not None:
            entries = coerce_list(options, split=False)
            if entries is None or not all(isinstance(entry, dict) for entry in entries):
                return "Error: options must be a list of objects with label, and optionally id and disabled."
            body["options"] = entries

        client, workspace_slug = get_plane_client_context()
        raw = client.work_items
        path = f"{workspace_slug}/projects/{project_id}/properties"

        try:
            if action == "list":
                return raw._get(path)

            if action == "retrieve":
                return raw._get(f"{path}/{field_id}")

            if action == "create":
                if error := needs(action, name=name, key=key, property_type=property_type):
                    return error
                return raw._post(path, {**body, "key": key, "type": property_type})

            if action == "update":
                if not body:
                    return f"Error: action 'update' needs at least one of: {', '.join(EDITABLE)}."
                return raw._patch(f"{path}/{field_id}", body)

            raw._delete(f"{path}/{field_id}")
            return None
        except HttpError as exc:
            if exc.status_code == 404:
                return _NOT_FOUND
            raise
