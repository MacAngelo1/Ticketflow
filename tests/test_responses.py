from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app


def test_all_remaining_read_routes_and_cancellation(client, actors, inventory):
    event, _, _ = inventory()
    for path, actor in [
        ("/health", "buyer"),
        ("/events/mine", "owner"),
        (f"/events/{event}", "buyer"),
        (f"/events/{event}/ticket-types", "buyer"),
        (f"/events/{event}/seats", "buyer"),
        (f"/events/{event}/sales", "owner"),
        ("/tickets/me", "buyer"),
        ("/admin/users", "admin"),
        ("/admin/events", "admin"),
        ("/admin/reservations", "admin"),
        ("/admin/settings", "admin"),
    ]:
        response = client.get(path, headers=actors[actor])
        assert response.status_code == 200, response.text
        assert "password_hash" not in response.text
    cancelled = client.delete(f"/events/{event}", headers=actors["owner"])
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "cancelled"


def test_errors_keep_status_headers_and_safe_validation_data(client, actors):
    missing = client.get("/auth/me")
    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    assert missing.json()["data"] is None
    assert client.get("/no-such-route").status_code == 404
    assert client.post("/health").status_code == 405
    assert client.get(f"/tickets/{uuid4()}/qr", headers=actors["buyer"]).status_code == 404
    invalid = client.post(
        "/auth/register",
        json={
            "email": "not-an-email",
            "password": "secret",
            "full_name": "Test",
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["data"]["errors"]
    assert "secret" not in invalid.text
    assert all(set(error) == {"loc", "msg", "type"} for error in invalid.json()["data"]["errors"])


def test_unhandled_errors_have_a_safe_envelope():
    def broken_database():
        raise RuntimeError("private database connection information")

    app.dependency_overrides[get_db] = broken_database
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/events/")
        assert response.status_code == 500
        assert response.json() == {
            "message": "Internal server error.",
            "data": None,
            "status_code": 500,
            "success": False,
        }
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_openapi_documents_envelopes_and_bearer_login():
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
        assert client.get("/docs").status_code == 200
    schemes = schema["components"]["securitySchemes"]
    assert schemes["HTTPBearer"]["scheme"] == "bearer"
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            for status, response in operation["responses"].items():
                if status.startswith("2") or status == "422":
                    ref = response["content"]["application/json"]["schema"]["$ref"]
                    model = schema["components"]["schemas"][ref.rsplit("/", 1)[1]]
                    assert set(model["required"]) == {"message", "data", "status_code", "success"}
