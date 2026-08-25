import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import UtcDateTime, utcnow
from app.db import Base

if TYPE_CHECKING:
    from app.models.task import Task
    from app.models.user import User


class NotificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class AssignmentEvent(Base):
    """An append-only record of one assignment and its notification attempt.

    This is what makes the business's traceability requirement concrete: every
    assignment writes a row before any email is attempted, and the outcome of
    that attempt is recorded on it. 
    Then, a failed notification is visible rather than silent,
    and reassignment produces a history rather than overwriting what came before.
    """

    __tablename__ = "assignment_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    assigned_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assigned_to_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    assigned_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow
    )

    notification_status: Mapped[NotificationStatus] = mapped_column(
        SAEnum(NotificationStatus, native_enum=False),
        default=NotificationStatus.PENDING,
    )
    notification_sent_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, default=None
    )
    notification_error: Mapped[Optional[str]] = mapped_column(Text, default=None)

    task: Mapped["Task"] = relationship(back_populates="assignment_events")
    assigned_by: Mapped["User"] = relationship(
        foreign_keys=[assigned_by_id], lazy="joined"
    )
    assigned_to: Mapped["User"] = relationship(
        foreign_keys=[assigned_to_id], lazy="joined"
    )

    def __repr__(self) -> str:
        return (
            f"<AssignmentEvent id={self.id} task_id={self.task_id} "
            f"to={self.assigned_to_id} notification={self.notification_status.value}>"
        )
