"""Shared test infrastructure.

Each test gets a fresh in-memory SQLite database. StaticPool is what makes an
in-memory DB usable here: by default every new connection to ":memory:" gets
its own empty database, so the pool must hand out one single connection to the
app, the fixtures, and the assertions alike.
"""

from collections.abc import Callable, Generator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import User, UserRole


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
    """A minimal cast: one of each role, plus a second worker.

    Deliberately smaller and independent of app/seed.py -- tests should not
    break when the demo seed data changes.
    """
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


@pytest.fixture()
def client(
    override_get_db: Callable[[], Generator[Session, None, None]],
) -> Iterator[TestClient]:
    """The real application, wired to the test database.

    TestClient is used *without* its context manager on purpose. Entering the
    context would run the app's lifespan, which calls create_all and
    seed_if_empty against the real engine -- writing to the developer's app.db
    from a test run. Tables here are created by the `engine` fixture instead,
    and tests arrange their own data.
    """
    from app.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
