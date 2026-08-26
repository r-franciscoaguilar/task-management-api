"""Assigning work, and the notification trail it leaves.

Two requirements meet here. Correctness: only a manager may assign, and only to
a worker, and only at a point in the lifecycle where it makes sense.
Traceability: whether the assignee was told must not be invisible -- including
when the telling fails.
"""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AssignmentEvent,
    NotificationStatus,
    StatusChangeEvent,
    Task,
    TaskStatus,
    User,
)

from .conftest import RecordingSender, auth


@pytest.fixture()
def task(users: dict[str, User], make_task: Callable[..., Task]) -> Task:
    return make_task(creator=users["manager"], title="Audit safety signage")


def _assign(
    client: TestClient, task: Task, *, by: User, to: User
) -> object:
    return client.post(
        f"/tasks/{task.id}/assign",
        json={"assignee_id": to.id},
        headers=auth(by),
    )


# --- the happy path -------------------------------------------------------


def test_manager_can_assign_to_a_worker(
    client: TestClient, users: dict[str, User], task: Task
) -> None:
    response = _assign(client, task, by=users["manager"], to=users["worker"])

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == TaskStatus.ASSIGNED.value
    assert body["assignee"]["id"] == users["worker"].id
    assert body["assigned_at"] is not None


def test_assignment_sends_an_email_to_the_assignee(
    client: TestClient, users: dict[str, User], task: Task, sender: RecordingSender
) -> None:
    _assign(client, task, by=users["manager"], to=users["worker"])

    assert len(sender.sent) == 1
    message = sender.sent[0]
    assert message.to == users["worker"].email
    assert task.title in message.subject
    assert users["manager"].name in message.body


def test_assignment_is_recorded(
    client: TestClient, users: dict[str, User], task: Task, db: Session
) -> None:
    _assign(client, task, by=users["manager"], to=users["worker"])

    events = list(db.scalars(select(AssignmentEvent)))
    assert len(events) == 1
    assert events[0].assigned_by_id == users["manager"].id
    assert events[0].assigned_to_id == users["worker"].id


def test_successful_delivery_is_recorded_as_sent(
    client: TestClient, users: dict[str, User], task: Task
) -> None:
    body = _assign(client, task, by=users["manager"], to=users["worker"]).json()

    assignment = body["latest_assignment"]
    assert assignment["notification_status"] == NotificationStatus.SENT.value
    assert assignment["notification_sent_at"] is not None
    assert assignment["notification_error"] is None


def test_assignment_records_a_status_change(
    client: TestClient, users: dict[str, User], task: Task, db: Session
) -> None:
    _assign(client, task, by=users["manager"], to=users["worker"])

    changes = list(db.scalars(select(StatusChangeEvent)))
    assert len(changes) == 1
    assert changes[0].from_status is TaskStatus.UNASSIGNED
    assert changes[0].to_status is TaskStatus.ASSIGNED
    assert changes[0].reason is None


# --- authorization --------------------------------------------------------


def test_worker_cannot_assign(
    client: TestClient, users: dict[str, User], task: Task, sender: RecordingSender
) -> None:
    response = _assign(client, task, by=users["worker"], to=users["other_worker"])

    assert response.status_code == 403
    assert response.json()["error"] == "manager_role_required"
    assert sender.sent == []


def test_assignment_requires_identity(client: TestClient, task: Task) -> None:
    response = client.post(f"/tasks/{task.id}/assign", json={"assignee_id": 1})
    assert response.status_code == 401


def test_any_manager_may_assign_any_task(
    client: TestClient, users: dict[str, User], make_task: Callable[..., Task]
) -> None:
    """Managers are peers with shared oversight, not siloed by who filed it."""
    filed_by_worker = make_task(creator=users["worker"], title="Worker filed this")
    response = _assign(
        client, filed_by_worker, by=users["manager"], to=users["other_worker"]
    )
    assert response.status_code == 200


# --- assignee validation --------------------------------------------------


def test_assigning_to_an_unknown_user_is_404(
    client: TestClient, users: dict[str, User], task: Task
) -> None:
    response = client.post(
        f"/tasks/{task.id}/assign",
        json={"assignee_id": 999999},
        headers=auth(users["manager"]),
    )
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_assigning_to_a_manager_is_rejected(
    client: TestClient, users: dict[str, User], task: Task, sender: RecordingSender
) -> None:
    """422, not 404: the user exists but is not a valid assignee."""
    response = _assign(client, task, by=users["manager"], to=users["manager"])

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_assignee"
    assert body["assignee_role"] == "MANAGER"
    assert sender.sent == []


def test_missing_assignee_id_is_a_validation_error(
    client: TestClient, users: dict[str, User], task: Task
) -> None:
    response = client.post(
        f"/tasks/{task.id}/assign", json={}, headers=auth(users["manager"])
    )
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


def test_assigning_a_task_that_does_not_exist_is_404(
    client: TestClient, users: dict[str, User]
) -> None:
    response = client.post(
        "/tasks/999999/assign",
        json={"assignee_id": users["worker"].id},
        headers=auth(users["manager"]),
    )
    assert response.status_code == 404


# --- reassignment ---------------------------------------------------------


def test_reassignment_moves_ownership_and_notifies_the_new_person(
    client: TestClient, users: dict[str, User], task: Task, sender: RecordingSender
) -> None:
    _assign(client, task, by=users["manager"], to=users["worker"])
    response = _assign(client, task, by=users["manager"], to=users["other_worker"])

    assert response.status_code == 200
    assert response.json()["assignee"]["id"] == users["other_worker"].id
    assert [message.to for message in sender.sent] == [
        users["worker"].email,
        users["other_worker"].email,
    ]


def test_reassignment_appends_history_rather_than_overwriting(
    client: TestClient, users: dict[str, User], task: Task, db: Session
) -> None:
    _assign(client, task, by=users["manager"], to=users["worker"])
    _assign(client, task, by=users["manager"], to=users["other_worker"])

    events = list(db.scalars(select(AssignmentEvent).order_by(AssignmentEvent.id)))
    assert [event.assigned_to_id for event in events] == [
        users["worker"].id,
        users["other_worker"].id,
    ]


def test_reassignment_records_no_status_change(
    client: TestClient, users: dict[str, User], task: Task, db: Session
) -> None:
    """The asymmetry that justifies two event tables: ownership moved, status
    did not, so only one of the two logs gains a row."""
    _assign(client, task, by=users["manager"], to=users["worker"])
    _assign(client, task, by=users["manager"], to=users["other_worker"])

    assert len(list(db.scalars(select(AssignmentEvent)))) == 2
    assert len(list(db.scalars(select(StatusChangeEvent)))) == 1


def test_reassigning_to_the_same_person_is_rejected(
    client: TestClient, users: dict[str, User], task: Task, sender: RecordingSender
) -> None:
    """Every assignment emails someone, so a double-submitted request must not
    mail the same person twice."""
    _assign(client, task, by=users["manager"], to=users["worker"])
    response = _assign(client, task, by=users["manager"], to=users["worker"])

    assert response.status_code == 409
    assert response.json()["error"] == "already_assigned"
    assert len(sender.sent) == 1


@pytest.mark.parametrize(
    "status", [TaskStatus.IN_PROGRESS, TaskStatus.DONE]
)
def test_work_underway_cannot_be_reassigned(
    client: TestClient,
    users: dict[str, User],
    make_task: Callable[..., Task],
    sender: RecordingSender,
    status: TaskStatus,
) -> None:
    started = make_task(
        creator=users["manager"],
        title="Already underway",
        assignee=users["worker"],
        status=status,
    )
    response = _assign(client, started, by=users["manager"], to=users["other_worker"])

    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "invalid_state_transition"
    assert body["current_status"] == status.value
    assert sender.sent == []


# --- when email delivery fails -------------------------------------------


def test_delivery_failure_does_not_undo_the_assignment(
    client: TestClient,
    users: dict[str, User],
    task: Task,
    sender: RecordingSender,
    db: Session,
) -> None:
    """Who is responsible for work must not depend on a mail server."""
    sender.fail()
    response = _assign(client, task, by=users["manager"], to=users["worker"])

    assert response.status_code == 200
    db.expire_all()
    stored = db.get(Task, task.id)
    assert stored is not None
    assert stored.status is TaskStatus.ASSIGNED
    assert stored.assignee_id == users["worker"].id


def test_delivery_failure_is_recorded_not_swallowed(
    client: TestClient, users: dict[str, User], task: Task, sender: RecordingSender
) -> None:
    sender.fail("SMTP connection refused")
    body = _assign(client, task, by=users["manager"], to=users["worker"]).json()

    assignment = body["latest_assignment"]
    assert assignment["notification_status"] == NotificationStatus.FAILED.value
    assert "SMTP connection refused" in assignment["notification_error"]
    assert assignment["notification_sent_at"] is None


def test_an_unexpected_sender_error_is_also_recorded(
    client: TestClient, users: dict[str, User], task: Task, sender: RecordingSender
) -> None:
    """A sender may fail in ways it never declared; the request still succeeded,
    so a 500 would be a lie."""
    sender.fail_with = RuntimeError("dns explosion")
    response = _assign(client, task, by=users["manager"], to=users["worker"])

    assert response.status_code == 200
    assignment = response.json()["latest_assignment"]
    assert assignment["notification_status"] == NotificationStatus.FAILED.value
    assert "dns explosion" in assignment["notification_error"]


def test_a_failed_notification_can_be_superseded_by_reassignment(
    client: TestClient, users: dict[str, User], task: Task, sender: RecordingSender
) -> None:
    """The failed record stays; it is history, not a mutable status field."""
    sender.fail()
    _assign(client, task, by=users["manager"], to=users["worker"])

    sender.fail_with = None
    _assign(client, task, by=users["manager"], to=users["other_worker"])

    history = client.get(
        f"/tasks/{task.id}/assignments", headers=auth(users["manager"])
    ).json()
    assert [event["notification_status"] for event in history] == ["SENT", "FAILED"]


# --- the history endpoint -------------------------------------------------


def test_history_is_newest_first(
    client: TestClient, users: dict[str, User], task: Task
) -> None:
    _assign(client, task, by=users["manager"], to=users["worker"])
    _assign(client, task, by=users["manager"], to=users["other_worker"])

    history = client.get(
        f"/tasks/{task.id}/assignments", headers=auth(users["manager"])
    ).json()
    assert [event["assigned_to"]["id"] for event in history] == [
        users["other_worker"].id,
        users["worker"].id,
    ]


def test_history_is_empty_for_an_unassigned_task(
    client: TestClient, users: dict[str, User], task: Task
) -> None:
    response = client.get(
        f"/tasks/{task.id}/assignments", headers=auth(users["manager"])
    )
    assert response.status_code == 200
    assert response.json() == []


def test_assignee_can_read_their_own_assignment_history(
    client: TestClient, users: dict[str, User], task: Task
) -> None:
    _assign(client, task, by=users["manager"], to=users["worker"])
    response = client.get(
        f"/tasks/{task.id}/assignments", headers=auth(users["worker"])
    )
    assert response.status_code == 200


def test_history_of_someone_elses_task_is_404(
    client: TestClient, users: dict[str, User], task: Task
) -> None:
    """Same visibility rule as the task itself: no seeing who else has work."""
    _assign(client, task, by=users["manager"], to=users["other_worker"])
    response = client.get(
        f"/tasks/{task.id}/assignments", headers=auth(users["worker"])
    )
    assert response.status_code == 404


def test_history_exposes_only_declared_fields(
    client: TestClient, users: dict[str, User], task: Task
) -> None:
    _assign(client, task, by=users["manager"], to=users["worker"])
    history = client.get(
        f"/tasks/{task.id}/assignments", headers=auth(users["manager"])
    ).json()

    assert set(history[0]) == {
        "id",
        "assigned_by",
        "assigned_to",
        "assigned_at",
        "notification_status",
        "notification_sent_at",
        "notification_error",
    }
