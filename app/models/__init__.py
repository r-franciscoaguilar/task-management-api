"""Importing every model here registers it on Base.metadata.

Anything that calls create_all (app startup, test fixtures) only needs to
import this package for the full schema to exist.
"""

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
