"""`workitem_step` against the fork's steps routes, and against a Plane that has none."""

from __future__ import annotations

import pytest
from plane.errors.errors import HttpError

ITEM = {"project_id": "proj-1", "workitem_id": "wi-1"}
BASE = "acme/projects/proj-1/work-items/wi-1"
STEPS = f"{BASE}/steps"
NOT_FOUND = HttpError("Not found", status_code=404)
PLAN = {"id": "s-1", "title": "Plan", "status": "pending"}


def _call(registered, **arguments):
    return registered["workitem_step"].fn(**ITEM, **arguments)


def test_list_reads_steps_and_current_step_from_the_item(registered, spy):
    spy.returns["work_items._get"] = {"id": "wi-1", "steps": [PLAN], "current_step": PLAN}

    result = _call(registered, action="list")

    assert result == {"steps": [PLAN], "current_step": PLAN}
    call = spy.recorder.only()
    assert (call.method, call.kwargs["endpoint"]) == ("work_items._get", BASE)
    assert call.kwargs["params"] == {"fields": "steps,current_step"}


def test_list_on_stock_plane_says_steps_are_unavailable(registered, spy):
    # stock CE ignores the unknown fields and answers the item without them
    spy.returns["work_items._get"] = {"id": "wi-1"}

    assert "steps aren't available" in _call(registered, action="list")


def test_add_posts_only_the_fields_given(registered, spy):
    _call(registered, action="add", title="Build", actor="u-1", position=0)

    call = spy.recorder.only()
    assert (call.method, call.kwargs["endpoint"]) == ("work_items._post", STEPS)
    assert call.kwargs["data"] == {"title": "Build", "actor": "u-1", "position": 0}


def test_add_requires_a_title(registered, spy):
    assert _call(registered, action="add") == "Error: action 'add' requires: title."
    assert spy.recorder.calls == []


def test_update_sends_null_to_clear_actor_and_not_before(registered, spy):
    _call(registered, action="update", step_id="s-1", actor=None, not_before=None)

    call = spy.recorder.only()
    assert (call.method, call.kwargs["endpoint"]) == ("work_items._patch", f"{STEPS}/s-1")
    assert call.kwargs["data"] == {"actor": None, "not_before": None}


def test_update_with_nothing_to_change_is_refused(registered, spy):
    assert _call(registered, action="update", step_id="s-1").startswith("Error:")
    assert spy.recorder.calls == []


def test_finish_marks_the_step_done_by_default(registered, spy):
    _call(registered, action="finish", step_id="s-1", note="merged")

    call = spy.recorder.only()
    assert (call.method, call.kwargs["endpoint"]) == ("work_items._patch", f"{STEPS}/s-1")
    assert call.kwargs["data"] == {"status": "done", "note": "merged"}


def test_finish_refuses_pending(registered, spy):
    result = _call(registered, action="finish", step_id="s-1", status="pending")

    assert result == "Error: status must be one of: done, failed, skipped."
    assert spy.recorder.calls == []


def test_an_unknown_status_is_refused_before_plane(registered, spy):
    assert _call(registered, action="add", title="X", status="blocked").startswith("Error: status must be one of:")
    assert spy.recorder.calls == []


def test_set_path_posts_the_entries_in_order(registered, spy):
    entries = [{"id": "s-1", "status": "done"}, {"title": "Review", "actor": "u-2"}]

    _call(registered, action="set_path", steps=entries)

    call = spy.recorder.only()
    assert (call.method, call.kwargs["endpoint"]) == ("work_items._post", f"{STEPS}/set-path")
    assert call.kwargs["data"] == {"steps": entries}


def test_set_path_accepts_steps_sent_as_a_json_string(registered, spy):
    _call(registered, action="set_path", steps='[{"title": "Plan"}, {"title": "Build"}]')

    assert spy.recorder.only().kwargs["data"] == {"steps": [{"title": "Plan"}, {"title": "Build"}]}


def test_set_path_accepts_an_empty_path(registered, spy):
    _call(registered, action="set_path", steps=[])

    assert spy.recorder.only().kwargs["data"] == {"steps": []}


@pytest.mark.parametrize(
    "steps,expected",
    [
        ([{"name": "Plan"}], "unknown keys name"),
        (["Plan"], "must be an object"),
        ([{"title": "Plan", "status": "blocked"}], "steps[0].status must be one of"),
    ],
)
def test_set_path_refuses_a_bad_entry_before_plane(registered, spy, steps, expected):
    assert expected in _call(registered, action="set_path", steps=steps)
    assert spy.recorder.calls == []


def test_reorder_posts_every_step_id(registered, spy):
    _call(registered, action="reorder", step_ids="s-2,s-1")

    call = spy.recorder.only()
    assert (call.method, call.kwargs["endpoint"]) == ("work_items._post", f"{STEPS}/reorder")
    assert call.kwargs["data"] == {"step_ids": ["s-2", "s-1"]}


def test_delete_removes_the_step(registered, spy):
    assert _call(registered, action="delete", step_id="s-1") is None

    call = spy.recorder.only()
    assert (call.method, call.kwargs["endpoint"]) == ("work_items._delete", f"{STEPS}/s-1")


@pytest.mark.parametrize(
    "method,arguments",
    [
        ("_get", {"action": "list"}),
        ("_post", {"action": "add", "title": "X"}),
        ("_patch", {"action": "finish", "step_id": "s-1"}),
        ("_delete", {"action": "delete", "step_id": "s-1"}),
    ],
)
def test_a_404_names_both_causes(registered, spy, method, arguments):
    spy.returns[f"work_items.{method}"] = NOT_FOUND

    text = _call(registered, **arguments)

    assert "no such work item or step" in text and "no step routes" in text


def test_other_errors_are_not_reworded(registered, spy):
    spy.returns["work_items._post"] = HttpError("Bad request", status_code=400)

    with pytest.raises(HttpError):
        _call(registered, action="add", title="X")
