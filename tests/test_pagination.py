"""Paging behaviour, including that it never widens a caller's scope."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.models import Task, TaskStatus, User
from app.schemas.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

from .conftest import auth


@pytest.fixture()
def many_tasks(
    users: dict[str, User], make_task: Callable[..., Task]
) -> list[Task]:
    """25 tasks, so the default page size is exceeded.

    Alternating assignees means paging can be checked against a worker's
    narrower scope as well as a manager's full view.
    """
    return [
        make_task(
            creator=users["manager"],
            title=f"Task {index:02d}",
            assignee=users["worker"] if index % 2 == 0 else users["other_worker"],
            status=TaskStatus.ASSIGNED,
        )
        for index in range(25)
    ]


def test_default_page_size_is_applied(
    client: TestClient, users: dict[str, User], many_tasks: list[Task]
) -> None:
    body = client.get("/tasks", headers=auth(users["manager"])).json()
    assert len(body["items"]) == DEFAULT_PAGE_SIZE
    assert body["total"] == 25
    assert body["limit"] == DEFAULT_PAGE_SIZE
    assert body["offset"] == 0


def test_envelope_reports_the_requested_window(
    client: TestClient, users: dict[str, User], many_tasks: list[Task]
) -> None:
    body = client.get(
        "/tasks", params={"limit": 5, "offset": 10}, headers=auth(users["manager"])
    ).json()
    assert (body["limit"], body["offset"], len(body["items"])) == (5, 10, 5)
    assert body["total"] == 25


def test_paging_covers_everything_exactly_once(
    client: TestClient, users: dict[str, User], many_tasks: list[Task]
) -> None:
    """The real risk with offset paging is duplicated or skipped rows when the
    sort key ties, which is why ordering breaks ties on id."""
    seen: list[int] = []
    offset = 0
    while True:
        body = client.get(
            "/tasks",
            params={"limit": 7, "offset": offset},
            headers=auth(users["manager"]),
        ).json()
        if not body["items"]:
            break
        seen.extend(item["id"] for item in body["items"])
        offset += 7

    assert len(seen) == len(set(seen)) == 25
    assert set(seen) == {task.id for task in many_tasks}


def test_results_are_newest_first(
    client: TestClient, users: dict[str, User], many_tasks: list[Task]
) -> None:
    body = client.get(
        "/tasks", params={"limit": MAX_PAGE_SIZE}, headers=auth(users["manager"])
    ).json()
    timestamps = [item["created_at"] for item in body["items"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_offset_past_the_end_is_an_empty_page_not_an_error(
    client: TestClient, users: dict[str, User], many_tasks: list[Task]
) -> None:
    body = client.get(
        "/tasks", params={"offset": 500}, headers=auth(users["manager"])
    ).json()
    assert body["items"] == []
    assert body["total"] == 25


def test_paging_respects_worker_scope(
    client: TestClient, users: dict[str, User], many_tasks: list[Task]
) -> None:
    """Paging must not become a way to walk past the visibility rules."""
    seen: list[int] = []
    offset = 0
    while True:
        body = client.get(
            "/tasks",
            params={"limit": 4, "offset": offset},
            headers=auth(users["worker"]),
        ).json()
        if not body["items"]:
            break
        seen.extend(item["id"] for item in body["items"])
        offset += 4

    expected = {
        task.id for task in many_tasks if task.assignee_id == users["worker"].id
    }
    assert set(seen) == expected
    assert len(seen) == len(expected) == 13


@pytest.mark.parametrize("limit", [0, -1, MAX_PAGE_SIZE + 1])
def test_out_of_range_limits_are_rejected(
    client: TestClient, users: dict[str, User], limit: int
) -> None:
    """An unbounded limit would be a denial-of-service lever."""
    response = client.get(
        "/tasks", params={"limit": limit}, headers=auth(users["manager"])
    )
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


def test_negative_offset_is_rejected(
    client: TestClient, users: dict[str, User]
) -> None:
    response = client.get(
        "/tasks", params={"offset": -1}, headers=auth(users["manager"])
    )
    assert response.status_code == 422
