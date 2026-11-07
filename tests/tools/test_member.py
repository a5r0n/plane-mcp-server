"""`member add_project` against Plane's v1 endpoint, which the SDK does not wrap."""

from __future__ import annotations

import pytest
from plane.errors.errors import HttpError

RAW_POST = "projects._post"
ADD = {"action": "add_project", "project_id": "proj-1", "user_id": "user-1"}


def test_add_posts_member_and_role_to_the_v1_members_endpoint(registered, spy):
    result = registered["member"].fn(role=15, **ADD)

    assert result is None
    call = spy.recorder.only()
    assert call.method == RAW_POST
    assert call.kwargs["endpoint"] == "acme/projects/proj-1/members"
    assert call.kwargs["data"] == {"member": "user-1", "role": 15}


def test_add_without_a_role_is_refused_rather_than_made_a_guest(registered, spy):
    result = registered["member"].fn(**ADD)

    assert result == "Error: action 'add_project' requires: role."
    assert not spy.recorder.calls


def test_a_non_admin_gets_plane_403_unchanged(registered, spy):
    forbidden = HttpError("You don't have the required permissions.", status_code=403)
    spy.returns[RAW_POST] = forbidden

    with pytest.raises(HttpError) as excinfo:
        registered["member"].fn(role=20, **ADD)

    assert excinfo.value is forbidden
