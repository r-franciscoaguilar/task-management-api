"""Request-scoped dependencies: who is calling, and may they do this.

Authentication is out of scope, so the API trusts an `X-User-Id` header and
looks the role up from the database. Anyone reaching the service can therefore
claim to be anyone -- see the README. Authorization is real and tested.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.exceptions import ForbiddenError, UnauthenticatedError
from app.models import User, UserRole
from app.schemas.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.services.notifications import NotificationSender, get_notification_sender

USER_ID_HEADER = "X-User-Id"

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbSession,
    x_user_id: Annotated[str | None, Header(alias=USER_ID_HEADER)] = None,
) -> User:
    """Resolve the caller from the identity header.

    Typed as a string, not an int: an int annotation would make FastAPI reject
    `bogus` with its own 422, so a malformed credential and an unknown one
    would fail differently. Both are unusable credentials, so both are 401.
    """
    if x_user_id is None or not x_user_id.strip():
        raise UnauthenticatedError(
            f"Missing {USER_ID_HEADER} header. "
            "Every request must identify the acting user.",
        )

    try:
        user_id = int(x_user_id)
    except ValueError:
        raise UnauthenticatedError(
            f"{USER_ID_HEADER} must be an integer user id, got {x_user_id!r}.",
        ) from None

    user = db.get(User, user_id)
    if user is None:
        raise UnauthenticatedError(
            f"No user exists with id {user_id}.",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_manager(user: CurrentUser) -> User:
    """Guard for actions that belong to people who manage work."""
    if user.role is not UserRole.MANAGER:
        raise ForbiddenError(
            "This action is limited to managers.",
            error_code="manager_role_required",
            required_role=UserRole.MANAGER.value,
            actual_role=user.role.value,
        )
    return user


def require_worker(user: CurrentUser) -> User:
    """Guard for actions that belong to people who do the work.

    Managers are excluded outright: progressing a task is not a manager
    capability, per the brief.
    """
    if user.role is not UserRole.WORKER:
        raise ForbiddenError(
            "This action is limited to workers -- progress is recorded by the "
            "person doing the work.",
            error_code="worker_role_required",
            required_role=UserRole.WORKER.value,
            actual_role=user.role.value,
        )
    return user


ManagerUser = Annotated[User, Depends(require_manager)]
WorkerUser = Annotated[User, Depends(require_worker)]


@dataclass(frozen=True)
class PageParams:
    limit: int
    offset: int


def page_params(
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_PAGE_SIZE, description="Rows per page."),
    ] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0, description="Rows to skip.")] = 0,
) -> PageParams:
    """Shared paging bounds. The `le` ceiling matters: an unbounded limit is a
    denial-of-service lever."""
    return PageParams(limit=limit, offset=offset)


Pagination = Annotated[PageParams, Depends(page_params)]


Sender = Annotated[NotificationSender, Depends(get_notification_sender)]
