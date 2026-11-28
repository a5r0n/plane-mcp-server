"""`workitem_activity list_workspace` against the fork's workspace feed, and against a Plane without one."""

from __future__ import annotations

import pytest
from plane.errors.errors import HttpError

from plane_mcp.middleware import ReadOnly

SINCE = "2026-11-28T15:00:00Z"
FEED = "acme/activities"
ROW = {
    "id": "a-1",
    "field": "state",
    "issue_identifier": "ENG-42",
    "provenance": {"channel": "api", "token": None, "session": "s-1", "agent": "pilot"},
}


def _call(registered, **arguments):
    return registered["workitem_activity"].fn(**arguments)


def test_list_workspace_reads_the_feed_with_since_only(registered, spy):
    spy.returns["work_items._get"] = {"more": False, "results": [ROW]}

    result = _call(registered, action="list_workspace", since=SINCE)

    assert result == {"more": False, "results": [ROW]}
    call = spy.recorder.only()
    assert (call.method, call.kwargs["endpoint"]) == ("work_items._get", FEED)
    assert call.kwargs["params"] == {"since": SINCE}


def test_list_workspace_sends_filters_under_the_feed_names(registered, spy):
    _call(
        registered,
        action="list_workspace",
        since=SINCE,
        project_id="p-1,p-2",
        field="state,comment",
        agent="pilot",
        per_page=50,
    )

    assert spy.recorder.only().kwargs["params"] == {
        "since": SINCE,
        "project": "p-1,p-2",
        "field": "state,comment",
        "agent": "pilot",
        "per_page": 50,
    }


def test_list_workspace_requires_since(registered, spy):
    assert _call(registered, action="list_workspace") == "Error: action 'list_workspace' requires: since."
    assert spy.recorder.calls == []


def test_list_workspace_on_stock_plane_says_the_feed_is_unavailable(registered, spy):
    spy.returns["work_items._get"] = HttpError("Not found", status_code=404)

    assert "no workspace activity feed" in _call(registered, action="list_workspace", since=SINCE)


def test_a_feed_rejection_keeps_its_reason(registered, spy):
    reason = {"code": "INVALID_PARAM", "error": "since must be an ISO 8601 datetime."}
    spy.returns["work_items._get"] = HttpError("HTTP 400: Bad Request", 400, reason)

    with pytest.raises(HttpError, match="since must be an ISO 8601 datetime"):
        _call(registered, action="list_workspace", since="yesterday")


@pytest.mark.parametrize("action", ["list", "retrieve"])
def test_item_actions_still_require_both_ids(registered, spy, action):
    assert _call(registered, action=action, project_id="p-1").endswith("requires: workitem_id.")
    assert spy.recorder.calls == []


def test_the_read_only_server_allows_list_workspace():
    assert ReadOnly().rejection("workitem_activity", {"action": "list_workspace"}) is None
