"""Response shape for the lifecycle history."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import TaskStatus
from app.schemas.user import UserOut


class StatusChangeEventOut(BaseModel):
    """One movement through the lifecycle.

    `reason` is populated only for backward moves, where the brief requires a
    justification. Forward progress needs none, so it is null there.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    from_status: TaskStatus
    to_status: TaskStatus
    changed_by: UserOut
    reason: str | None
    changed_at: datetime
