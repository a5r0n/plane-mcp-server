"""`custom_field` definitions and `workitem` custom_fields values, against the fork's routes."""

from __future__ import annotations

import pytest
from plane.errors.errors import HttpError

from plane_mcp.pql_reference import PQL_FIELD_DESCRIPTION, PQL_FULL_REFERENCE

PROJECT = "11111111-1111-1111-1111-111111111111"
PROPERTIES = f"acme/projects/{PROJECT}/properties"
ITEMS = f"acme/projects/{PROJECT}/work-items"
RISK = [{"label": "High"}, {"label": "Low", "id": "low"}]


def _field(registered, **arguments):
    return registered["custom_field"].fn(project_id=PROJECT, **arguments)


def _item(registered, **arguments):
    return registered["workitem"].fn(project_id=PROJECT, **arguments)


# custom_field


@pytest.mark.parametrize(
    ("arguments", "method", "endpoint"),
    [
        ({"action": "list"}, "work_items._get", PROPERTIES),
        ({"action": "retrieve", "field_id": "f-1"}, "work_items._get", f"{PROPERTIES}/f-1"),
    ],
)
def test_reads_hit_the_properties_routes(registered, spy, arguments, method, endpoint):
    spy.returns[method] = [{"id": "f-1"}]

    assert _field(registered, **arguments) == [{"id": "f-1"}]
    call = spy.recorder.only()
    assert (call.method, call.kwargs["endpoint"]) == (method, endpoint)


def test_create_sends_type_and_key_with_the_fields_given(registered, spy):
    _field(registered, action="create", name="Risk", key="risk", property_type="choice", options=RISK, multiple=False)

    call = spy.recorder.only()
    assert (call.method, call.kwargs["endpoint"]) == ("work_items._post", PROPERTIES)
    assert call.kwargs["data"] == {"name": "Risk", "key": "risk", "type": "choice", "options": RISK, "multiple": False}


def test_create_needs_name_key_and_type(registered, spy):
    assert _field(registered, action="create", name="Risk").startswith("Error: action 'create' requires:")
    assert spy.recorder.calls == []


def test_options_given_as_a_json_string_are_repaired(registered, spy):
    _field(registered, action="update", field_id="f-1", options='[{"label": "High"}]')

    assert spy.recorder.only().kwargs["data"] == {"options": [{"label": "High"}]}


def test_options_that_are_not_objects_are_refused(registered, spy):
    assert _field(registered, action="update", field_id="f-1", options=["High"]).startswith("Error: options")
    assert spy.recorder.calls == []


def test_update_sends_only_what_changes(registered, spy):
    _field(registered, action="update", field_id="f-1", name="Risk level", visible=False, write_role=20)

    call = spy.recorder.only()
    assert (call.method, call.kwargs["endpoint"]) == ("work_items._patch", f"{PROPERTIES}/f-1")
    assert call.kwargs["data"] == {"name": "Risk level", "visible": False, "write_role": 20}


def test_update_with_nothing_to_change_is_refused(registered, spy):
    assert _field(registered, action="update", field_id="f-1").startswith("Error: action 'update' needs")
    assert spy.recorder.calls == []


def test_delete_answers_nothing(registered, spy):
    assert _field(registered, action="delete", field_id="f-1") is None
    call = spy.recorder.only()
    assert (call.method, call.kwargs["endpoint"]) == ("work_items._delete", f"{PROPERTIES}/f-1")


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"action": "retrieve"}, "Error: action 'retrieve' requires: field_id."),
        ({"action": "create", "name": "R", "key": "r", "property_type": "select"}, "Error: property_type must be"),
        ({"action": "update", "field_id": "f-1", "write_role": 10}, "Error: write_role must be one of"),
    ],
)
def test_bad_arguments_are_refused_before_plane(registered, spy, arguments, error):
    assert _field(registered, **arguments).startswith(error)
    assert spy.recorder.calls == []


def test_a_404_says_the_routes_may_be_missing(registered, spy):
    spy.returns["work_items._get"] = HttpError("Not found", status_code=404)

    assert "stock Community Edition" in _field(registered, action="list")


def test_other_errors_propagate(registered, spy):
    spy.returns["work_items._post"] = HttpError("Forbidden", status_code=403)

    with pytest.raises(HttpError):
        _field(registered, action="create", name="Risk", key="risk", property_type="text")


# workitem custom_fields


def test_create_with_custom_fields_posts_them_raw(registered, spy):
    spy.returns["work_items._post"] = {"id": "w", "name": "x", "custom_fields": {"risk": "high"}}

    result = _item(registered, action="create", name="x", custom_fields={"risk": "high", "notes": None})

    call = spy.recorder.calls[0]
    assert (call.method, call.kwargs["endpoint"]) == ("work_items._post", ITEMS)
    assert call.kwargs["data"] == {"name": "x", "custom_fields": {"risk": "high", "notes": None}}
    assert result.custom_fields == {"risk": "high"}


def test_update_with_custom_fields_patches_only_what_was_given(registered, spy):
    spy.returns["work_items._patch"] = {"id": "w", "name": "x"}

    _item(registered, action="update", workitem_id="w", priority="high", custom_fields={"risk": None})

    call = spy.recorder.calls[0]
    assert (call.method, call.kwargs["endpoint"]) == ("work_items._patch", f"{ITEMS}/w")
    assert call.kwargs["data"] == {"priority": "high", "custom_fields": {"risk": None}}


def test_writes_without_custom_fields_keep_the_sdk_path(registered, spy):
    _item(registered, action="update", workitem_id="w", priority="high")

    assert spy.recorder.methods[0] == "work_items.update"


def test_custom_fields_must_be_an_object(registered, spy):
    result = _item(registered, action="update", workitem_id="w", custom_fields=["risk"])

    assert result == "Error: custom_fields must be an object of property key to value."
    assert spy.recorder.calls == []


@pytest.mark.parametrize(("status", "saved"), [(400, "nothing was saved"), (409, "the work item was saved")])
def test_a_rejected_value_comes_back_correctable(registered, spy, status, saved):
    reasons = {"risk": "not one of: high, low"}
    spy.returns["work_items._patch"] = HttpError("Bad", status_code=status, response={"custom_fields": reasons})

    result = _item(registered, action="update", workitem_id="w", custom_fields={"risk": "medium"})

    assert saved in result["error"]
    assert result["custom_fields"] == reasons
    assert result["hint"]


def test_a_409_on_create_says_to_update_the_saved_item_not_create_again(registered, spy):
    reasons = {"risk": "option removed"}
    spy.returns["work_items._post"] = HttpError("Conflict", status_code=409, response={"custom_fields": reasons})

    result = _item(registered, action="create", name="x", custom_fields={"risk": "high"})

    assert "the work item was saved" in result["error"]
    assert "don't create it again" in result["hint"]
    assert "retry workitem update" in result["hint"]


def test_other_write_errors_propagate(registered, spy):
    spy.returns["work_items._patch"] = HttpError("Bad", status_code=400, response={"name": ["required"]})

    with pytest.raises(HttpError):
        _item(registered, action="update", workitem_id="w", custom_fields={"risk": "high"})


def test_a_sparse_read_keeps_custom_fields(registered, spy):
    from plane.models.work_items import WorkItem

    spy.returns["work_items.retrieve"] = WorkItem(id="w", name="x", custom_fields={"risk": "high"})

    result = _item(registered, action="retrieve", workitem_id="w", fields="id,custom_fields")

    assert result == {"id": "w", "custom_fields": {"risk": "high"}}


# pql


@pytest.mark.parametrize("reference", [PQL_FIELD_DESCRIPTION, PQL_FULL_REFERENCE])
def test_pql_reference_documents_prop_keys(reference):
    assert "prop.<key>" in reference
    assert "`custom_field list`" in reference
    assert "{custom_fields}" not in reference
