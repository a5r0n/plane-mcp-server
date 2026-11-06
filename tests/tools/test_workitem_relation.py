"""`workitem_relation` against Community Edition's answers, which differ from the SDK's types.

The fixtures copy CE's `/relations/` responses. `relations.list` is typed `list[str]`
and CE sends objects, so a spy that returned the SDK's model would pass while the
real call failed on the first item that has a relation.
"""

from __future__ import annotations

import pytest
from plane.errors.errors import HttpError

from plane_mcp.tools.workitem_relation import RELATION_TYPES

ITEM = {"project_id": "proj-1", "workitem_id": "wi-1"}
CE_LIST = {
    "blocking": [],
    "blocked_by": [{"project_id": "proj-1", "issue_id": "wi-2"}],
    "duplicate": [],
    "relates_to": [],
    "start_after": [],
    "start_before": [],
    "finish_after": [],
    "finish_before": [],
}
RAW_GET = "work_items.relations._get"
CREATE = "work_items.relations.create"
CUSTOM_LIST = "work_items.custom_relations.list"
NOT_FOUND = HttpError("Not found", status_code=404)


def _call(registered, **arguments):
    return registered["workitem_relation"].fn(**arguments)


def test_all_eight_types_are_offered():
    assert set(RELATION_TYPES) == {
        "blocking",
        "blocked_by",
        "duplicate",
        "relates_to",
        "start_before",
        "start_after",
        "finish_before",
        "finish_after",
    }


@pytest.mark.parametrize("relation_type", RELATION_TYPES)
def test_create_posts_to_relations_with_the_shape_plane_reads(registered, spy, relation_type):
    spy.returns[CREATE] = [{"id": "wi-2", "relation_type": relation_type}]

    result = _call(registered, action="create", workitem_ids=["wi-2"], relation_type=relation_type, **ITEM)

    assert result == [{"id": "wi-2", "relation_type": relation_type}]
    call = spy.recorder.only()
    assert call.method == CREATE
    assert call.kwargs["data"].model_dump() == {"relation_type": relation_type, "issues": ["wi-2"]}


def test_create_refuses_an_unknown_type_without_calling_plane(registered, spy):
    result = _call(registered, action="create", workitem_ids=["wi-2"], relation_type="parent_of", **ITEM)

    assert result.startswith("Error: relation_type must be one of:")
    assert "relates_to" in result
    assert spy.recorder.calls == []


def test_a_create_plane_skipped_is_an_error_not_a_success(registered, spy):
    spy.returns[CREATE] = []  # CE's 201 when the pair already has another relation type

    result = _call(registered, action="create", workitem_ids=["wi-2"], relation_type="blocking", **ITEM)

    assert result.startswith("Error: no relation created for wi-2.")


def test_a_partly_skipped_create_names_what_was_left_out(registered, spy):
    created = [{"id": "wi-2", "relation_type": "blocked_by"}]
    spy.returns[CREATE] = created

    result = _call(registered, action="create", workitem_ids=["wi-2", "wi-3"], relation_type="blocked_by", **ITEM)

    assert result["created"] == created
    assert result["not_related"] == ["wi-3"]


def test_a_create_with_no_body_is_not_read_as_skipped(registered, spy):
    spy.returns[CREATE] = None

    assert _call(registered, action="create", workitem_ids=["wi-2"], relation_type="blocking", **ITEM) is None


def test_list_keeps_the_objects_plane_sends(registered, spy):
    spy.returns[RAW_GET] = CE_LIST
    spy.returns[CUSTOM_LIST] = NOT_FOUND

    result = _call(registered, action="list", **ITEM)

    assert result == {"dependencies": CE_LIST, "custom": {}}
    assert spy.recorder.calls[0].kwargs["endpoint"] == "acme/projects/proj-1/work-items/wi-1/relations"


def test_list_does_not_read_an_auth_failure_as_no_custom_relations(registered, spy):
    spy.returns[RAW_GET] = CE_LIST
    spy.returns[CUSTOM_LIST] = HttpError("Unauthorized", status_code=401)

    with pytest.raises(HttpError):
        _call(registered, action="list", **ITEM)


def test_list_still_reports_a_missing_work_item(registered, spy):
    spy.returns[RAW_GET] = NOT_FOUND

    with pytest.raises(HttpError):
        _call(registered, action="list", **ITEM)


@pytest.mark.parametrize(
    ("method", "arguments"),
    [
        ("work_item_relation_definitions.list", {"action": "list_definitions"}),
        ("work_item_relation_definitions.create", {"action": "create_definition", "name": "Tracks"}),
        (
            "work_item_relation_definitions.update",
            {"action": "update_definition", "definition_id": "def-1", "name": "Tracks"},
        ),
        ("work_item_relation_definitions.delete", {"action": "delete_definition", "definition_id": "def-1"}),
        (
            "work_items.custom_relations.create",
            {
                "action": "create",
                "workitem_ids": ["wi-2"],
                "relation_definition_id": "def-1",
                "relation_definition_label": "tracks",
                **ITEM,
            },
        ),
    ],
)
def test_custom_relations_fail_cleanly_where_the_edition_has_none(registered, spy, method, arguments):
    spy.returns[method] = NOT_FOUND

    result = _call(registered, **arguments)

    assert result.startswith("Error: Plane answered 404: custom relations are not on this edition")
    assert "relates_to" in result


@pytest.mark.parametrize(
    ("method", "is_dependency"),
    [("work_items.relations.delete", True), ("work_items.custom_relations.remove", False)],
)
def test_delete_points_at_the_ui_where_the_api_cannot_remove(registered, spy, method, is_dependency):
    spy.returns[method] = NOT_FOUND

    result = _call(registered, action="delete", related_workitem_id="wi-2", is_dependency=is_dependency, **ITEM)

    assert spy.recorder.only().method == method
    assert result.startswith("Error:") and "web UI" in result


def test_delete_sends_the_field_plane_reads(registered, spy):
    _call(registered, action="delete", related_workitem_id="wi-2", is_dependency=True, **ITEM)

    assert spy.recorder.only().kwargs["data"].model_dump() == {"related_issue": "wi-2"}


def test_a_server_error_is_not_read_as_a_missing_feature(registered, spy):
    spy.returns["work_item_relation_definitions.list"] = HttpError("Boom", status_code=500)

    with pytest.raises(HttpError):
        _call(registered, action="list_definitions")
