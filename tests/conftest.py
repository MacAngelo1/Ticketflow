import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pytest

test_url = os.getenv("TEST_DATABASE_URL", "")
if not test_url or not urlparse(test_url).path.endswith("_test"):
    raise RuntimeError(
        "Set TEST_DATABASE_URL to a dedicated PostgreSQL database ending in _test. Tests erase its tables."
    )
os.environ["DATABASE_URL"] = test_url
os.environ["SECRET_KEY"] = secrets.token_hex(32)
os.environ["RATE_LIMIT_ENABLED"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import User


@pytest.fixture
def client():
    with engine.begin() as connection:
        names = ", ".join('"' + table.name + '"' for table in Base.metadata.sorted_tables)
        connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def actors(client):
    result = {}
    for name, role in [
        ("owner", "organizer"),
        ("buyer", "customer"),
        ("other", "customer"),
        ("admin", "customer"),
    ]:
        response = client.post(
            "/auth/register",
            json={
                "email": f"{name}@example.com",
                "full_name": name,
                "password": "Password123!",
                "role": role,
            },
        )
        assert response.status_code == 201, response.text
        assert "password_hash" not in response.json()
        token = client.post(
            "/auth/token", data={"username": f"{name}@example.com", "password": "Password123!"}
        )
        result[name] = {"Authorization": "Bearer " + token.json()["access_token"]}
    with SessionLocal.begin() as db:
        db.scalar(select(User).where(User.email == "admin@example.com")).role = "admin"
    return result


@pytest.fixture
def inventory(client, actors):
    def make(seated=True, quantity=2, hours=24):
        event = client.post(
            "/events/",
            headers=actors["owner"],
            json={
                "name": "Test event",
                "venue": "Lagos",
                "starts_at": (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(),
                "is_seated": seated,
            },
        )
        assert event.status_code == 201, event.text
        event_id = event.json()["id"]
        item = client.post(
            f"/events/{event_id}/ticket-types",
            headers=actors["owner"],
            json={"name": "Regular", "price": "1500.50", "quantity": quantity},
        )
        assert item.status_code == 201, item.text
        item_id = item.json()["id"]
        seats = []
        if seated:
            response = client.post(
                f"/events/{event_id}/seats",
                headers=actors["owner"],
                json={"ticket_type_id": item_id, "labels": [f"A{i + 1}" for i in range(quantity)]},
            )
            assert response.status_code == 201, response.text
            seats = [seat["id"] for seat in response.json()]
        approved = client.patch(
            f"/admin/events/{event_id}/moderation",
            headers=actors["admin"],
            json={"status": "active", "reason": "Verified event details."},
        )
        assert approved.status_code == 200, approved.text
        return event_id, item_id, seats

    return make
