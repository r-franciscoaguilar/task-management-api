"""Task business rules.

Everything that decides *what is allowed* lives here rather than in the
routers, for two reasons: the rules are testable without an HTTP layer, and a
rule enforced in one place cannot be forgotten by a second endpoint that
touches the same data.

These functions raise AppError subclasses and never build HTTP responses.
"""

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.exceptions import NotFoundError
from app.models import Task, TaskStatus, User, UserRole


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
