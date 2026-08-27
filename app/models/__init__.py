"""Importing every model here registers it on Base.metadata, so callers of
create_all only need to import this package."""

from app.models.assignment_event import AssignmentEvent, NotificationStatus
from app.models.status_change_event import StatusChangeEvent
from app.models.task import Task, TaskStatus
from app.models.user import User, UserRole

__all__ = [
    "AssignmentEvent",
    "NotificationStatus",
    "StatusChangeEvent",
    "Task",
    "TaskStatus",
    "User",
    "UserRole",
]
