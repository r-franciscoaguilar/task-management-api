"""Email delivery, behind a one-method protocol.

The protocol is resolved as a FastAPI dependency so tests can inject a sender
that fails on demand -- the failure path cannot be exercised against a working
mail server.
"""

import smtplib
from dataclasses import dataclass, replace
from email.message import EmailMessage as MimeMessage
from typing import Protocol, runtime_checkable

from app.core.config import Settings, get_settings
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
    """Discards messages. Only for EMAIL_BACKEND=noop.

    Never the default: reporting success without delivering would make every
    notification record read SENT while nothing was sent.
    """

    def send(self, message: EmailMessage) -> None:
        return None


class SmtpEmailSender:
    """Delivers over SMTP. One connection per message; see README for why that
    is acceptable here and what would replace it."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        from_address: str,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = False,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.from_address = from_address
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.timeout = timeout

    def _build(self, message: EmailMessage) -> MimeMessage:
        mime = MimeMessage()
        mime["From"] = self.from_address
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.body)
        return mime

    def send(self, message: EmailMessage) -> None:
        """Deliver one message, or raise NotificationSendError.

        Failures are translated so callers depend on this module rather than on
        smtplib's exception hierarchy. The text names host and port: "connection
        refused" alone does not say which server.
        """
        try:
            with smtplib.SMTP(
                host=self.host, port=self.port, timeout=self.timeout
            ) as smtp:
                if self.use_tls:
                    smtp.starttls()
                if self.username and self.password:
                    smtp.login(self.username, self.password)
                smtp.send_message(self._build(message))
        except (OSError, smtplib.SMTPException) as error:
            raise NotificationSendError(
                f"Could not deliver to {message.to} via SMTP at "
                f"{self.host}:{self.port} -- {error}"
            ) from error


def build_assignment_email(
    *, task: Task, assignee: User, assigned_by: User
) -> EmailMessage:
    """Compose the message an assignee receives.

    Kept out of the task service so content is testable without a database.
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


class RedirectingSender:
    """Diverts every message to one address, keeping the intended recipient in
    the subject.

    A wrapper rather than a flag inside SmtpEmailSender, so it composes with
    either backend.
    """

    def __init__(self, inner: NotificationSender, address: str) -> None:
        self.inner = inner
        self.address = address

    def send(self, message: EmailMessage) -> None:
        self.inner.send(
            replace(
                message,
                to=self.address,
                subject=f"[to: {message.to}] {message.subject}",
            )
        )


def build_sender(settings: Settings) -> NotificationSender:
    """Construct the sender for these settings.

    Separate from the dependency below so it is directly testable:
    get_notification_sender must take no arguments, or FastAPI would try to
    bind them to the request.
    """
    sender: NotificationSender
    if settings.email_backend == "noop":
        sender = NoopEmailSender()
    else:
        sender = SmtpEmailSender(
            host=settings.smtp_host,
            port=settings.smtp_port,
            from_address=settings.smtp_from_address,
            username=settings.smtp_username,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            timeout=settings.smtp_timeout,
        )

    if settings.notify_override_address:
        sender = RedirectingSender(sender, settings.notify_override_address)

    return sender


def get_notification_sender() -> NotificationSender:
    """The sender for this deployment, as an overridable dependency."""
    return build_sender(get_settings())
