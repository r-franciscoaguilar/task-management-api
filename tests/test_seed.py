"""The seed data must obey the same invariants the API enforces.

Because app/seed.py writes rows directly instead of going through the service
layer (it must not fire real emails on every boot), these tests are what keep
the demo data honest. If a future change to the domain rules makes the seed
inconsistent, this fails rather than shipping a reviewer a contradictory
database.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AssignmentEvent,
    NotificationStatus,
    StatusChangeEvent,
    Task,
    TaskStatus,
    User,
    UserRole,
)
from app.seed import seed_if_empty


@pytest.fixture()
def seeded(db: Session) -> Session:
    assert seed_if_empty(db) is True
    return db


def _tasks(session: Session) -> list[Task]:
    return list(session.scalars(select(Task).order_by(Task.id)))


# --- the guard ------------------------------------------------------------


def test_seeding_is_a_no_op_when_users_exist(seeded: Session) -> None:
    before = seeded.scalar(select(func.count()).select_from(Task))
    assert seed_if_empty(seeded) is False
    assert seeded.scalar(select(func.count()).select_from(Task)) == before


def test_user_ids_are_deterministic(seeded: Session) -> None:
    """Reviewers copy these ids straight into X-User-Id, so they must be stable."""
    people = list(seeded.scalars(select(User).order_by(User.id)))
    assert [(u.id, u.role) for u in people] == [
        (1, UserRole.MANAGER),
        (2, UserRole.MANAGER),
        (3, UserRole.WORKER),
        (4, UserRole.WORKER),
        (5, UserRole.WORKER),
    ]


# --- coverage of the demo surface ----------------------------------------


def test_every_status_is_represented(seeded: Session) -> None:
    """A fresh clone should show the whole lifecycle, not one slice of it."""
    present = {task.status for task in _tasks(seeded)}
    assert present == set(TaskStatus)


def test_a_worker_created_task_exists(seeded: Session) -> None:
    """Demonstrates the assumption that creation is not manager-only."""
    creators = {task.creator.role for task in _tasks(seeded)}
    assert UserRole.WORKER in creators


def test_a_failed_notification_is_visible(seeded: Session) -> None:
    """The failure path must be demonstrable, not just handled."""
    failed = seeded.scalars(
        select(AssignmentEvent).where(
            AssignmentEvent.notification_status == NotificationStatus.FAILED
        )
    ).all()
    assert len(failed) >= 1
    event = failed[0]
    assert event.notification_error
    assert event.notification_sent_at is None
    # The assignment itself still stands despite the delivery failure.
    assert event.task.assignee_id == event.assigned_to_id


def test_a_release_with_a_reason_exists(seeded: Session) -> None:
    backward = seeded.scalars(
        select(StatusChangeEvent).where(
            StatusChangeEvent.from_status == TaskStatus.IN_PROGRESS,
            StatusChangeEvent.to_status == TaskStatus.ASSIGNED,
        )
    ).all()
    assert len(backward) >= 1
    assert all(event.reason for event in backward)


def test_a_reassignment_exists_without_a_status_change(seeded: Session) -> None:
    """The case that justifies two separate event tables.

    A reassignment moves ownership while status stays ASSIGNED, so it produces
    an AssignmentEvent with no corresponding StatusChangeEvent.
    """
    reassigned = [
        task
        for task in _tasks(seeded)
        if len({event.assigned_to_id for event in task.assignment_events}) > 1
    ]
    assert reassigned, "expected at least one task assigned to more than one person"

    # Compare by timestamp rather than by count: a release also moves *to*
    # ASSIGNED, so counting ASSIGNED-bound status changes would not isolate
    # reassignment. Seeded moments are distinct, so an assignment timestamp
    # with no matching status change is precisely an ownership-only move.
    task = reassigned[0]
    assignment_moments = {event.assigned_at for event in task.assignment_events}
    status_moments = {event.changed_at for event in task.status_change_events}
    ownership_only = assignment_moments - status_moments
    assert ownership_only, (
        "expected an assignment that changed ownership without changing status"
    )


# --- invariants -----------------------------------------------------------


def test_unassigned_tasks_have_no_assignee_and_no_history(seeded: Session) -> None:
    for task in _tasks(seeded):
        if task.status is not TaskStatus.UNASSIGNED:
            continue
        assert task.assignee_id is None
        assert task.assigned_at is None
        assert task.assignment_events == []
        assert task.status_change_events == []


def test_assigned_tasks_have_an_assignee_and_an_assignment_record(
    seeded: Session,
) -> None:
    """Nobody is on the hook without a record of being told."""
    for task in _tasks(seeded):
        if task.status is TaskStatus.UNASSIGNED:
            continue
        assert task.assignee_id is not None
        assert task.assigned_at is not None
        assert task.assignment_events, f"task {task.id} has no assignment record"


def test_current_assignee_matches_the_latest_assignment(seeded: Session) -> None:
    """Task.assignee_id is a projection of the event log, so they must agree."""
    for task in _tasks(seeded):
        if not task.assignment_events:
            continue
        # The relationship is ordered newest-first.
        assert task.assignee_id == task.assignment_events[0].assigned_to_id


def test_status_history_forms_an_unbroken_chain(seeded: Session) -> None:
    """Each move must start where the previous one ended, and end at today."""
    for task in _tasks(seeded):
        history = list(reversed(task.status_change_events))  # oldest first
        if not history:
            continue

        assert history[0].from_status is TaskStatus.UNASSIGNED
        for earlier, later in zip(history, history[1:]):
            assert earlier.to_status is later.from_status, (
                f"task {task.id}: {earlier.to_status} does not lead into "
                f"{later.from_status}"
            )
        assert history[-1].to_status is task.status


def test_only_backward_moves_carry_a_reason(seeded: Session) -> None:
    """A reason is the justification for reversing; forward moves need none."""
    order = {
        TaskStatus.UNASSIGNED: 0,
        TaskStatus.ASSIGNED: 1,
        TaskStatus.IN_PROGRESS: 2,
        TaskStatus.DONE: 3,
    }
    for event in seeded.scalars(select(StatusChangeEvent)):
        went_backward = order[event.to_status] < order[event.from_status]
        if went_backward:
            assert event.reason, f"backward move {event.id} has no reason"
        else:
            assert event.reason is None


def test_completed_tasks_are_stamped_and_others_are_not(seeded: Session) -> None:
    for task in _tasks(seeded):
        if task.status is TaskStatus.DONE:
            assert task.completed_at is not None
        else:
            assert task.completed_at is None


def test_timestamps_are_ordered_within_each_task(seeded: Session) -> None:
    for task in _tasks(seeded):
        for event in task.assignment_events:
            assert event.assigned_at >= task.created_at
        for event in task.status_change_events:
            assert event.changed_at >= task.created_at
        assert task.updated_at >= task.created_at


def test_assignees_are_always_workers(seeded: Session) -> None:
    """Assigning work to a manager is invalid, so the seed must not contain it."""
    for task in _tasks(seeded):
        if task.assignee is not None:
            assert task.assignee.role is UserRole.WORKER
        for event in task.assignment_events:
            assert event.assigned_to.role is UserRole.WORKER
            assert event.assigned_by.role is UserRole.MANAGER
