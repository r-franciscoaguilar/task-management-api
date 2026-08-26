"""Request-scoped dependencies: who is calling, and may they do this.

Authentication is out of scope for this exercise -- accounts live in another
system -- so the API trusts an `X-User-Id` header to name the caller and looks
the role up from the database. This is a deliberate stand-in for a real
identity layer, and the trade-off is spelled out in the README: anyone who can
reach the service can claim to be anyone. Authorization, by contrast, is real:
every rule below is enforced server-side and covered by tests.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.exceptions import ForbiddenError, UnauthenticatedError
from app.models import User, UserRole
from app.schemas.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

USER_ID_HEADER = "X-User-Id"

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbSession,
    x_user_id: Annotated[str | None, Header(alias=USER_ID_HEADER)] = None,
) -> User:
    """Resolve the caller from the identity header.

    The header is typed as a string rather than an int on purpose. If it were
    an int, FastAPI would reject a non-numeric value with its own 422 before
    this function ran, so `X-User-Id: bogus` and `X-User-Id: 999999` would fail
    in two different ways. Both are the same thing from the caller's point of
    view -- an unusable credential -- so both return 401 here.
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

    Managers are excluded rather than merely unhelped here: the brief says they
    "should not be doing someone else's work on their behalf", so progressing a
    task is not a manager capability at all.
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
    """Shared paging bounds.

    `le=MAX_PAGE_SIZE` matters: without a ceiling, a caller could request
    limit=1000000 and turn a list endpoint into a denial-of-service lever.
    """
    return PageParams(limit=limit, offset=offset)


Pagination = Annotated[PageParams, Depends(page_params)]
