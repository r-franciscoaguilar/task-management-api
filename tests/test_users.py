"""Tests for the user discovery endpoints, against the real application."""

from fastapi.testclient import TestClient

from app.models import User

from .conftest import auth


def test_me_returns_the_caller(client: TestClient, users: dict[str, User]) -> None:
    worker = users["worker"]
    response = client.get("/users/me", headers=auth(worker))

    assert response.status_code == 200
    assert response.json() == {
        "id": worker.id,
        "name": worker.name,
        "email": worker.email,
        "role": "WORKER",
    }


def test_me_reflects_a_manager_caller(
    client: TestClient, users: dict[str, User]
) -> None:
    response = client.get("/users/me", headers=auth(users["manager"]))
    assert response.json()["role"] == "MANAGER"


def test_me_requires_identity(client: TestClient) -> None:
    response = client.get("/users/me")
    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"


def test_list_returns_everyone_in_id_order(
    client: TestClient, users: dict[str, User]
) -> None:
    response = client.get("/users", headers=auth(users["manager"]))

    assert response.status_code == 200
    body = response.json()
    assert [person["id"] for person in body] == sorted(
        user.id for user in users.values()
    )


def test_list_is_a_plain_array_not_an_envelope(
    client: TestClient, users: dict[str, User]
) -> None:
    """Users are a bounded reference set; only task lists are paginated."""
    body = client.get("/users", headers=auth(users["manager"])).json()
    assert isinstance(body, list)


def test_list_filters_by_role(client: TestClient, users: dict[str, User]) -> None:
    body = client.get(
        "/users", params={"role": "WORKER"}, headers=auth(users["manager"])
    ).json()

    assert {person["role"] for person in body} == {"WORKER"}
    assert {person["id"] for person in body} == {
        users["worker"].id,
        users["other_worker"].id,
    }


def test_workers_may_also_read_the_directory(
    client: TestClient, users: dict[str, User]
) -> None:
    """A deliberate choice, documented in the README: not manager-only."""
    assert client.get("/users", headers=auth(users["worker"])).status_code == 200


def test_list_requires_identity(client: TestClient) -> None:
    assert client.get("/users").status_code == 401


def test_unknown_role_is_rejected_in_the_standard_envelope(
    client: TestClient, users: dict[str, User]
) -> None:
    response = client.get(
        "/users", params={"role": "SUPERVISOR"}, headers=auth(users["manager"])
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "validation_error"
    assert "details" in body
    assert "detail" not in body


def test_role_filter_is_case_sensitive(
    client: TestClient, users: dict[str, User]
) -> None:
    """Documented behaviour: the filter takes the canonical uppercase value."""
    response = client.get(
        "/users", params={"role": "worker"}, headers=auth(users["manager"])
    )
    assert response.status_code == 422


def test_no_password_or_internal_fields_are_exposed(
    client: TestClient, users: dict[str, User]
) -> None:
    """The response schema is explicit, so new model columns cannot leak."""
    body = client.get("/users", headers=auth(users["manager"])).json()
    assert set(body[0]) == {"id", "name", "email", "role"}
