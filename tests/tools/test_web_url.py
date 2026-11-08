"""workitem results carry `web_url`, built from config and the item, never guessed."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from plane.errors.errors import HttpError
from plane.models.work_items import WorkItem, WorkItemSearch

from plane_mcp.toolkit import weburl

PROJECT = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    weburl._identifiers.clear()
    monkeypatch.delenv("PLANE_WEB_URL", raising=False)
    monkeypatch.setenv("PLANE_BASE_URL", "https://plane.example.test")
    yield
    weburl._identifiers.clear()


def _item(**overrides):
    return WorkItem(**{"id": "w", "name": "x", "sequence_id": 7, "project": PROJECT, **overrides})


def _page(*items):
    return SimpleNamespace(
        results=list(items),
        total_count=len(items),
        count=len(items),
        next_cursor="",
        prev_cursor="",
        next_page_results=False,
        prev_page_results=False,
    )


def _with_project(spy, identifier="ENG"):
    spy.returns["projects.retrieve"] = SimpleNamespace(identifier=identifier)


EXPECTED = "https://plane.example.test/acme/browse/ENG-7/"


@pytest.mark.parametrize(
    ("args", "method"),
    [
        ({"action": "retrieve", "project_id": PROJECT, "workitem_id": "w"}, "work_items.retrieve"),
        ({"action": "retrieve_by_identifier", "workitem_identifier": "ENG-7"}, "work_items.retrieve_by_identifier"),
        ({"action": "create", "project_id": PROJECT, "name": "x"}, "work_items.create"),
        ({"action": "update", "project_id": PROJECT, "workitem_id": "w", "name": "x"}, "work_items.update"),
    ],
)
def test_single_item_actions_return_web_url(registered, spy, args, method):
    spy.returns[method] = _item()
    _with_project(spy)
    result = registered["workitem"].fn(**args)
    assert result.web_url == EXPECTED
    assert result.model_dump()["web_url"] == EXPECTED


@pytest.mark.parametrize(
    ("args", "method"),
    [
        ({"action": "list", "project_id": PROJECT}, "work_items.list"),
        ({"action": "list"}, "work_items.list_workspace"),
        ({"action": "list_archived", "project_id": PROJECT}, "work_items.list_archived"),
    ],
)
def test_list_actions_return_web_url_with_one_lookup_per_project(registered, spy, args, method):
    spy.returns[method] = _page(_item(), _item(sequence_id=8))
    _with_project(spy)
    page = registered["workitem"].fn(**args)
    assert [r["web_url"] for r in page["results"]] == [EXPECTED, EXPECTED.replace("-7", "-8")]
    assert spy.recorder.methods.count("projects.retrieve") == 1


def test_search_uses_the_identifier_it_already_carries(registered, spy):
    spy.returns["work_items.search"] = WorkItemSearch(
        issues=[
            {
                "id": "w",
                "name": "x",
                "sequence_id": 3,
                "project__identifier": "OPS",
                "project_id": PROJECT,
                "workspace__slug": "acme",
            }
        ]
    )
    result = registered["workitem"].fn(action="search", query="x")
    assert result.issues[0].web_url == "https://plane.example.test/acme/browse/OPS-3/"
    assert "projects.retrieve" not in spy.recorder.methods


def test_expanded_project_needs_no_lookup(registered, spy):
    spy.returns["work_items.retrieve"] = _item(project={"id": PROJECT, "identifier": "ENG"})
    result = registered["workitem"].fn(action="retrieve", project_id=PROJECT, workitem_id="w")
    assert result.web_url == EXPECTED
    assert "projects.retrieve" not in spy.recorder.methods


@pytest.mark.parametrize("missing", ["sequence_id", "project"])
def test_missing_field_omits_web_url(registered, spy, missing):
    spy.returns["work_items.retrieve"] = _item(**{missing: None})
    _with_project(spy)
    result = registered["workitem"].fn(action="retrieve", project_id=PROJECT, workitem_id="w")
    assert "web_url" not in result.model_dump()


def test_sparse_fields_without_its_inputs_omit_web_url(registered, spy):
    spy.returns["work_items.list"] = _page(WorkItem(id="w", name="x"))
    _with_project(spy)
    page = registered["workitem"].fn(action="list", project_id=PROJECT, fields="id,name")
    assert page["results"] == [{"id": "w", "name": "x"}]


def _sent_fields(spy, method):
    call = next(c for c in spy.recorder.calls if c.method == method)
    return set(call.kwargs["params"].fields.split(","))


@pytest.mark.parametrize(
    ("args", "method"),
    [
        ({"action": "list", "project_id": PROJECT}, "work_items.list"),
        ({"action": "list"}, "work_items.list_workspace"),
        ({"action": "list_archived", "project_id": PROJECT}, "work_items.list_archived"),
    ],
)
def test_sparse_fields_naming_web_url_build_it_and_return_only_what_was_asked(registered, spy, args, method):
    """Plane drops a field name it does not know, so `fields=id,web_url` used to answer nulls."""
    spy.returns[method] = _page(_item())
    _with_project(spy)
    page = registered["workitem"].fn(**args, fields="id,web_url")
    assert page["results"] == [{"id": "w", "web_url": EXPECTED}]
    assert _sent_fields(spy, method) == {"id", "project", "sequence_id"}


@pytest.mark.parametrize(
    ("args", "method"),
    [
        ({"action": "retrieve", "project_id": PROJECT, "workitem_id": "w"}, "work_items.retrieve"),
        ({"action": "retrieve_by_identifier", "workitem_identifier": "ENG-7"}, "work_items.retrieve_by_identifier"),
    ],
)
def test_sparse_fields_naming_web_url_on_a_single_item(registered, spy, args, method):
    spy.returns[method] = _item()
    _with_project(spy)
    result = registered["workitem"].fn(**args, fields="id,name,web_url")
    assert result == {"id": "w", "name": "x", "web_url": EXPECTED}
    assert _sent_fields(spy, method) == {"id", "name", "project", "sequence_id"}


def test_sparse_fields_without_web_url_are_sent_as_asked(registered, spy):
    spy.returns["work_items.retrieve"] = _item()
    _with_project(spy)
    result = registered["workitem"].fn(action="retrieve", project_id=PROJECT, workitem_id="w", fields="id,name")
    assert result == {"id": "w", "name": "x"}
    assert _sent_fields(spy, "work_items.retrieve") == {"id", "name"}


def test_failed_lookup_omits_web_url_and_still_answers(registered, spy):
    spy.returns["work_items.retrieve"] = _item()
    spy.returns["projects.retrieve"] = HttpError("forbidden", status_code=403)
    result = registered["workitem"].fn(action="retrieve", project_id=PROJECT, workitem_id="w")
    assert result.sequence_id == 7
    assert "web_url" not in result.model_dump()


def test_failed_lookup_is_tried_once_per_list(registered, spy):
    spy.returns["work_items.list"] = _page(_item(), _item(sequence_id=8), _item(sequence_id=9))
    spy.returns["projects.retrieve"] = HttpError("forbidden", status_code=403)
    page = registered["workitem"].fn(action="list", project_id=PROJECT)
    assert all("web_url" not in r for r in page["results"])
    assert spy.recorder.methods.count("projects.retrieve") == 1


def test_failed_lookup_is_retried_on_the_next_call(registered, spy):
    spy.returns["work_items.retrieve"] = _item()
    spy.returns["projects.retrieve"] = HttpError("forbidden", status_code=403)
    registered["workitem"].fn(action="retrieve", project_id=PROJECT, workitem_id="w")
    _with_project(spy)
    result = registered["workitem"].fn(action="retrieve", project_id=PROJECT, workitem_id="w")
    assert result.web_url == EXPECTED


def test_missing_workspace_slug_omits_web_url():
    assert weburl.build_web_url("", "ENG", 7) is None


def test_identifier_is_cached_across_calls(registered, spy):
    spy.returns["work_items.retrieve"] = _item()
    _with_project(spy)
    for _ in range(3):
        registered["workitem"].fn(action="retrieve", project_id=PROJECT, workitem_id="w")
    assert spy.recorder.methods.count("projects.retrieve") == 1


def test_web_url_override_wins(monkeypatch):
    monkeypatch.setenv("PLANE_WEB_URL", "https://web.example.test/")
    assert weburl.web_base() == "https://web.example.test"


def test_internal_url_is_never_the_web_base(monkeypatch):
    monkeypatch.setenv("PLANE_INTERNAL_BASE_URL", "http://plane-api.internal:8000")
    assert weburl.web_base() == "https://plane.example.test"


@pytest.mark.parametrize("base", [None, "https://api.plane.so", "https://api.plane.so/"])
def test_cloud_api_host_maps_to_the_web_app(monkeypatch, base):
    if base is None:
        monkeypatch.delenv("PLANE_BASE_URL")
    else:
        monkeypatch.setenv("PLANE_BASE_URL", base)
    assert weburl.web_base() == "https://app.plane.so"


# --- pages ---------------------------------------------------------------------

PAGE = "22222222-2222-2222-2222-222222222222"
PAGE_URL = f"https://plane.example.test/acme/projects/{PROJECT}/pages/{PAGE}/"


def _plane_page(**overrides):
    from plane.models.pages import Page

    return Page.model_validate({"id": PAGE, "name": "Spec", **overrides})


@pytest.mark.parametrize(
    ("args", "method"),
    [
        ({"action": "retrieve", "project_id": PROJECT, "page_id": PAGE}, "pages.retrieve_project_page"),
        (
            {"action": "create", "project_id": PROJECT, "name": "Spec", "description_html": "<p/>"},
            "pages.create_project_page",
        ),
        ({"action": "update", "project_id": PROJECT, "page_id": PAGE, "name": "Spec"}, "pages.update_project_page"),
    ],
)
def test_project_page_actions_return_web_url(registered, spy, args, method):
    spy.returns[method] = _plane_page()
    result = registered["page"].fn(**args)
    assert result.model_dump()["web_url"] == PAGE_URL


def test_project_page_list_returns_web_url(registered, spy):
    spy.returns["pages.list_project_pages"] = _page(_plane_page())
    page = registered["page"].fn(action="list", project_id=PROJECT)
    assert [r["web_url"] for r in page["results"]] == [PAGE_URL]


def test_workspace_pages_get_no_web_url(registered, spy):
    spy.returns["pages.retrieve_workspace_page"] = _plane_page()
    result = registered["page"].fn(action="retrieve", page_id=PAGE)
    assert "web_url" not in result.model_dump()


def test_page_web_url_honours_the_web_url_override(monkeypatch):
    monkeypatch.setenv("PLANE_WEB_URL", "https://web.example.test")
    assert weburl.build_page_web_url("acme", PROJECT, PAGE) == (
        f"https://web.example.test/acme/projects/{PROJECT}/pages/{PAGE}/"
    )
