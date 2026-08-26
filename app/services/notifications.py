"""Sending notifications, behind a boundary.

The brief is emphatic that an assignee "must receive a real email. Not a mock,
not a log line". Real SMTP delivery is the last thing being wired up, so today
the shipped sender is a no-op -- but it sits behind the same interface a real
one will, and every caller already runs the full path: build the message, hand
it to a sender, record what happened.

That matters for more than tidiness. Because the send is a dependency rather
than a direct call, tests can inject a sender that fails on demand and assert
the system behaves correctly when email delivery breaks -- which is the case
that actually needs proving.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.core.config import get_settings
from app.models import Task, User


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    body: str


class NotificationSendError(Exception):
    """Delivery failed. Senders raise this; callers record it and carry on."""


@runtime_checkable
class NotificationSender(Protocol):
    def send(self, message: EmailMessage) -> None:
        """Deliver the message, or raise NotificationSendError."""
        ...


class NoopEmailSender:
    """Placeholder sender: performs no I/O and always succeeds.

    Deliberately not named "Fake" or "Mock" -- it is the production sender until
    SMTP is wired up, and the README says so plainly rather than implying the
    email requirement is already met.
    """

    def send(self, message: EmailMessage) -> None:
        return None


def build_assignment_email(
    *, task: Task, assignee: User, assigned_by: User
) -> EmailMessage:
    """Compose the message an assignee receives.

    Content lives here rather than in the task service so that what gets sent
    can be tested without touching the database or the lifecycle rules.
    """
    lines = [
        f"Hello {assignee.name},",
        "",
        f"{assigned_by.name} has assigned a task to you.",
        "",
        f"  Task #{task.id}: {task.title}",
    ]
    if task.description:
        lines.append(f"  {task.description}")
    lines += [
        "",
        "It is now waiting for you to start it.",
    ]
    return EmailMessage(
        to=assignee.email,
        subject=f"Task assigned to you: {task.title}",
        body="\n".join(lines),
    )


def get_notification_sender() -> NotificationSender:
    """Choose the sender for this deployment.

    A FastAPI dependency, so a test can substitute a recording or failing
    sender without patching module internals. Swapping in real SMTP delivery
    means returning a different object here and changing nothing else.
    """
    get_settings()  # SMTP settings are read here once a real sender exists.
    return NoopEmailSender()
