"""Shared test infrastructure.

Each test gets a fresh in-memory SQLite database. StaticPool is required: every
new connection to ":memory:" otherwise gets its own empty database, so the pool
must hand the same one to the app, the fixtures, and the assertions.
"""

from collections.abc import Callable, Generator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.time import utcnow
from app.db import Base
from app.models import Task, TaskStatus, User, UserRole
from app.services.notifications import EmailMessage, NotificationSendError


@pytest.fixture()
def engine() -> Iterator[Engine]:
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """A session for arranging fixtures and asserting on stored state."""
    with session_factory() as session:
        yield session


@pytest.fixture()
def override_get_db(
    session_factory: sessionmaker[Session],
) -> Callable[[], Generator[Session, None, None]]:
    """Drop-in replacement for app.db.get_db, bound to the test database."""

    def _get_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    return _get_db


@pytest.fixture()
def users(db: Session) -> dict[str, User]:
    """One of each role, plus a second worker. Independent of app/seed.py so
    tests do not break when demo data changes."""
    people = {
        "manager": User(name="Alice", email="alice@example.com", role=UserRole.MANAGER),
        "worker": User(name="Carol", email="carol@example.com", role=UserRole.WORKER),
        "other_worker": User(name="Dave", email="dave@example.com", role=UserRole.WORKER),
    }
    db.add_all(people.values())
    db.commit()
    return people


def auth(user: User) -> dict[str, str]:
    """Headers that identify `user` as the caller."""
    return {"X-User-Id": str(user.id)}


class RecordingSender:
    """Remembers what it was asked to send. Set `fail_with` to make delivery
    raise, which is how the failure path gets tested at all."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self.fail_with: Exception | None = None

    def send(self, message: EmailMessage) -> None:
        self.sent.append(message)
        if self.fail_with is not None:
            raise self.fail_with

    def fail(self, message: str = "SMTP connection refused") -> None:
        self.fail_with = NotificationSendError(message)


@pytest.fixture()
def sender() -> RecordingSender:
    return RecordingSender()


@pytest.fixture()
def client(
    override_get_db: Callable[[], Generator[Session, None, None]],
    sender: RecordingSender,
) -> Iterator[TestClient]:
    """The real application, wired to the test database.

    TestClient is used *without* its context manager on purpose: entering it
    would run the lifespan, which calls create_all and seed_if_empty against the
    real engine -- writing to the developer's app.db during a test run.
    """
    from app.db import get_db
    from app.main import app
    from app.services.notifications import get_notification_sender

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_notification_sender] = lambda: sender
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def make_task(db: Session) -> Callable[..., Task]:
    """Build a task directly in the database, so a bug in one endpoint cannot
    break unrelated tests' setup."""

    def _make(
        *,
        creator: User,
        title: str = "Some work",
        description: str | None = None,
        assignee: User | None = None,
        status: TaskStatus = TaskStatus.UNASSIGNED,
    ) -> Task:
        task = Task(
            title=title,
            description=description,
            creator_id=creator.id,
            assignee_id=assignee.id if assignee else None,
            status=status,
            assigned_at=utcnow() if assignee else None,
        )
        db.add(task)
        db.commit()
        return task

    return _make
