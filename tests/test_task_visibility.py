"""Who can see which tasks.

This is the "correctness" requirement from the brief: the right person can do
the right thing, the wrong person cannot. These are the tests most worth having.
"""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.models import Task, TaskStatus, User

from .conftest import auth


@pytest.fixture()
def scenario(
    users: dict[str, User], make_task: Callable[..., Task]
) -> dict[str, Task]:
    """One task for each visibility relationship a worker can have."""
    return {
        # Carol filed it, nobody is on it yet.
        "filed_by_worker": make_task(creator=users["worker"], title="Carol filed"),
        # Assigned to Carol, filed by the manager.
        "assigned_to_worker": make_task(
            creator=users["manager"],
            title="Carol assigned",
            assignee=users["worker"],
            status=TaskStatus.ASSIGNED,
        ),
        # Nothing to do with Carol at all.
        "unrelated": make_task(
            creator=users["manager"],
            title="Dave assigned",
            assignee=users["other_worker"],
            status=TaskStatus.ASSIGNED,
        ),
    }


# --- list scoping ---------------------------------------------------------


def test_manager_sees_every_task(
    client: TestClient, users: dict[str, User], scenario: dict[str, Task]
) -> None:
    body = client.get("/tasks", headers=auth(users["manager"])).json()
    assert body["total"] == 3
    assert {item["id"] for item in body["items"]} == {
        task.id for task in scenario.values()
    }


def test_worker_sees_only_own_and_self_filed_tasks(
    client: TestClient, users: dict[str, User], scenario: dict[str, Task]
) -> None:
    body = client.get("/tasks", headers=auth(users["worker"])).json()

    assert {item["id"] for item in body["items"]} == {
        scenario["filed_by_worker"].id,
        scenario["assigned_to_worker"].id,
    }
    assert body["total"] == 2


def test_worker_keeps_sight_of_tasks_they_filed(
    client: TestClient, users: dict[str, User], scenario: dict[str, Task]
) -> None:
    """A worker who reports a problem should not lose it before it is routed."""
    body = client.get("/tasks", headers=auth(users["worker"])).json()
    ids = {item["id"] for item in body["items"]}
    assert scenario["filed_by_worker"].id in ids


def test_worker_never_sees_another_workers_task(
    client: TestClient, users: dict[str, User], scenario: dict[str, Task]
) -> None:
    body = client.get("/tasks", headers=auth(users["worker"])).json()
    ids = {item["id"] for item in body["items"]}
    assert scenario["unrelated"].id not in ids


def test_total_reflects_the_scope_not_the_whole_table(
    client: TestClient, users: dict[str, User], scenario: dict[str, Task]
) -> None:
    """A leaky `total` would disclose how much work exists team-wide."""
    worker_total = client.get("/tasks", headers=auth(users["worker"])).json()["total"]
    manager_total = client.get("/tasks", headers=auth(users["manager"])).json()["total"]
    assert worker_total == 2
    assert manager_total == 3


# --- filters apply inside the scope --------------------------------------


def test_status_filter_applies(
    client: TestClient, users: dict[str, User], scenario: dict[str, Task]
) -> None:
    body = client.get(
        "/tasks", params={"status": "ASSIGNED"}, headers=auth(users["manager"])
    ).json()
    assert {item["status"] for item in body["items"]} == {"ASSIGNED"}
    assert body["total"] == 2


def test_manager_can_filter_by_assignee(
    client: TestClient, users: dict[str, User], scenario: dict[str, Task]
) -> None:
    body = client.get(
        "/tasks",
        params={"assignee_id": users["other_worker"].id},
        headers=auth(users["manager"]),
    ).json()
    assert {item["id"] for item in body["items"]} == {scenario["unrelated"].id}


def test_worker_filtering_by_another_assignee_gets_nothing(
    client: TestClient, users: dict[str, User], scenario: dict[str, Task]
) -> None:
    """Filters narrow within the scope; they cannot widen it.

    An empty page is the truthful answer -- "nothing you can see matches" --
    rather than silently returning the caller's own tasks.
    """
    body = client.get(
        "/tasks",
        params={"assignee_id": users["other_worker"].id},
        headers=auth(users["worker"]),
    ).json()
    assert body["items"] == []
    assert body["total"] == 0


def test_worker_can_filter_to_their_own_assigned_work(
    client: TestClient, users: dict[str, User], scenario: dict[str, Task]
) -> None:
    body = client.get(
        "/tasks",
        params={"assignee_id": users["worker"].id},
        headers=auth(users["worker"]),
    ).json()
    assert {item["id"] for item in body["items"]} == {
        scenario["assigned_to_worker"].id
    }


# --- detail visibility ---------------------------------------------------


def test_manager_can_read_any_task(
    client: TestClient, users: dict[str, User], scenario: dict[str, Task]
) -> None:
    response = client.get(
        f"/tasks/{scenario['unrelated'].id}", headers=auth(users["manager"])
    )
    assert response.status_code == 200


def test_assignee_can_read_their_task(
    client: TestClient, users: dict[str, User], scenario: dict[str, Task]
) -> None:
    response = client.get(
        f"/tasks/{scenario['assigned_to_worker'].id}", headers=auth(users["worker"])
    )
    assert response.status_code == 200


def test_creator_can_read_their_own_filing(
    client: TestClient, users: dict[str, User], scenario: dict[str, Task]
) -> None:
    response = client.get(
        f"/tasks/{scenario['filed_by_worker'].id}", headers=auth(users["worker"])
    )
    assert response.status_code == 200


def test_other_peoples_tasks_are_404_not_403(
    client: TestClient, users: dict[str, User], scenario: dict[str, Task]
) -> None:
    """403 would confirm the task exists, which leaks information."""
    response = client.get(
        f"/tasks/{scenario['unrelated'].id}", headers=auth(users["worker"])
    )
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_hidden_and_missing_tasks_are_indistinguishable(
    client: TestClient, users: dict[str, User], scenario: dict[str, Task]
) -> None:
    """No oracle: the response for someone else's task must be byte-identical
    to the response for a task that was never created."""
    hidden = client.get(
        f"/tasks/{scenario['unrelated'].id}", headers=auth(users["worker"])
    )
    missing = client.get("/tasks/999999", headers=auth(users["worker"]))

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"] == missing.json()["error"]
    # The id appears in both messages, so only the id differs -- not the wording.
    assert hidden.json()["message"].replace(
        str(scenario["unrelated"].id), "X"
    ) == missing.json()["message"].replace("999999", "X")


def test_detail_requires_identity(
    client: TestClient, scenario: dict[str, Task]
) -> None:
    response = client.get(f"/tasks/{scenario['unrelated'].id}")
    assert response.status_code == 401


def test_list_requires_identity(client: TestClient) -> None:
    assert client.get("/tasks").status_code == 401


def test_non_numeric_task_id_is_a_validation_error(
    client: TestClient, users: dict[str, User]
) -> None:
    response = client.get("/tasks/not-a-number", headers=auth(users["worker"]))
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"
