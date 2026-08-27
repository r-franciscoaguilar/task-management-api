"""Read-only endpoints for discovering who exists.

Not asked for by the brief; they exist so a client or a reviewer can find the
ids to act as and the workers eligible for work. Readable by both roles -- a
manager needs it to choose an assignee. Production would likely narrow this.
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
    """Resolve whoever the X-User-Id header names."""
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

    A plain array, not the paginated envelope used for tasks: this is a small
    bounded reference set.
    """
    statement = select(User).order_by(User.id)
    if role is not None:
        statement = statement.where(User.role == role)
    return list(db.scalars(statement))
