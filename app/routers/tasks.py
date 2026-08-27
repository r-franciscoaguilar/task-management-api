"""Task endpoints.

Routers stay thin: they translate HTTP to service calls and back. Every rule
about who may do what lives in app/services/tasks.py.
"""

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.deps import (
    CurrentUser,
    DbSession,
    ManagerUser,
    Pagination,
    Sender,
    WorkerUser,
)
from app.models import AssignmentEvent, StatusChangeEvent, Task, TaskStatus
from app.schemas.assignment_event import AssignmentEventOut
from app.schemas.pagination import Page
from app.schemas.status_change_event import StatusChangeEventOut
from app.schemas.task import (
    AssignRequest,
    ReleaseRequest,
    TaskCreate,
    TaskDetailOut,
    TaskOut,
)
from app.services import tasks as task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post(
    "",
    response_model=TaskOut,
    status_code=status.HTTP_201_CREATED,
    summary="File a new task",
)
def create_task(
    payload: TaskCreate, db: DbSession, caller: CurrentUser
) -> Task:
    """Create a task. Available to both roles; always starts UNASSIGNED."""
    return task_service.create_task(
        db,
        creator=caller,
        title=payload.title,
        description=payload.description,
    )


@router.get("", response_model=Page[TaskOut], summary="List tasks")
def list_tasks(
    db: DbSession,
    caller: CurrentUser,
    page: Pagination,
    task_status: TaskStatus | None = Query(
        default=None,
        alias="status",
        description="Restrict to one lifecycle status.",
    ),
    assignee_id: int | None = Query(default=None, description="Filter by assignee."),
    creator_id: int | None = Query(default=None, description="Filter by creator."),
) -> Page[TaskOut]:
    """A page of tasks, newest first.

    Managers see the team's workload; workers see their own queue plus anything
    they filed. Filters apply within that scope, never around it.
    """
    items, total = task_service.list_tasks(
        db,
        caller=caller,
        limit=page.limit,
        offset=page.offset,
        status=task_status,
        assignee_id=assignee_id,
        creator_id=creator_id,
    )
    return Page[TaskOut](
        items=[TaskOut.model_validate(task) for task in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{task_id}", response_model=TaskDetailOut, summary="Get one task")
def get_task(task_id: int, db: DbSession, caller: CurrentUser) -> TaskDetailOut:
    """Fetch a task the caller is entitled to see, otherwise 404.

    Includes the latest assignment, so notification status is visible without a
    second call. Lists omit it to avoid loading history per row.
    """
    task = task_service.get_task(db, caller=caller, task_id=task_id)
    detail = TaskDetailOut.model_validate(task)
    if task.assignment_events:
        detail.latest_assignment = AssignmentEventOut.model_validate(
            task.assignment_events[0]  # relationship is ordered newest-first
        )
    return detail


@router.post(
    "/{task_id}/assign",
    response_model=TaskDetailOut,
    summary="Assign a task to a worker",
)
def assign_task(
    task_id: int,
    payload: AssignRequest,
    db: DbSession,
    caller: ManagerUser,
    sender: Sender,
) -> TaskDetailOut:
    """Make a worker responsible for a task, and email them.

    Managers only, and only while UNASSIGNED or ASSIGNED.
    """
    task = task_service.assign_task(
        db,
        caller=caller,
        task_id=task_id,
        assignee_id=payload.assignee_id,
        sender=sender,
    )
    detail = TaskDetailOut.model_validate(task)
    detail.latest_assignment = AssignmentEventOut.model_validate(
        task.assignment_events[0]
    )
    return detail


@router.post(
    "/{task_id}/start", response_model=TaskOut, summary="Start assigned work"
)
def start_task(task_id: int, db: DbSession, caller: WorkerUser) -> Task:
    """Move a task to IN_PROGRESS. The assignee only -- progressing work is not
    a manager capability."""
    return task_service.start_task(db, caller=caller, task_id=task_id)


@router.post(
    "/{task_id}/complete", response_model=TaskOut, summary="Complete work"
)
def complete_task(task_id: int, db: DbSession, caller: WorkerUser) -> Task:
    """Move a task to DONE, which is terminal."""
    return task_service.complete_task(db, caller=caller, task_id=task_id)


@router.post(
    "/{task_id}/release",
    response_model=TaskOut,
    summary="Hand work back, with a reason",
)
def release_task(
    task_id: int, payload: ReleaseRequest, db: DbSession, caller: WorkerUser
) -> Task:
    """Return in-progress work to ASSIGNED, recording why.

    The only backward transition. The reason is mandatory and kept in history.
    """
    return task_service.release_task(
        db, caller=caller, task_id=task_id, reason=payload.reason
    )


@router.get(
    "/{task_id}/history",
    response_model=list[StatusChangeEventOut],
    summary="Lifecycle history",
)
def list_history(
    task_id: int, db: DbSession, caller: CurrentUser
) -> list[StatusChangeEvent]:
    """Every status change this task has been through, newest first."""
    return task_service.list_status_history(db, caller=caller, task_id=task_id)


@router.get(
    "/{task_id}/assignments",
    response_model=list[AssignmentEventOut],
    summary="Assignment and notification history",
)
def list_assignments(
    task_id: int, db: DbSession, caller: CurrentUser
) -> list[AssignmentEvent]:
    """Every assignment this task has had, newest first, including whether each
    notification was delivered."""
    return task_service.list_assignments(db, caller=caller, task_id=task_id)
