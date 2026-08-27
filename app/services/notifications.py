"""Sending notifications, behind a boundary.

The brief is emphatic that an assignee "must receive a real email. Not a mock,
not a log line", so the default sender opens an SMTP connection and delivers.
NoopEmailSender exists only for deliberately switching email off; it is not the
default, and nothing in the normal path pretends to send.

Delivery sits behind a one-method protocol resolved as a FastAPI dependency.
That is what lets tests inject a sender that fails on demand and assert the
system stays correct when email breaks -- the case that actually needs proving,
and one that cannot be exercised against a real mail server.
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
    """Discards messages without sending them.

    Only selected by EMAIL_BACKEND=noop, for when email must be switched off on
    purpose -- a demo without a mail server, or a load test. It reports success,
    so it must never be the default: that would make every notification record
    read SENT while nothing was delivered.
    """

    def send(self, message: EmailMessage) -> None:
        return None


class SmtpEmailSender:
    """Delivers over SMTP using the standard library.

    A connection is opened per message. That is wasteful at volume, and the
    right fix is not connection pooling but moving delivery off the request
    path entirely -- see the README's evolution notes.
    """

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

        Every failure mode is translated into NotificationSendError so callers
        depend on this module's contract rather than on smtplib's exception
        hierarchy. The message text names the host and port, because
        "connection refused" on its own tells an operator nothing about which
        server was unreachable.
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


class RedirectingSender:
    """Wraps another sender and diverts every message to one address.

    Composes with any backend rather than being built into the SMTP sender, so
    the redirect rule is one small testable thing and does not complicate
    delivery.

    The original recipient is preserved in the subject line. Without that, a
    redirected inbox is a pile of messages with no way to tell who each was
    meant for -- which defeats the point of testing with it.
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
    """Construct the sender described by the given settings.

    Kept separate from the dependency below so it can be tested directly --
    get_notification_sender takes no arguments on purpose, since FastAPI would
    otherwise try to bind any parameter to the request.
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
    """The sender for this deployment, as a FastAPI dependency.

    Being a dependency is what allows a test to substitute a recording or
    failing sender without patching module internals.
    """
    return build_sender(get_settings())
