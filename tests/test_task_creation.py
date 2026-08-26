"""Creating tasks."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Task, TaskStatus, User

from .conftest import auth


def test_worker_can_create_a_task(
    client: TestClient, users: dict[str, User], db: Session
) -> None:
    """Creation is deliberately not manager-only."""
    response = client.post(
        "/tasks",
        json={"title": "Loading bay door sticks", "description": "Night shift report"},
        headers=auth(users["worker"]),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Loading bay door sticks"
    assert body["creator"]["id"] == users["worker"].id
    assert db.get(Task, body["id"]) is not None


def test_manager_can_create_a_task(
    client: TestClient, users: dict[str, User]
) -> None:
    response = client.post(
        "/tasks", json={"title": "Replace filter"}, headers=auth(users["manager"])
    )
    assert response.status_code == 201
    assert response.json()["creator"]["id"] == users["manager"].id


def test_new_task_starts_unassigned_with_no_timestamps(
    client: TestClient, users: dict[str, User]
) -> None:
    """A caller cannot create and hand off in one step: assigning is separate,
    because it must be notified and audited."""
    body = client.post(
        "/tasks", json={"title": "Replace filter"}, headers=auth(users["manager"])
    ).json()

    assert body["status"] == TaskStatus.UNASSIGNED.value
    assert body["assignee"] is None
    assert body["assigned_at"] is None
    assert body["completed_at"] is None


def test_assignee_cannot_be_set_at_creation(
    client: TestClient, users: dict[str, User]
) -> None:
    """Unknown fields are ignored rather than honoured."""
    body = client.post(
        "/tasks",
        json={"title": "Sneaky", "assignee_id": users["worker"].id, "status": "DONE"},
        headers=auth(users["manager"]),
    ).json()

    assert body["assignee"] is None
    assert body["status"] == TaskStatus.UNASSIGNED.value


def test_description_is_optional(client: TestClient, users: dict[str, User]) -> None:
    body = client.post(
        "/tasks", json={"title": "No detail"}, headers=auth(users["manager"])
    ).json()
    assert body["description"] is None


def test_blank_description_is_stored_as_absent(
    client: TestClient, users: dict[str, User]
) -> None:
    body = client.post(
        "/tasks",
        json={"title": "Whitespace detail", "description": "   "},
        headers=auth(users["manager"]),
    ).json()
    assert body["description"] is None


def test_title_is_trimmed(client: TestClient, users: dict[str, User]) -> None:
    body = client.post(
        "/tasks", json={"title": "  Padded  "}, headers=auth(users["manager"])
    ).json()
    assert body["title"] == "Padded"


def test_missing_title_is_rejected(
    client: TestClient, users: dict[str, User]
) -> None:
    response = client.post("/tasks", json={}, headers=auth(users["manager"]))
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


def test_blank_title_is_rejected(client: TestClient, users: dict[str, User]) -> None:
    """A length check alone would let whitespace through."""
    response = client.post(
        "/tasks", json={"title": "   "}, headers=auth(users["manager"])
    )
    assert response.status_code == 422


def test_overlong_title_is_rejected(
    client: TestClient, users: dict[str, User]
) -> None:
    response = client.post(
        "/tasks", json={"title": "x" * 201}, headers=auth(users["manager"])
    )
    assert response.status_code == 422


def test_creation_requires_identity(client: TestClient) -> None:
    response = client.post("/tasks", json={"title": "Anonymous"})
    assert response.status_code == 401


def test_response_exposes_only_declared_fields(
    client: TestClient, users: dict[str, User]
) -> None:
    body = client.post(
        "/tasks", json={"title": "Shape check"}, headers=auth(users["manager"])
    ).json()

    assert set(body) == {
        "id",
        "title",
        "description",
        "status",
        "creator",
        "assignee",
        "created_at",
        "updated_at",
        "assigned_at",
        "completed_at",
    }


def test_timestamps_are_utc_qualified(
    client: TestClient, users: dict[str, User]
) -> None:
    """SQLite drops timezones; UtcDateTime puts them back."""
    body = client.post(
        "/tasks", json={"title": "Time check"}, headers=auth(users["manager"])
    ).json()
    assert body["created_at"].endswith("Z") or "+00:00" in body["created_at"]
