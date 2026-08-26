"""Read-only endpoints for discovering who exists.

Nothing in the brief asks for a user directory; these exist for operability, so
that a client integrating with the API -- or a reviewer running it locally --
can find the ids to act as and the workers eligible to receive work. Both roles
may read it: a manager needs it to choose an assignee, and it is a small,
non-sensitive reference set. A production system would likely narrow this (see
the README).
"""

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.deps import CurrentUser, DbSession
from app.models import User, UserRole
from app.schemas.user import UserOut

router = APIRouter(prefix="/users", tags=["users"])


# Declared before any parameterised path so that "me" is never mistaken for an
# id, should a /users/{user_id} route be added later.
@router.get("/me", response_model=UserOut, summary="The caller's own record")
def read_current_user(user: CurrentUser) -> User:
    """Resolve whoever the X-User-Id header names.

    Useful as a sanity check that a client is sending identity correctly, and
    as the cheapest way to see one's own role.
    """
    return user


@router.get("", response_model=list[UserOut], summary="List users")
def list_users(
    db: DbSession,
    caller: CurrentUser,
    role: UserRole | None = Query(
        default=None, description="Restrict to one role, e.g. WORKER."
    ),
) -> list[User]:
    """List everyone, optionally filtered by role.

    Returned as a plain array rather than the paginated envelope used for
    tasks. This is a small, bounded reference set, whereas the brief asks
    specifically for a practical way to browse *work items* as they grow.
    """
    statement = select(User).order_by(User.id)
    if role is not None:
        statement = statement.where(User.role == role)
    return list(db.scalars(statement))
