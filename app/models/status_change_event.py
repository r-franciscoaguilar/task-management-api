from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import UtcDateTime, utcnow
from app.db import Base
from app.models.task import TaskStatus

if TYPE_CHECKING:
    from app.models.task import Task
    from app.models.user import User


class StatusChangeEvent(Base):
    """An append-only record of one movement through the task lifecycle.

    This tracks a different axis than AssignmentEvent. Assignment answers
    "who owns this, and were they told"; this answers "where did the work
    stand, who moved it, and why". The two genuinely diverge: reassigning a
    task changes ownership without changing status, while starting one
    changes status without changing ownership.

    `reason` is optional at the schema level but required by the service for
    backward transitions -- it is how the business rule "work should not
    bounce backward without a good reason" becomes an auditable record rather
    than an unenforced expectation.
    """

    __tablename__ = "status_change_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    from_status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, native_enum=False)
    )
    to_status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, native_enum=False)
    )
    changed_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, default=None)
    changed_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)

    task: Mapped["Task"] = relationship(back_populates="status_change_events")
    changed_by: Mapped["User"] = relationship(lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<StatusChangeEvent id={self.id} task_id={self.task_id} "
            f"{self.from_status.value}->{self.to_status.value} by={self.changed_by_id}>"
        )
