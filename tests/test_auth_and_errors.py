"""Tests for the identity/authorization layer and the error envelope.

These mount the real dependencies on a purpose-built app rather than on the
production one, because at this point no business endpoint exists yet. The
dependencies and exception handlers under test are the real ones.
"""

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import CurrentUser, ManagerUser, WorkerUser
from app.exceptions import (
    ConflictError,
    InvalidStateTransitionError,
    NotFoundError,
    register_exception_handlers,
)
from app.models import User

from .conftest import auth


@pytest.fixture()
def client(override_get_db: Callable[..., object]) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.dependency_overrides[get_db] = override_get_db

    @app.get("/whoami")
    def whoami(user: CurrentUser) -> dict[str, object]:
        return {"id": user.id, "role": user.role.value}

    @app.get("/manager-only")
    def manager_only(user: ManagerUser) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/worker-only")
    def worker_only(user: WorkerUser) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/boom-conflict")
    def boom_conflict() -> None:
        raise ConflictError("Something already happened.")

    @app.get("/boom-transition")
    def boom_transition() -> None:
        raise InvalidStateTransitionError(
            "Cannot start a task that is not ASSIGNED.", current_status="IN_PROGRESS"
        )

    @app.get("/boom-notfound")
    def boom_notfound() -> None:
        raise NotFoundError("No such task.")

    return TestClient(app)


# --- identity resolution -------------------------------------------------


def test_valid_header_resolves_caller(
    client: TestClient, users: dict[str, User]
) -> None:
    response = client.get("/whoami", headers=auth(users["worker"]))
    assert response.status_code == 200
    assert response.json() == {"id": users["worker"].id, "role": "WORKER"}


def test_missing_header_is_unauthenticated(client: TestClient) -> None:
    response = client.get("/whoami")
    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"


def test_blank_header_is_unauthenticated(client: TestClient) -> None:
    response = client.get("/whoami", headers={"X-User-Id": "   "})
    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"


def test_non_numeric_header_is_unauthenticated_not_422(client: TestClient) -> None:
    """A malformed credential and an unknown one must fail the same way."""
    response = client.get("/whoami", headers={"X-User-Id": "bogus"})
    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"


def test_unknown_user_id_is_unauthenticated(
    client: TestClient, users: dict[str, User]
) -> None:
    response = client.get("/whoami", headers={"X-User-Id": "999999"})
    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"


# --- role guards ---------------------------------------------------------


def test_manager_passes_manager_guard(
    client: TestClient, users: dict[str, User]
) -> None:
    assert client.get("/manager-only", headers=auth(users["manager"])).status_code == 200


def test_worker_rejected_by_manager_guard(
    client: TestClient, users: dict[str, User]
) -> None:
    response = client.get("/manager-only", headers=auth(users["worker"]))
    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "manager_role_required"
    assert body["required_role"] == "MANAGER"
    assert body["actual_role"] == "WORKER"


def test_worker_passes_worker_guard(
    client: TestClient, users: dict[str, User]
) -> None:
    assert client.get("/worker-only", headers=auth(users["worker"])).status_code == 200


def test_manager_rejected_by_worker_guard(
    client: TestClient, users: dict[str, User]
) -> None:
    """Managers must not act on a worker's behalf, per the brief."""
    response = client.get("/worker-only", headers=auth(users["manager"]))
    assert response.status_code == 403
    assert response.json()["error"] == "worker_role_required"


def test_role_guards_still_require_identity(client: TestClient) -> None:
    """Missing identity fails as 401, not as a role failure."""
    assert client.get("/manager-only").status_code == 401


# --- error envelope ------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/boom-conflict", 409, "conflict"),
        ("/boom-transition", 409, "invalid_state_transition"),
        ("/boom-notfound", 404, "not_found"),
    ],
)
def test_domain_errors_map_to_status_and_code(
    client: TestClient, path: str, status: int, code: str
) -> None:
    response = client.get(path)
    assert response.status_code == status
    body = response.json()
    assert body["error"] == code
    assert isinstance(body["message"], str) and body["message"]


def test_transition_error_carries_current_status(client: TestClient) -> None:
    """Context fields let a client explain the failure and reconcile state."""
    body = client.get("/boom-transition").json()
    assert body["current_status"] == "IN_PROGRESS"


def test_unmatched_route_uses_the_same_envelope(client: TestClient) -> None:
    body = client.get("/no-such-route").json()
    assert body["error"] == "not_found"
    assert "message" in body


def test_framework_validation_errors_use_the_same_envelope(
    override_get_db: Callable[..., object],
) -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/needs-int")
    def needs_int(count: int) -> dict[str, int]:
        return {"count": count}

    response = TestClient(app).get("/needs-int", params={"count": "abc"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_error"
    assert "details" in body


def test_error_bodies_never_use_fastapis_detail_key(client: TestClient) -> None:
    """One envelope everywhere: clients should never need to parse two shapes."""
    for path in ("/boom-conflict", "/no-such-route", "/whoami"):
        body = client.get(path).json()
        assert "detail" not in body
