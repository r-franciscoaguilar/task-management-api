"""Real SMTP delivery.

The brief insists on "a real email. Not a mock, not a log line", so these tests
run an actual SMTP server in-process and assert the message arrives over a
socket. Asserting that `.send()` was called would prove nothing about whether
anything is delivered.
"""

import os
import socket
from collections.abc import Callable, Iterator

import pytest
from aiosmtpd.controller import Controller

from app.core.config import Settings
from app.services.notifications import (
    EmailMessage,
    NoopEmailSender,
    NotificationSendError,
    RedirectingSender,
    SmtpEmailSender,
    build_sender,
)


class _Collector:
    """Minimal aiosmtpd handler that keeps what it receives."""

    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def handle_DATA(self, server, session, envelope) -> str:  # noqa: N802
        self.messages.append(
            {
                "from": envelope.mail_from,
                "to": list(envelope.rcpt_tos),
                "raw": envelope.content.decode("utf-8", errors="replace"),
            }
        )
        return "250 Message accepted"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("localhost", 0))
        return int(probe.getsockname()[1])


@pytest.fixture()
def settings_factory(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Settings]:
    """Build Settings without the developer's .env or shell environment.

    Settings reads .env by default, so without this a real SMTP configuration
    on the machine would change what these tests observe -- putting working
    credentials in .env would start failing tests about defaults. Config tests
    must describe the code, not the developer's setup.
    """
    for key in list(os.environ):
        if key.startswith(("SMTP_", "EMAIL_", "NOTIFY_")):
            monkeypatch.delenv(key, raising=False)

    def _build(**overrides: object) -> Settings:
        return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]

    return _build


@pytest.fixture()
def smtp_server() -> Iterator[tuple[_Collector, int]]:
    collector = _Collector()
    port = _free_port()
    controller = Controller(collector, hostname="localhost", port=port)
    controller.start()
    try:
        yield collector, port
    finally:
        controller.stop()


@pytest.fixture()
def sender_to(smtp_server: tuple[_Collector, int]) -> SmtpEmailSender:
    _, port = smtp_server
    return SmtpEmailSender(
        host="localhost",
        port=port,
        from_address="noreply@task-api.local",
        timeout=5.0,
    )


# --- delivery over a socket ----------------------------------------------


def test_message_actually_arrives(
    sender_to: SmtpEmailSender, smtp_server: tuple[_Collector, int]
) -> None:
    collector, _ = smtp_server
    sender_to.send(
        EmailMessage(
            to="carol@example.com",
            subject="Task assigned to you: Audit safety signage",
            body="Hello Carol,\n\nAlice has assigned a task to you.",
        )
    )

    assert len(collector.messages) == 1
    received = collector.messages[0]
    assert received["to"] == ["carol@example.com"]
    assert received["from"] == "noreply@task-api.local"


def test_envelope_and_headers_are_populated(
    sender_to: SmtpEmailSender, smtp_server: tuple[_Collector, int]
) -> None:
    collector, _ = smtp_server
    sender_to.send(
        EmailMessage(to="dave@example.com", subject="Subject line", body="Body text")
    )

    raw = str(collector.messages[0]["raw"])
    assert "To: dave@example.com" in raw
    assert "From: noreply@task-api.local" in raw
    assert "Subject: Subject line" in raw
    assert "Body text" in raw


def test_each_send_delivers_separately(
    sender_to: SmtpEmailSender, smtp_server: tuple[_Collector, int]
) -> None:
    collector, _ = smtp_server
    for address in ("carol@example.com", "dave@example.com"):
        sender_to.send(EmailMessage(to=address, subject="Ping", body="Body"))

    assert [message["to"] for message in collector.messages] == [
        ["carol@example.com"],
        ["dave@example.com"],
    ]


def test_a_unicode_body_survives_the_round_trip(
    sender_to: SmtpEmailSender, smtp_server: tuple[_Collector, int]
) -> None:
    """Task titles come from users, so non-ASCII must not break delivery."""
    collector, _ = smtp_server
    sender_to.send(
        EmailMessage(
            to="erin@example.com",
            subject="Recalibración de la báscula",
            body="Revisar la báscula — deriva de 2kg",
        )
    )
    assert len(collector.messages) == 1


# --- failure translation --------------------------------------------------


def test_connection_refused_becomes_a_notification_error() -> None:
    """Callers depend on this module's contract, not on smtplib's exceptions."""
    sender = SmtpEmailSender(
        host="localhost",
        port=_free_port(),  # nothing is listening here
        from_address="noreply@task-api.local",
        timeout=2.0,
    )

    with pytest.raises(NotificationSendError) as caught:
        sender.send(EmailMessage(to="carol@example.com", subject="s", body="b"))

    # The message must name the server, or "connection refused" tells an
    # operator nothing about which one was unreachable.
    assert "localhost" in str(caught.value)
    assert "carol@example.com" in str(caught.value)


def test_an_unresolvable_host_becomes_a_notification_error() -> None:
    sender = SmtpEmailSender(
        host="smtp.invalid-host-that-does-not-exist.example",
        port=25,
        from_address="noreply@task-api.local",
        timeout=2.0,
    )
    with pytest.raises(NotificationSendError):
        sender.send(EmailMessage(to="carol@example.com", subject="s", body="b"))


# --- backend selection ----------------------------------------------------


def test_smtp_is_the_default_backend(
    settings_factory: Callable[..., Settings]
) -> None:
    """A no-op default would record every notification as SENT while nothing
    was delivered, which is worse than failing loudly."""
    assert isinstance(build_sender(settings_factory()), SmtpEmailSender)


def test_noop_backend_can_be_selected_explicitly(
    settings_factory: Callable[..., Settings]
) -> None:
    assert isinstance(
        build_sender(settings_factory(email_backend="noop")), NoopEmailSender
    )


def test_settings_are_carried_into_the_sender(
    settings_factory: Callable[..., Settings]
) -> None:
    sender = build_sender(
        settings_factory(
            smtp_host="mail.example.com",
            smtp_port=587,
            smtp_username="apikey",
            smtp_password="secret",
            smtp_from_address="tasks@example.com",
            smtp_use_tls=True,
            smtp_timeout=3.5,
        )
    )
    assert isinstance(sender, SmtpEmailSender)
    assert (sender.host, sender.port, sender.use_tls) == (
        "mail.example.com",
        587,
        True,
    )
    assert sender.from_address == "tasks@example.com"
    assert sender.timeout == 3.5


# --- end to end through the API ------------------------------------------


def test_assigning_through_the_api_delivers_a_real_email(
    override_get_db, users, make_task, smtp_server, sender_to
) -> None:
    """The whole path: HTTP request in, message out over SMTP.

    This is the test that actually answers the brief's requirement. Every other
    assignment test substitutes a recording sender, which proves the plumbing
    but not that anything leaves the process.
    """
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app
    from app.services.notifications import get_notification_sender

    from .conftest import auth

    collector, _ = smtp_server
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_notification_sender] = lambda: sender_to
    try:
        client = TestClient(app)
        task = make_task(creator=users["manager"], title="Audit safety signage")

        response = client.post(
            f"/tasks/{task.id}/assign",
            json={"assignee_id": users["worker"].id},
            headers=auth(users["manager"]),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["latest_assignment"]["notification_status"] == "SENT"

    assert len(collector.messages) == 1
    delivered = collector.messages[0]
    assert delivered["to"] == [users["worker"].email]

    raw = str(delivered["raw"])
    assert "Audit safety signage" in raw       # what the work is
    assert users["worker"].name in raw          # who it is for
    assert users["manager"].name in raw         # who assigned it


def test_a_dead_mail_server_records_failure_without_losing_the_assignment(
    override_get_db, users, make_task
) -> None:
    """The realistic operational failure, exercised against a real socket
    rather than a sender told to raise."""
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app
    from app.services.notifications import get_notification_sender

    from .conftest import auth

    dead = SmtpEmailSender(
        host="localhost",
        port=_free_port(),  # nothing listening
        from_address="noreply@task-api.local",
        timeout=2.0,
    )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_notification_sender] = lambda: dead
    try:
        client = TestClient(app)
        task = make_task(creator=users["manager"], title="Replace filter")

        response = client.post(
            f"/tasks/{task.id}/assign",
            json={"assignee_id": users["worker"].id},
            headers=auth(users["manager"]),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ASSIGNED"
    assert body["assignee"]["id"] == users["worker"].id

    assignment = body["latest_assignment"]
    assert assignment["notification_status"] == "FAILED"
    assert "SMTP" in assignment["notification_error"]
    assert assignment["notification_sent_at"] is None


# --- redirecting every message to one address ----------------------------


def test_redirect_sends_to_the_override_address(
    smtp_server: tuple[_Collector, int],
    settings_factory: Callable[..., Settings],
) -> None:
    collector, port = smtp_server
    sender = build_sender(
        settings_factory(
            smtp_host="localhost",
            smtp_port=port,
            smtp_timeout=5.0,
            notify_override_address="me@example.com",
        )
    )

    sender.send(
        EmailMessage(to="carol@example.com", subject="Fix the pump", body="Body")
    )

    assert collector.messages[0]["to"] == ["me@example.com"]


def test_redirect_keeps_the_intended_recipient_in_the_subject(
    smtp_server: tuple[_Collector, int],
    settings_factory: Callable[..., Settings],
) -> None:
    """Otherwise a redirected inbox is messages with no way to tell who each
    was meant for, which defeats the point of testing with it."""
    collector, port = smtp_server
    sender = build_sender(
        settings_factory(
            smtp_host="localhost",
            smtp_port=port,
            smtp_timeout=5.0,
            notify_override_address="me@example.com",
        )
    )

    sender.send(
        EmailMessage(to="carol@example.com", subject="Fix the pump", body="Body")
    )

    raw = str(collector.messages[0]["raw"])
    assert "Subject: [to: carol@example.com] Fix the pump" in raw


def test_redirect_wraps_whichever_backend_is_configured(
    settings_factory: Callable[..., Settings]
) -> None:
    """The redirect composes rather than being built into the SMTP sender."""
    wrapped = build_sender(
        settings_factory(
            email_backend="noop", notify_override_address="me@example.com"
        )
    )
    assert isinstance(wrapped, RedirectingSender)
    assert isinstance(wrapped.inner, NoopEmailSender)


def test_no_redirect_when_the_setting_is_absent(
    settings_factory: Callable[..., Settings]
) -> None:
    assert not isinstance(build_sender(settings_factory()), RedirectingSender)


def test_redirect_does_not_alter_the_body(
    smtp_server: tuple[_Collector, int],
    settings_factory: Callable[..., Settings],
) -> None:
    collector, port = smtp_server
    build_sender(
        settings_factory(
            smtp_host="localhost",
            smtp_port=port,
            smtp_timeout=5.0,
            notify_override_address="me@example.com",
        )
    ).send(
        EmailMessage(to="carol@example.com", subject="s", body="Task #1: Fix the pump")
    )

    assert "Task #1: Fix the pump" in str(collector.messages[0]["raw"])
