"""Response shape for the assignment audit trail."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import NotificationStatus
from app.schemas.user import UserOut


class AssignmentEventOut(BaseModel):
    """One assignment, and what happened to its notification.

    `notification_status` is the point of this record. The brief asks that
    whether someone was told "should not be invisible", so a failed send is
    reported here rather than swallowed.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    assigned_by: UserOut
    assigned_to: UserOut
    assigned_at: datetime
    notification_status: NotificationStatus
    notification_sent_at: datetime | None
    notification_error: str | None
