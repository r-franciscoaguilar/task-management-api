"""Demo data so a reviewer can exercise the API immediately.

It writes event rows, not just tasks: a task in IN_PROGRESS with no history
would contradict the invariants the API enforces. But it does not go through the
service layer to do so, because assigning sends a real email and seeding that
way would mail fake addresses on every boot. The helpers below mirror the
service instead, and tests/test_seed.py catches drift.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.models import (
    AssignmentEvent,
    NotificationStatus,
    StatusChangeEvent,
    Task,
    TaskStatus,
    User,
    UserRole,
)

# Seeded history is dated into the past so ordering is meaningful rather than
# every row sharing one timestamp.
_EPOCH_DAYS_AGO = 10


def _at(hours: float) -> datetime:
    """A point in the seeded timeline, `hours` after its start."""
    return utcnow() - timedelta(days=_EPOCH_DAYS_AGO) + timedelta(hours=hours)


def _assign(
    session: Session,
    task: Task,
    *,
    by: User,
    to: User,
    at: datetime,
    notification: NotificationStatus = NotificationStatus.SENT,
    error: str | None = None,
) -> None:
    """Record an assignment as the service layer does.

    Note the asymmetry behind two event tables: a reassignment writes an
    AssignmentEvent but no StatusChangeEvent -- ownership moved, status did not.
    """
    # A fresh Task has status=None until flushed -- column defaults apply on
    # INSERT, not __init__ -- so flush before reading the current status.
    session.flush()
    previous = task.status

    task.assignee_id = to.id
    task.status = TaskStatus.ASSIGNED
    task.assigned_at = at
    task.updated_at = at

    session.add(
        AssignmentEvent(
            task=task,
            assigned_by=by,
            assigned_to=to,
            assigned_at=at,
            notification_status=notification,
            notification_sent_at=(
                at if notification is NotificationStatus.SENT else None
            ),
            notification_error=error,
        )
    )

    if previous is not TaskStatus.ASSIGNED:
        session.add(
            StatusChangeEvent(
                task=task,
                from_status=previous,
                to_status=TaskStatus.ASSIGNED,
                changed_by=by,
                changed_at=at,
            )
        )


def _move(
    session: Session,
    task: Task,
    *,
    to: TaskStatus,
    by: User,
    at: datetime,
    reason: str | None = None,
) -> None:
    """Record a lifecycle move, again mirroring the service layer."""
    session.flush()  # see _assign: defaults materialize on flush
    session.add(
        StatusChangeEvent(
            task=task,
            from_status=task.status,
            to_status=to,
            changed_by=by,
            changed_at=at,
            reason=reason,
        )
    )
    task.status = to
    task.updated_at = at
    if to is TaskStatus.DONE:
        task.completed_at = at


def seed_if_empty(session: Session) -> bool:
    """Populate demo data unless any users exist. Returns True if it wrote.

    The emptiness guard makes this safe to run on every startup.
    """
    if session.scalar(select(func.count()).select_from(User)):
        return False

    alice = User(name="Alice Nguyen", email="alice@example.com", role=UserRole.MANAGER)
    bob = User(name="Bob Okafor", email="bob@example.com", role=UserRole.MANAGER)
    carol = User(name="Carol Diaz", email="carol@example.com", role=UserRole.WORKER)
    dave = User(name="Dave Lindqvist", email="dave@example.com", role=UserRole.WORKER)
    erin = User(name="Erin Sokolov", email="erin@example.com", role=UserRole.WORKER)
    session.add_all([alice, bob, carol, dave, erin])
    session.flush()  # assign ids before they are referenced below

    # 1. Filed and waiting for a manager to route it.
    session.add(
        Task(
            title="Replace intake filter on pump 3",
            description="Maintenance window is Thursday morning.",
            creator=alice,
            created_at=_at(0),
            updated_at=_at(0),
        )
    )

    # 2. Filed by a worker, showing that creation is not a manager-only power.
    session.add(
        Task(
            title="Loading bay door sticks when closing",
            description="Reported by the night shift; not urgent but worsening.",
            creator=dave,
            created_at=_at(1),
            updated_at=_at(1),
        )
    )

    # 3. Assigned and waiting to be picked up.
    signage = Task(
        title="Audit safety signage in warehouse B",
        description="Compare against the updated floor plan.",
        creator=alice,
        created_at=_at(2),
        updated_at=_at(2),
    )
    session.add(signage)
    _assign(session, signage, by=alice, to=carol, at=_at(3))

    # 4. Currently being worked on.
    invoices = Task(
        title="Reconcile October delivery invoices",
        description="Three line items do not match the receiving log.",
        creator=bob,
        created_at=_at(4),
        updated_at=_at(4),
    )
    session.add(invoices)
    _assign(session, invoices, by=bob, to=dave, at=_at(5))
    _move(session, invoices, to=TaskStatus.IN_PROGRESS, by=dave, at=_at(20))

    # 5. Finished, end to end.
    onboarding = Task(
        title="Update onboarding checklist for new hires",
        description="Add the badge-access step that was missed last quarter.",
        creator=alice,
        created_at=_at(6),
        updated_at=_at(6),
    )
    session.add(onboarding)
    _assign(session, onboarding, by=alice, to=erin, at=_at(7))
    _move(session, onboarding, to=TaskStatus.IN_PROGRESS, by=erin, at=_at(26))
    _move(session, onboarding, to=TaskStatus.DONE, by=erin, at=_at(48))

    # 6. The interesting one: started, released with a reason, then handed to
    #    someone else. This is where the two event tables visibly diverge.
    scale = Task(
        title="Recalibrate scale in the loading bay",
        description="Readings drift by roughly 2kg under load.",
        creator=bob,
        created_at=_at(8),
        updated_at=_at(8),
    )
    session.add(scale)
    _assign(session, scale, by=bob, to=carol, at=_at(9))
    _move(session, scale, to=TaskStatus.IN_PROGRESS, by=carol, at=_at(30))
    _move(
        session,
        scale,
        to=TaskStatus.ASSIGNED,
        by=carol,
        at=_at(34),
        reason="Calibration weights are on loan to the other site until Monday.",
    )
    _assign(session, scale, by=bob, to=dave, at=_at(36))  # reassignment

    # 7. A notification that failed, so the failure is demonstrably visible
    #    rather than silent. The assignment still stands.
    inventory = Task(
        title="Spot-check inventory counts on aisle 7",
        description="Cycle count variance flagged by the weekly report.",
        creator=alice,
        created_at=_at(10),
        updated_at=_at(10),
    )
    session.add(inventory)
    _assign(
        session,
        inventory,
        by=alice,
        to=erin,
        at=_at(11),
        notification=NotificationStatus.FAILED,
        error="SMTP connection refused (demo of a visible delivery failure).",
    )

    session.commit()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the task database.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop every table and rebuild before seeding.",
    )
    args = parser.parse_args()

    # Imported here so `python -m app.seed` does not pay for engine setup at
    # import time when it is only being used as a library by the app.
    from app.db import Base, SessionLocal, engine

    if args.reset:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as session:
        if seed_if_empty(session):
            users = session.scalar(select(func.count()).select_from(User))
            tasks = session.scalar(select(func.count()).select_from(Task))
            print(f"Seeded {users} users and {tasks} tasks.")
        else:
            print("Database already has users; nothing seeded. Use --reset to rebuild.")


if __name__ == "__main__":
    main()
