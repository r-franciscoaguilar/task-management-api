import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import UtcDateTime, utcnow
from app.db import Base

if TYPE_CHECKING:
    from app.models.assignment_event import AssignmentEvent
    from app.models.status_change_event import StatusChangeEvent
    from app.models.user import User


class TaskStatus(str, enum.Enum):
    """The lifecycle of a work item.

    Movement is forward-only: UNASSIGNED -> ASSIGNED -> IN_PROGRESS -> DONE.
    There is deliberately no operation anywhere in the API that moves a task
    backwards, so the invariant is enforced by absence rather than by a guard
    that could be bypassed.
    """

    UNASSIGNED = "UNASSIGNED"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"


class Task(Base):
    """A unit of work, and the current-state projection of its history.

    `status` and `assignee_id` answer "where does this stand and whose is it
    right now"; the append-only AssignmentEvent rows answer "how did it get
    here, and was the assignee actually told".
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text, default=None)
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, native_enum=False),
        default=TaskStatus.UNASSIGNED,
        index=True,
    )

    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    assignee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), default=None, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow
    )
    assigned_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, default=None
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        UtcDateTime, default=None
    )

    # Two foreign keys point at users, so each relationship has to say which.
    creator: Mapped["User"] = relationship(
        foreign_keys=[creator_id], lazy="joined"
    )
    assignee: Mapped[Optional["User"]] = relationship(
        foreign_keys=[assignee_id], lazy="joined"
    )
    assignment_events: Mapped[list["AssignmentEvent"]] = relationship(
        back_populates="task",
        order_by="AssignmentEvent.assigned_at.desc()",
        cascade="all, delete-orphan",
    )
    status_change_events: Mapped[list["StatusChangeEvent"]] = relationship(
        back_populates="task",
        order_by="StatusChangeEvent.changed_at.desc()",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Task id={self.id} status={self.status.value} assignee_id={self.assignee_id}>"
