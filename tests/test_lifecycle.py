"""The task lifecycle: start, complete, and the one audited way back.

The state machine is the core business invariant, so this covers both the
happy path and every move the machine must refuse.
"""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StatusChangeEvent, Task, TaskStatus, User

from .conftest import auth

FORWARD = [TaskStatus.UNASSIGNED, TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS, TaskStatus.DONE]


@pytest.fixture()
def assigned(users: dict[str, User], make_task: Callable[..., Task]) -> Task:
    return make_task(
        creator=users["manager"],
        title="Recalibrate the scale",
        assignee=users["worker"],
        status=TaskStatus.ASSIGNED,
    )


@pytest.fixture()
def in_progress(users: dict[str, User], make_task: Callable[..., Task]) -> Task:
    return make_task(
        creator=users["manager"],
        title="Reconcile invoices",
        assignee=users["worker"],
        status=TaskStatus.IN_PROGRESS,
    )


# --- forward progress -----------------------------------------------------


def test_assignee_can_start_assigned_work(
    client: TestClient, users: dict[str, User], assigned: Task
) -> None:
    response = client.post(
        f"/tasks/{assigned.id}/start", headers=auth(users["worker"])
    )
    assert response.status_code == 200
    assert response.json()["status"] == TaskStatus.IN_PROGRESS.value


def test_assignee_can_complete_work_in_progress(
    client: TestClient, users: dict[str, User], in_progress: Task
) -> None:
    response = client.post(
        f"/tasks/{in_progress.id}/complete", headers=auth(users["worker"])
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == TaskStatus.DONE.value
    assert body["completed_at"] is not None


def test_completed_at_is_only_set_on_completion(
    client: TestClient, users: dict[str, User], assigned: Task
) -> None:
    body = client.post(
        f"/tasks/{assigned.id}/start", headers=auth(users["worker"])
    ).json()
    assert body["completed_at"] is None


def test_the_whole_lifecycle_runs_end_to_end(
    client: TestClient, users: dict[str, User], make_task: Callable[..., Task]
) -> None:
    task = make_task(creator=users["worker"], title="Full journey")
    manager = auth(users["manager"])
    worker = auth(users["worker"])

    assert client.post(
        f"/tasks/{task.id}/assign",
        json={"assignee_id": users["worker"].id},
        headers=manager,
    ).json()["status"] == "ASSIGNED"
    assert client.post(f"/tasks/{task.id}/start", headers=worker).json()[
        "status"
    ] == "IN_PROGRESS"
    assert client.post(f"/tasks/{task.id}/complete", headers=worker).json()[
        "status"
    ] == "DONE"

    history = client.get(f"/tasks/{task.id}/history", headers=manager).json()
    assert [event["to_status"] for event in history] == [
        "DONE",
        "IN_PROGRESS",
        "ASSIGNED",
    ]


def test_every_transition_is_recorded(
    client: TestClient, users: dict[str, User], assigned: Task, db: Session
) -> None:
    client.post(f"/tasks/{assigned.id}/start", headers=auth(users["worker"]))

    changes = list(db.scalars(select(StatusChangeEvent)))
    assert len(changes) == 1
    assert changes[0].from_status is TaskStatus.ASSIGNED
    assert changes[0].to_status is TaskStatus.IN_PROGRESS
    assert changes[0].changed_by_id == users["worker"].id
    assert changes[0].reason is None


# --- moves the machine must refuse ---------------------------------------


@pytest.mark.parametrize("status", [TaskStatus.IN_PROGRESS, TaskStatus.DONE])
def test_start_is_refused_once_work_is_underway_or_finished(
    client: TestClient,
    users: dict[str, User],
    make_task: Callable[..., Task],
    status: TaskStatus,
) -> None:
    task = make_task(
        creator=users["manager"], assignee=users["worker"], status=status
    )
    response = client.post(f"/tasks/{task.id}/start", headers=auth(users["worker"]))

    assert response.status_code == 409
    assert response.json()["current_status"] == status.value


def test_start_is_refused_on_an_unassigned_task_the_worker_filed(
    client: TestClient, users: dict[str, User], make_task: Callable[..., Task]
) -> None:
    """403, not 409: nobody has been made responsible yet, so the assignee
    check bites before the state machine is even consulted."""
    task = make_task(creator=users["worker"], title="Filed but unrouted")
    response = client.post(f"/tasks/{task.id}/start", headers=auth(users["worker"]))

    assert response.status_code == 403
    assert response.json()["error"] == "not_assignee"


def test_start_is_404_on_an_unassigned_task_the_worker_cannot_see(
    client: TestClient, users: dict[str, User], make_task: Callable[..., Task]
) -> None:
    """Visibility is checked before anything else, so no existence is leaked."""
    task = make_task(creator=users["manager"], title="Not Carol's business")
    response = client.post(f"/tasks/{task.id}/start", headers=auth(users["worker"]))

    assert response.status_code == 404


@pytest.mark.parametrize("status", [TaskStatus.ASSIGNED, TaskStatus.DONE])
def test_complete_is_refused_unless_work_is_underway(
    client: TestClient,
    users: dict[str, User],
    make_task: Callable[..., Task],
    status: TaskStatus,
) -> None:
    task = make_task(
        creator=users["manager"], assignee=users["worker"], status=status
    )
    response = client.post(
        f"/tasks/{task.id}/complete", headers=auth(users["worker"])
    )

    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "invalid_state_transition"
    assert body["current_status"] == status.value


def test_done_is_terminal(
    client: TestClient, users: dict[str, User], make_task: Callable[..., Task]
) -> None:
    """Nothing moves a completed task, in either direction."""
    done = make_task(
        creator=users["manager"], assignee=users["worker"], status=TaskStatus.DONE
    )
    worker = auth(users["worker"])

    assert client.post(f"/tasks/{done.id}/start", headers=worker).status_code == 409
    assert client.post(f"/tasks/{done.id}/complete", headers=worker).status_code == 409
    assert client.post(
        f"/tasks/{done.id}/release", json={"reason": "changed my mind"}, headers=worker
    ).status_code == 409


def test_transition_errors_report_the_current_status(
    client: TestClient, users: dict[str, User], assigned: Task
) -> None:
    """The context field a retrying client needs to reconcile its own view."""
    body = client.post(
        f"/tasks/{assigned.id}/complete", headers=auth(users["worker"])
    ).json()
    assert body["current_status"] == TaskStatus.ASSIGNED.value


def test_refused_transitions_leave_no_history(
    client: TestClient, users: dict[str, User], assigned: Task, db: Session
) -> None:
    client.post(f"/tasks/{assigned.id}/complete", headers=auth(users["worker"]))
    assert list(db.scalars(select(StatusChangeEvent))) == []


# --- who may act ----------------------------------------------------------


def test_manager_cannot_start_work(
    client: TestClient, users: dict[str, User], assigned: Task
) -> None:
    """Managers must not do someone else's work on their behalf."""
    response = client.post(
        f"/tasks/{assigned.id}/start", headers=auth(users["manager"])
    )
    assert response.status_code == 403
    assert response.json()["error"] == "worker_role_required"


def test_manager_cannot_complete_work(
    client: TestClient, users: dict[str, User], in_progress: Task
) -> None:
    response = client.post(
        f"/tasks/{in_progress.id}/complete", headers=auth(users["manager"])
    )
    assert response.status_code == 403


def test_another_worker_cannot_act_on_work_they_cannot_see(
    client: TestClient, users: dict[str, User], assigned: Task
) -> None:
    """404, not 403: they must not learn the task exists."""
    response = client.post(
        f"/tasks/{assigned.id}/start", headers=auth(users["other_worker"])
    )
    assert response.status_code == 404


def test_the_creator_cannot_progress_work_assigned_to_someone_else(
    client: TestClient, users: dict[str, User], make_task: Callable[..., Task]
) -> None:
    """403 here, not 404: the creator can see the task, so denying its
    existence would be nonsense. They simply are not the one doing the work."""
    task = make_task(
        creator=users["other_worker"],
        title="Filed by Dave, assigned to Carol",
        assignee=users["worker"],
        status=TaskStatus.ASSIGNED,
    )
    response = client.post(
        f"/tasks/{task.id}/start", headers=auth(users["other_worker"])
    )

    assert response.status_code == 403
    assert response.json()["error"] == "not_assignee"


def test_transitions_require_identity(client: TestClient, assigned: Task) -> None:
    assert client.post(f"/tasks/{assigned.id}/start").status_code == 401


def test_acting_on_a_missing_task_is_404(
    client: TestClient, users: dict[str, User]
) -> None:
    response = client.post("/tasks/999999/start", headers=auth(users["worker"]))
    assert response.status_code == 404


# --- release: the one way back -------------------------------------------


def test_assignee_can_release_work_with_a_reason(
    client: TestClient, users: dict[str, User], in_progress: Task
) -> None:
    response = client.post(
        f"/tasks/{in_progress.id}/release",
        json={"reason": "Calibration weights are on loan until Monday."},
        headers=auth(users["worker"]),
    )

    assert response.status_code == 200
    assert response.json()["status"] == TaskStatus.ASSIGNED.value


def test_release_stores_the_reason_in_history(
    client: TestClient, users: dict[str, User], in_progress: Task
) -> None:
    """The rule "not backward without a good reason" becomes a record."""
    reason = "Blocked on the vendor callback."
    client.post(
        f"/tasks/{in_progress.id}/release",
        json={"reason": reason},
        headers=auth(users["worker"]),
    )

    history = client.get(
        f"/tasks/{in_progress.id}/history", headers=auth(users["manager"])
    ).json()
    assert history[0]["from_status"] == "IN_PROGRESS"
    assert history[0]["to_status"] == "ASSIGNED"
    assert history[0]["reason"] == reason
    assert history[0]["changed_by"]["id"] == users["worker"].id


def test_release_keeps_the_same_assignee(
    client: TestClient, users: dict[str, User], in_progress: Task
) -> None:
    """Released work is not orphaned: someone stays responsible until a
    manager redirects it."""
    body = client.post(
        f"/tasks/{in_progress.id}/release",
        json={"reason": "Out of parts."},
        headers=auth(users["worker"]),
    ).json()
    assert body["assignee"]["id"] == users["worker"].id


def test_release_lets_a_manager_reassign_previously_stuck_work(
    client: TestClient, users: dict[str, User], in_progress: Task
) -> None:
    """The hole release was added to close: in-progress work cannot be
    reassigned, so without release it would be stuck with one person forever."""
    manager = auth(users["manager"])
    blocked = client.post(
        f"/tasks/{in_progress.id}/assign",
        json={"assignee_id": users["other_worker"].id},
        headers=manager,
    )
    assert blocked.status_code == 409

    client.post(
        f"/tasks/{in_progress.id}/release",
        json={"reason": "Cannot finish this week."},
        headers=auth(users["worker"]),
    )

    now_allowed = client.post(
        f"/tasks/{in_progress.id}/assign",
        json={"assignee_id": users["other_worker"].id},
        headers=manager,
    )
    assert now_allowed.status_code == 200
    assert now_allowed.json()["assignee"]["id"] == users["other_worker"].id


def test_released_work_can_be_started_again(
    client: TestClient, users: dict[str, User], in_progress: Task
) -> None:
    worker = auth(users["worker"])
    client.post(
        f"/tasks/{in_progress.id}/release",
        json={"reason": "Interrupted."},
        headers=worker,
    )
    response = client.post(f"/tasks/{in_progress.id}/start", headers=worker)
    assert response.status_code == 200
    assert response.json()["status"] == TaskStatus.IN_PROGRESS.value


@pytest.mark.parametrize("payload", [{}, {"reason": ""}, {"reason": "   "}])
def test_release_without_a_real_reason_is_rejected(
    client: TestClient,
    users: dict[str, User],
    in_progress: Task,
    payload: dict[str, str],
) -> None:
    response = client.post(
        f"/tasks/{in_progress.id}/release",
        json=payload,
        headers=auth(users["worker"]),
    )
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


def test_release_reason_is_trimmed(
    client: TestClient, users: dict[str, User], in_progress: Task
) -> None:
    client.post(
        f"/tasks/{in_progress.id}/release",
        json={"reason": "  Waiting on parts.  "},
        headers=auth(users["worker"]),
    )
    history = client.get(
        f"/tasks/{in_progress.id}/history", headers=auth(users["worker"])
    ).json()
    assert history[0]["reason"] == "Waiting on parts."


def test_manager_cannot_release_someone_elses_work(
    client: TestClient, users: dict[str, User], in_progress: Task
) -> None:
    response = client.post(
        f"/tasks/{in_progress.id}/release",
        json={"reason": "Reassigning this."},
        headers=auth(users["manager"]),
    )
    assert response.status_code == 403


# --- the history endpoint -------------------------------------------------


def test_history_is_empty_for_an_untouched_task(
    client: TestClient, users: dict[str, User], make_task: Callable[..., Task]
) -> None:
    task = make_task(creator=users["manager"], title="Nothing happened yet")
    response = client.get(f"/tasks/{task.id}/history", headers=auth(users["manager"]))
    assert response.status_code == 200
    assert response.json() == []


def test_history_of_an_invisible_task_is_404(
    client: TestClient, users: dict[str, User], assigned: Task
) -> None:
    response = client.get(
        f"/tasks/{assigned.id}/history", headers=auth(users["other_worker"])
    )
    assert response.status_code == 404


def test_history_exposes_only_declared_fields(
    client: TestClient, users: dict[str, User], assigned: Task
) -> None:
    client.post(f"/tasks/{assigned.id}/start", headers=auth(users["worker"]))
    history = client.get(
        f"/tasks/{assigned.id}/history", headers=auth(users["manager"])
    ).json()

    assert set(history[0]) == {
        "id",
        "from_status",
        "to_status",
        "changed_by",
        "reason",
        "changed_at",
    }


def test_only_backward_moves_carry_a_reason(
    client: TestClient, users: dict[str, User], assigned: Task
) -> None:
    worker = auth(users["worker"])
    client.post(f"/tasks/{assigned.id}/start", headers=worker)
    client.post(
        f"/tasks/{assigned.id}/release",
        json={"reason": "Ran out of time."},
        headers=worker,
    )

    history = client.get(f"/tasks/{assigned.id}/history", headers=worker).json()
    by_direction = {
        (event["from_status"], event["to_status"]): event["reason"]
        for event in history
    }
    assert by_direction[("ASSIGNED", "IN_PROGRESS")] is None
    assert by_direction[("IN_PROGRESS", "ASSIGNED")] == "Ran out of time."
