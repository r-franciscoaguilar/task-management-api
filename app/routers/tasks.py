"""Task endpoints.

Routers stay thin: they translate HTTP to service calls and back. Every rule
about who may do what lives in app/services/tasks.py.
"""

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.deps import CurrentUser, DbSession, Pagination
from app.models import Task, TaskStatus
from app.schemas.pagination import Page
from app.schemas.task import TaskCreate, TaskOut
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

    Managers see the whole team's workload; workers see their own queue plus
    anything they filed. Filters apply within that scope, never around it.
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


@router.get("/{task_id}", response_model=TaskOut, summary="Get one task")
def get_task(task_id: int, db: DbSession, caller: CurrentUser) -> Task:
    """Fetch a task the caller is entitled to see, otherwise 404."""
    return task_service.get_task(db, caller=caller, task_id=task_id)
