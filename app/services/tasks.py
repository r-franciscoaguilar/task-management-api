"""Task business rules.

Everything that decides *what is allowed* lives here rather than in the
routers, for two reasons: the rules are testable without an HTTP layer, and a
rule enforced in one place cannot be forgotten by a second endpoint that
touches the same data.

These functions raise AppError subclasses and never build HTTP responses.
"""

import logging

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.exceptions import (
    ConflictError,
    InvalidStateTransitionError,
    NotFoundError,
    ValidationError,
)
from app.models import (
    AssignmentEvent,
    NotificationStatus,
    StatusChangeEvent,
    Task,
    TaskStatus,
    User,
    UserRole,
)
from app.services.notifications import (
    NotificationSender,
    build_assignment_email,
)

logger = logging.getLogger(__name__)


def create_task(
    session: Session, *, creator: User, title: str, description: str | None
) -> Task:
    """File a new task.

    Open to both roles. The brief describes creation role-agnostically ("work
    starts when someone identifies something that needs to be done") and only
    assignment as a management power. A new task always starts UNASSIGNED --
    the caller cannot hand it to someone in the same breath, because assigning
    is a separate, notified, audited action.
    """
    task = Task(title=title, description=description, creator_id=creator.id)
    session.add(task)
    session.commit()
    return task


def _visibility_conditions(caller: User) -> list[ColumnElement[bool]]:
    """Rows this caller is allowed to know exist.

    Managers see everything -- the brief says they need visibility across the
    team. Workers see what is theirs, which means work assigned to them *or*
    work they filed themselves: a worker who reports a problem should not lose
    sight of it just because it has not been routed to them yet.
    """
    if caller.role is UserRole.MANAGER:
        return []
    return [or_(Task.creator_id == caller.id, Task.assignee_id == caller.id)]


def may_view(caller: User, task: Task) -> bool:
    if caller.role is UserRole.MANAGER:
        return True
    return caller.id in (task.creator_id, task.assignee_id)


def list_tasks(
    session: Session,
    *,
    caller: User,
    limit: int,
    offset: int,
    status: TaskStatus | None = None,
    assignee_id: int | None = None,
    creator_id: int | None = None,
) -> tuple[list[Task], int]:
    """A page of visible tasks, newest first, plus the total that matched.

    Filters are applied *inside* the caller's visible scope rather than being
    ignored for workers. So a worker filtering by someone else's assignee_id
    gets an empty page -- a truthful "nothing you can see matches" -- instead of
    silently receiving their own tasks back, which would be actively misleading.
    """
    conditions = _visibility_conditions(caller)
    if status is not None:
        conditions.append(Task.status == status)
    if assignee_id is not None:
        conditions.append(Task.assignee_id == assignee_id)
    if creator_id is not None:
        conditions.append(Task.creator_id == creator_id)

    total = session.scalar(
        select(func.count()).select_from(Task).where(*conditions)
    )

    items = list(
        session.scalars(
            select(Task)
            .where(*conditions)
            # id breaks ties so paging is deterministic even when two tasks
            # share a created_at, which is otherwise a source of duplicate or
            # skipped rows across pages.
            .order_by(Task.created_at.desc(), Task.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return items, total or 0


def get_task(session: Session, *, caller: User, task_id: int) -> Task:
    """Fetch one task, or raise as though it does not exist.

    A task the caller may not see returns exactly the same 404 as a task that
    genuinely is not there -- same status, same message. A 403 would confirm the
    task exists, which is information the caller has no right to. "The wrong
    person cannot" is read as cannot *observe*, not merely cannot modify.
    """
    task = session.get(Task, task_id)
    if task is None or not may_view(caller, task):
        raise NotFoundError(f"No task with id {task_id} is available to you.")
    return task


def list_assignments(
    session: Session, *, caller: User, task_id: int
) -> list[AssignmentEvent]:
    """The assignment history of one task, newest first.

    Goes through get_task so the same visibility rule applies: a caller who may
    not see the task may not see who it has been given to either.
    """
    task = get_task(session, caller=caller, task_id=task_id)
    return list(task.assignment_events)


def assign_task(
    session: Session,
    *,
    caller: User,
    task_id: int,
    assignee_id: int,
    sender: NotificationSender,
) -> Task:
    """Put a worker on the hook for a task, and tell them.

    Only reachable by a manager (enforced at the route). Allowed while a task is
    UNASSIGNED or ASSIGNED -- a manager may redirect work that has not been
    picked up yet, but not work already in progress; for that the assignee must
    release it first, which records why.
    """
    task = get_task(session, caller=caller, task_id=task_id)

    if task.status not in (TaskStatus.UNASSIGNED, TaskStatus.ASSIGNED):
        raise InvalidStateTransitionError(
            "Work already under way cannot be reassigned. The assignee must "
            "release it first, which records the reason.",
            current_status=task.status.value,
        )

    assignee = session.get(User, assignee_id)
    if assignee is None:
        raise NotFoundError(f"No user with id {assignee_id} exists.")

    if assignee.role is not UserRole.WORKER:
        # 422 rather than 404: the user exists, but is not a valid assignee.
        raise ValidationError(
            f"{assignee.name} is a manager. Work is assigned to people who do "
            "the work.",
            error_code="invalid_assignee",
            assignee_id=assignee_id,
            assignee_role=assignee.role.value,
        )

    if task.assignee_id == assignee.id:
        # Rejected rather than treated as a nudge, because every assignment
        # sends a real email: a double-submitted request would otherwise mail
        # the same person twice. A deliberate "resend notification" action
        # would be the right way to nudge, and is noted as future work.
        raise ConflictError(
            f"Task {task.id} is already assigned to {assignee.name}.",
            error_code="already_assigned",
            assignee_id=assignee_id,
        )

    previous_status = task.status
    now = utcnow()

    # Assign the relationship, not the raw foreign key. Setting assignee_id
    # alone leaves an already-loaded `assignee` stale, and because sessions here
    # use expire_on_commit=False that stale value survives the commit and gets
    # serialized -- reporting a freshly assigned task as having no assignee.
    task.assignee = assignee
    task.status = TaskStatus.ASSIGNED
    task.assigned_at = now
    task.updated_at = now

    event = AssignmentEvent(
        task=task,
        assigned_by=caller,
        assigned_to=assignee,
        assigned_at=now,
        notification_status=NotificationStatus.PENDING,
    )
    session.add(event)

    # A reassignment moves ownership without moving status, so it records no
    # status change. This asymmetry is why assignment and lifecycle history are
    # separate tables.
    if previous_status is not TaskStatus.ASSIGNED:
        session.add(
            StatusChangeEvent(
                task=task,
                from_status=previous_status,
                to_status=TaskStatus.ASSIGNED,
                changed_by=caller,
                changed_at=now,
            )
        )

    # Committed *before* attempting delivery, on purpose. If the process dies
    # mid-send, the record survives as PENDING -- an assignment whose
    # notification is unaccounted for, which is visible and can be retried.
    # Sending first and recording after would lose that entirely.
    session.commit()

    _notify_assignee(
        session,
        sender=sender,
        event=event,
        task=task,
        assignee=assignee,
        by=caller,
    )
    return task


def _notify_assignee(
    session: Session,
    *,
    sender: NotificationSender,
    event: AssignmentEvent,
    task: Task,
    assignee: User,
    by: User,
) -> None:
    """Attempt delivery and record the outcome on the assignment record.

    Delivery failure never rolls back the assignment: who is responsible for
    work must not depend on an email server being reachable. But the failure is
    not silent either -- it is stored on the event, served by the API, and
    logged.

    Every exception is caught, not just NotificationSendError. The assignment is
    already committed at this point, so letting an unexpected error escape would
    return 500 to a client whose request actually succeeded, and leave the
    notification recorded as PENDING forever. Recording FAILED with the message
    is both more honest and more useful.
    """
    try:
        sender.send(build_assignment_email(task=task, assignee=assignee, assigned_by=by))
    except Exception as error:  # noqa: BLE001 -- see docstring
        logger.warning(
            "Assignment notification failed for task %s to %s: %s",
            task.id,
            assignee.email,
            error,
        )
        event.notification_status = NotificationStatus.FAILED
        event.notification_error = str(error) or error.__class__.__name__
        event.notification_sent_at = None
    else:
        event.notification_status = NotificationStatus.SENT
        event.notification_sent_at = utcnow()
        event.notification_error = None

    session.commit()
