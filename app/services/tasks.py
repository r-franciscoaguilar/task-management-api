"""Task business rules.

Everything deciding what is allowed lives here, not in the routers: the rules
are testable without HTTP, and a rule in one place cannot be forgotten by a
second endpoint. These functions raise AppError and never build responses.
"""

import logging

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.exceptions import (
    ConflictError,
    ForbiddenError,
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
    """File a new task. Open to both roles.

    Always starts UNASSIGNED: the caller cannot assign in the same breath,
    because assigning is separately notified and audited.
    """
    task = Task(title=title, description=description, creator_id=creator.id)
    session.add(task)
    session.commit()
    return task


def _visibility_conditions(caller: User) -> list[ColumnElement[bool]]:
    """Rows this caller is allowed to know exist.

    Managers see everything. Workers see work assigned to them *or* filed by
    them -- reporting a problem should not mean losing sight of it before it is
    routed.
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

    Filters apply *inside* the visible scope, so a worker filtering by someone
    else's assignee_id gets an empty page rather than their own tasks back.
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

    A task the caller may not see returns the same 404, wording included, as
    one that is genuinely absent. A 403 would confirm it exists.
    """
    task = session.get(Task, task_id)
    if task is None or not may_view(caller, task):
        raise NotFoundError(f"No task with id {task_id} is available to you.")
    return task


def list_assignments(
    session: Session, *, caller: User, task_id: int
) -> list[AssignmentEvent]:
    """The assignment history of one task, newest first.

    Via get_task, so whoever cannot see the task cannot see its assignments.
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

    Managers only (enforced at the route), and only while UNASSIGNED or
    ASSIGNED: work already under way must be released by its assignee first.
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

    Failure never rolls back the assignment -- responsibility must not depend on
    a reachable mail server -- but is not silent either: it is stored, served,
    and logged.

    Every exception is caught, not only NotificationSendError. The assignment is
    already committed, so letting one escape would return 500 for a request that
    succeeded and leave the notification PENDING forever.
    """
    try:
        sender.send(build_assignment_email(task=task, assignee=assignee, assigned_by=by))
    except Exception as error:  # broad by design -- see docstring
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


def list_status_history(
    session: Session, *, caller: User, task_id: int
) -> list[StatusChangeEvent]:
    """The lifecycle history of one task, newest first."""
    task = get_task(session, caller=caller, task_id=task_id)
    return list(task.status_change_events)


def _load_task_to_act_on(session: Session, *, caller: User, task_id: int) -> Task:
    """Fetch a task the caller may *change*, not merely read.

    Visibility first: a task they cannot see is 404, so existence is not leaked.
    Only then does being the assignee matter, and failing that is 403 -- they can
    already see the task, so denying its existence would be nonsense.
    """
    task = get_task(session, caller=caller, task_id=task_id)
    if task.assignee_id != caller.id:
        raise ForbiddenError(
            "Only the person a task is assigned to can record progress on it.",
            error_code="not_assignee",
        )
    return task


def _record_transition(
    session: Session,
    *,
    caller: User,
    task: Task,
    to: TaskStatus,
    reason: str | None = None,
) -> Task:
    """Move a task and write the matching history row.

    Single funnel, so a transition cannot happen without being recorded.
    """
    now = utcnow()

    session.add(
        StatusChangeEvent(
            task=task,
            from_status=task.status,
            to_status=to,
            changed_by=caller,
            reason=reason,
            changed_at=now,
        )
    )

    task.status = to
    task.updated_at = now
    if to is TaskStatus.DONE:
        task.completed_at = now

    session.commit()
    return task


def start_task(session: Session, *, caller: User, task_id: int) -> Task:
    """Pick up assigned work."""
    task = _load_task_to_act_on(session, caller=caller, task_id=task_id)

    if task.status is not TaskStatus.ASSIGNED:
        raise InvalidStateTransitionError(
            "Only a task that is assigned and not yet started can be started.",
            current_status=task.status.value,
        )

    return _record_transition(session, caller=caller, task=task, to=TaskStatus.IN_PROGRESS)


def complete_task(session: Session, *, caller: User, task_id: int) -> Task:
    """Finish work that is under way."""
    task = _load_task_to_act_on(session, caller=caller, task_id=task_id)

    if task.status is not TaskStatus.IN_PROGRESS:
        raise InvalidStateTransitionError(
            "Only work that is in progress can be completed.",
            current_status=task.status.value,
        )

    return _record_transition(session, caller=caller, task=task, to=TaskStatus.DONE)


def release_task(
    session: Session, *, caller: User, task_id: int, reason: str
) -> Task:
    """Hand work back because it cannot be continued.

    The one backward transition, and the reason is mandatory: that is what makes
    "not backward without a good reason" auditable rather than aspirational.

    Returns to ASSIGNED with the same person, so the work is not orphaned -- and
    a manager can now redirect it, since reassignment is blocked only while work
    is under way.
    """
    task = _load_task_to_act_on(session, caller=caller, task_id=task_id)

    if task.status is not TaskStatus.IN_PROGRESS:
        raise InvalidStateTransitionError(
            "Only work that is in progress can be released.",
            current_status=task.status.value,
        )

    # Also checked in the request schema; enforced here too because the service
    # layer -- not the HTTP layer -- owns this rule.
    cleaned = reason.strip()
    if not cleaned:
        raise ValidationError(
            "Releasing work requires a reason.",
            error_code="reason_required",
        )

    return _record_transition(
        session, caller=caller, task=task, to=TaskStatus.ASSIGNED, reason=cleaned
    )
