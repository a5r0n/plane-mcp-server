"""Relations between work items, and the workspace definitions that type them.

Two systems behind one tool: built-in relations (eight fixed types, on every
edition) and custom relations (workspace-defined, each with an outward and inward
label, paid only). `create` routes between them by which arguments are supplied.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.enums import WorkItemRelationTypeEnum
from plane.models.work_item_relation_definitions import (
    CreateWorkItemRelationDefinition,
    PaginatedWorkItemRelationDefinitionResponse,
    UpdateWorkItemRelationDefinition,
    WorkItemRelationDefinition,
)
from plane.models.work_items import (
    CreateWorkItemCustomRelation,
    CreateWorkItemRelation,
    RemoveWorkItemRelation,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import (
    Action,
    build_annotations,
    build_description,
    coerce_list,
    missing,
    needs,
    one_of,
    opt,
)

NAME = "workitem_relation"
TITLE = "Work item relations"

RELATION_TYPES: tuple[str, ...] = get_args(WorkItemRelationTypeEnum)

_OTHER_RELATIONS = (
    "For any other relationship pass relation_definition_id and "
    "relation_definition_label from the list_definitions action."
)

_NO_CUSTOM_RELATIONS = (
    "Error: Plane answered 404: custom relations are not on this edition (Community Edition "
    f"has none), or that id does not exist. Built-in relation_type values: {', '.join(RELATION_TYPES)}."
)

_NO_REMOVE = (
    "Error: Plane answered 404: Community Edition's API cannot remove relations, or no such "
    "relation exists. Remove it in the Plane web UI."
)

_NOT_RELATED = (
    "Plane accepts these without relating them when the pair already has a relation, of any "
    "type, or the id does not exist. Check with the list action."
)

ACTIONS = (
    Action("list", ("project_id", "workitem_id"), read=True),
    Action(
        "create",
        ("project_id", "workitem_id", "workitem_ids"),
        ("relation_type", "relation_definition_id", "relation_definition_label"),
        note="pass relation_type for a built-in relation, or definition id + label for a custom relation",
    ),
    Action(
        "delete",
        ("project_id", "workitem_id", "related_workitem_id"),
        ("is_dependency",),
        note="removes one relation; is_dependency=true for a built-in relation, false for a custom "
        "one (default false). Community Edition cannot remove through the API",
        destructive=True,
    ),
    Action("list_definitions", optional=("is_default", "is_active"), read=True),
    Action("create_definition", ("name",), ("outward", "inward", "is_active", "color")),
    Action("update_definition", ("definition_id",), ("name", "outward", "inward", "is_active", "color")),
    Action("delete_definition", ("definition_id",), destructive=True),
)

FOOTER = (
    f"relation_type takes a built-in type: {', '.join(RELATION_TYPES)}. A pair of work items "
    "holds one relation. For a custom relation (paid editions), call list_definitions and "
    "match the user's wording to an entry: its id goes in relation_definition_id and the "
    "matched outward or inward label in relation_definition_label, which sets direction."
)

LEGACY = {
    "list_work_item_relations": "list",
    "create_work_item_relation": "create",
    "remove_work_item_relation": "delete",
    "list_work_item_relation_definitions": "list_definitions",
    "create_work_item_relation_definition": "create_definition",
    "update_work_item_relation_definition": "update_definition",
    "delete_work_item_relation_definition": "delete_definition",
}


def _all_definitions(client, workspace_slug: str, is_default, is_active) -> list[WorkItemRelationDefinition]:
    """Definitions are a small set an agent must see whole, so page through them."""
    results: list[WorkItemRelationDefinition] = []
    cursor: str | None = None
    while True:
        page: PaginatedWorkItemRelationDefinitionResponse = client.work_item_relation_definitions.list(
            workspace_slug=workspace_slug,
            is_default=is_default,
            is_active=is_active,
            per_page=100,
            cursor=cursor,
        )
        results.extend(page.results)
        cursor = page.next_cursor
        if not page.next_page_results or not cursor:
            return results


def _created(created: Any, targets: list[str]) -> Any:
    """Plane answers 201 for targets it skipped, so name them rather than report success."""
    if not isinstance(created, list):  # the SDK types this None; only a list can show a skip
        return created
    related = {str(item.get("id")) for item in created}
    skipped = [target for target in targets if target not in related]
    if not skipped:
        return created
    if not related:
        return f"Error: no relation created for {', '.join(skipped)}. {_NOT_RELATED}"
    return {"created": created, "not_related": skipped, "note": _NOT_RELATED}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description(
            "Relations between work items, and the definitions that type them.", ACTIONS, FOOTER
        ),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def workitem_relation(
        action: Literal[
            "list",
            "create",
            "delete",
            "list_definitions",
            "create_definition",
            "update_definition",
            "delete_definition",
        ],
        project_id: str = "",
        workitem_id: str = "",
        workitem_ids: list[str] | None = None,
        related_workitem_id: str = "",
        relation_type: str = "",
        relation_definition_id: str = "",
        relation_definition_label: str = "",
        definition_id: str = "",
        name: str = "",
        outward: str = "",
        inward: str = "",
        color: str = "",
        # Tri-state: False is a real filter value, distinct from "no filter".
        is_default: bool | None = None,
        is_active: bool | None = None,
        is_dependency: bool = False,
    ) -> Any:
        client, workspace_slug = get_plane_client_context()

        try:
            if action == "list_definitions":
                return {
                    "built_in_dependencies": list(RELATION_TYPES),
                    "custom_definitions": [
                        d.model_dump() for d in _all_definitions(client, workspace_slug, is_default, is_active)
                    ],
                }

            if action == "create_definition":
                if not name:
                    return missing(action, "name")
                return client.work_item_relation_definitions.create(
                    workspace_slug=workspace_slug,
                    data=CreateWorkItemRelationDefinition(
                        name=name,
                        outward=opt(outward),
                        inward=opt(inward),
                        is_active=is_active,
                        color=opt(color),
                    ),
                )

            if action in ("update_definition", "delete_definition"):
                if not definition_id:
                    return missing(action, "definition_id")
                if action == "update_definition":
                    return client.work_item_relation_definitions.update(
                        workspace_slug=workspace_slug,
                        definition_id=definition_id,
                        data=UpdateWorkItemRelationDefinition(
                            name=opt(name),
                            outward=opt(outward),
                            inward=opt(inward),
                            is_active=is_active,
                            color=opt(color),
                        ),
                    )
                client.work_item_relation_definitions.delete(workspace_slug=workspace_slug, definition_id=definition_id)
                return None
        except HttpError as exc:
            if exc.status_code == 404:
                return _NO_CUSTOM_RELATIONS
            raise

        if error := needs(action, project_id=project_id, workitem_id=workitem_id):
            return error

        if action == "list":
            # Raw: relations.list validates entries as strings, and Plane sends objects.
            relations = client.work_items.relations._get(
                f"{workspace_slug}/projects/{project_id}/work-items/{workitem_id}/relations"
            )
            try:
                custom = client.work_items.custom_relations.list(
                    workspace_slug=workspace_slug, project_id=project_id, work_item_id=workitem_id
                )
            except HttpError as exc:
                if exc.status_code != 404:
                    raise
                custom = {}  # the work item exists, so this edition has no custom relations
            return {
                "dependencies": relations,
                "custom": {label: [item.model_dump() for item in items] for label, items in custom.items()},
            }

        if action == "create":
            targets = coerce_list(workitem_ids)
            if not targets:
                return missing(action, "workitem_ids")
            if relation_type:
                if error := one_of("relation_type", relation_type, RELATION_TYPES, _OTHER_RELATIONS):
                    return error
                created = client.work_items.relations.create(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    work_item_id=workitem_id,
                    data=CreateWorkItemRelation(
                        relation_type=relation_type,  # type: ignore[arg-type]
                        issues=targets,
                    ),
                )
                return _created(created, targets)
            if relation_definition_id and relation_definition_label:
                try:
                    return client.work_items.custom_relations.create(
                        workspace_slug=workspace_slug,
                        project_id=project_id,
                        work_item_id=workitem_id,
                        data=CreateWorkItemCustomRelation(
                            relation_definition_id=relation_definition_id,
                            relation_definition_type=relation_definition_label,
                            work_item_ids=targets,
                        ),
                    )
                except HttpError as exc:
                    if exc.status_code == 404:
                        return _NO_CUSTOM_RELATIONS
                    raise
            return (
                "Error: provide relation_type for a built-in relation, or both "
                "relation_definition_id and relation_definition_label for a custom relation. "
                "Call the list_definitions action to find one."
            )

        if not related_workitem_id:
            return missing(action, "related_workitem_id")
        try:
            if is_dependency:
                client.work_items.relations.delete(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    work_item_id=workitem_id,
                    data=RemoveWorkItemRelation(related_issue=related_workitem_id),
                )
            else:
                client.work_items.custom_relations.remove(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    work_item_id=workitem_id,
                    related_work_item_id=related_workitem_id,
                )
        except HttpError as exc:
            if exc.status_code == 404:
                return _NO_REMOVE
            raise
        return None
