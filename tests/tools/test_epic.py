"""`epic` against the epic routes, and against a Plane that has none."""

from __future__ import annotations

import pytest
from plane.errors.errors import HttpError

from plane_mcp.toolkit import missing
from plane_mcp.tools import epic

PROJECT = "proj-1"
EPIC = "epic-1"
ITEM = "wi-1"
NOT_FOUND = HttpError("Not found", status_code=404)


def _call(registered, **arguments):
    return registered["epic"].fn(project_id=PROJECT, **arguments)


def test_create_reaches_the_sdk(registered, spy):
    _call(registered, action="create", name="E", priority="high")

    call = spy.recorder.only()
    assert call.method == "epics.create"
    assert call.kwargs["data"].name == "E"
    assert call.kwargs["data"].priority == "high"


def test_create_has_no_fields_the_server_drops():
    create = next(a for a in epic.ACTIONS if a.name == "create")
    assert not {"state_id", "parent_id", "assignee_ids", "label_ids"} & set(create.requires + create.optional)


def test_create_refuses_an_unknown_priority_without_calling_plane(registered, spy):
    result = _call(registered, action="create", name="E", priority="critical")

    assert result.startswith("Error: priority must be one of:")
    assert spy.recorder.calls == []


def test_add_children_requires_ids(registered, spy):
    result = _call(registered, action="add_children", epic_id=EPIC)

    assert result == missing("add_children", "workitem_ids")
    assert spy.recorder.calls == []


def test_add_children_maps_the_field_name(registered, spy):
    _call(registered, action="add_children", epic_id=EPIC, workitem_ids=[ITEM])

    assert spy.recorder.only().kwargs["data"].work_item_ids == [ITEM]


def test_add_children_accepts_ids_sent_as_a_string(registered, spy):
    _call(registered, action="add_children", epic_id=EPIC, workitem_ids="wi-1,wi-2")

    assert spy.recorder.only().kwargs["data"].work_item_ids == ["wi-1", "wi-2"]


@pytest.mark.parametrize("action,method", [("list", "list"), ("children", "list_issues")])
def test_paged_actions_pass_the_cursor_through(registered, spy, action, method):
    _call(registered, action=action, epic_id=EPIC, cursor="c-1", per_page=5)

    params = spy.recorder.only().kwargs["params"]
    assert (params.cursor, params.per_page) == ("c-1", 5)


@pytest.mark.parametrize("action,kwargs", [("list", {}), ("create", {"name": "E"})])
def test_stock_plane_says_epics_are_unavailable(registered, spy, action, kwargs):
    spy.returns["epics." + action] = NOT_FOUND

    assert "aren't available" in str(_call(registered, action=action, **kwargs))


@pytest.mark.parametrize(
    "action,method,kwargs",
    [
        ("retrieve", "retrieve", {}),
        ("children", "list_issues", {}),
        ("add_children", "add_issues", {"workitem_ids": [ITEM]}),
    ],
)
def test_a_404_by_id_names_both_causes(registered, spy, action, method, kwargs):
    spy.returns[f"epics.{method}"] = NOT_FOUND

    text = str(_call(registered, action=action, epic_id=EPIC, **kwargs))

    assert "not an epic" in text and "aren't available" in text


def test_other_errors_are_not_reworded(registered, spy):
    spy.returns["epics.list"] = HttpError("Server error", status_code=500)

    with pytest.raises(HttpError):
        _call(registered, action="list")
