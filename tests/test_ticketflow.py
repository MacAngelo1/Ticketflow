from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import Reservation, User
from app.security import password_hasher, sign_token


def reserve(client, headers, item, seats=(), key="order-key-0001", quantity=1):
    return client.post(
        "/reservations/",
        headers={**headers, "Idempotency-Key": key},
        json={"ticket_type_id": item, "quantity": quantity, "seat_ids": list(seats)},
    )


def purchase(client, actors, item, seats=()):
    order = reserve(client, actors["buyer"], item, seats)
    assert order.status_code == 201, order.text
    order_id = order.json()["data"]["id"]
    payment = client.post(
        f"/reservations/{order_id}/pay-demo", headers=actors["buyer"], json={"outcome": "success"}
    )
    assert payment.status_code == 200, payment.text
    tickets = client.post(f"/reservations/{order_id}/confirm", headers=actors["buyer"])
    assert tickets.status_code == 200, tickets.text
    return order_id, tickets.json()["data"]


def test_auth_validation_and_roles(client, actors):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/admin/users", headers=actors["buyer"]).status_code == 403
    assert (
        client.post(
            "/auth/register",
            json={
                "email": "x@example.com",
                "full_name": "X",
                "password": "Password123!",
                "role": "admin",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/auth/register",
            json={"email": "buyer@example.com", "full_name": "X", "password": "Password123!"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/auth/token", data={"username": "buyer@example.com", "password": "wrong"}
        ).status_code
        == 401
    )
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "buyer@example.com"))
        assert user.password_hash != "Password123!"
        assert password_hasher.verify("Password123!", user.password_hash)
        token = sign_token(user.id, "access", datetime.now(timezone.utc) - timedelta(seconds=1))
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401
    bad_event = {"name": "X", "venue": "Lagos", "starts_at": "2020-01-01T12:00:00+01:00"}
    assert client.post("/events/", headers=actors["owner"], json=bad_event).status_code == 422


def test_event_moderation_and_ownership(client, actors, inventory):
    event, item, _ = inventory(False)
    assert (
        client.put(f"/events/{event}", headers=actors["buyer"], json={"name": "No"}).status_code
        == 403
    )
    assert client.get(f"/events/{event}/sales", headers=actors["buyer"]).status_code == 403
    updated = client.put(f"/events/{event}", headers=actors["owner"], json={"name": "Revised"})
    assert updated.json()["data"]["status"] == "pending"
    assert client.get("/events/").json()["data"] == []
    assert reserve(client, actors["buyer"], item).status_code == 409
    rejected = client.patch(
        f"/admin/events/{event}/moderation",
        headers=actors["admin"],
        json={"status": "rejected", "reason": "Venue not confirmed."},
    )
    assert rejected.status_code == 200
    assert client.get("/admin/audit-logs", headers=actors["admin"]).json()["data"]


def test_purchase_retry_refund_and_qr(client, actors, inventory):
    event, item, seats = inventory()
    order_id, tickets = purchase(client, actors, item, [seats[0]])
    repeat = client.post(f"/reservations/{order_id}/confirm", headers=actors["buyer"])
    assert repeat.json()["data"] == tickets
    same = reserve(client, actors["buyer"], item, [seats[0]])
    assert same.json()["data"]["id"] == order_id
    assert reserve(client, actors["buyer"], item, [seats[1]]).status_code == 409
    assert reserve(client, actors["other"], item, [seats[0]]).status_code == 409
    qr = client.get(f"/tickets/{tickets[0]['id']}/qr", headers=actors["buyer"])
    assert qr.status_code == 200 and b64decode(
        qr.json()["data"]["content"], validate=True
    ).startswith(b"\x89PNG")
    assert client.get(f"/tickets/{tickets[0]['id']}/qr", headers=actors["other"]).status_code == 403
    assert (
        client.post(f"/reservations/{order_id}/confirm", headers=actors["other"]).status_code == 403
    )
    report = client.get("/admin/reports", headers=actors["admin"]).json()["data"]
    assert float(report["net_sales"]) == 1500.50
    refund = client.post(
        f"/admin/reservations/{order_id}/refund",
        headers=actors["admin"],
        json={"reason": "Customer requested cancellation."},
    )
    assert refund.json()["data"]["status"] == "refunded"
    assert (
        client.post(
            f"/admin/reservations/{order_id}/refund",
            headers=actors["admin"],
            json={"reason": "Retry after network issue."},
        ).status_code
        == 200
    )
    assert client.get(f"/tickets/{tickets[0]['id']}/qr", headers=actors["buyer"]).status_code == 409
    assert reserve(client, actors["other"], item, [seats[0]]).status_code == 201
    assert (
        float(client.get("/admin/reports", headers=actors["admin"]).json()["data"]["net_sales"])
        == 0
    )


def test_expiry_and_failed_payment(client, actors, inventory):
    _, item, _ = inventory(False, quantity=1)
    order = reserve(client, actors["buyer"], item).json()["data"]
    assert (
        client.post(f"/reservations/{order['id']}/confirm", headers=actors["buyer"]).status_code
        == 409
    )
    assert (
        client.post(
            f"/reservations/{order['id']}/pay-demo",
            headers=actors["buyer"],
            json={"outcome": "failure"},
        ).status_code
        == 402
    )
    with SessionLocal.begin() as db:
        db.get(Reservation, UUID(order["id"])).expires_at = datetime.now(timezone.utc) - timedelta(
            seconds=1
        )
    assert reserve(client, actors["other"], item).status_code == 201
    assert (
        client.post(
            f"/reservations/{order['id']}/pay-demo",
            headers=actors["buyer"],
            json={"outcome": "success"},
        ).status_code
        == 409
    )


def test_concurrent_same_seat(client, actors, inventory):
    _, item, seats = inventory(True, quantity=1)
    barrier = Barrier(2)

    def attempt(actor):
        with TestClient(app) as parallel:
            barrier.wait(timeout=10)
            return reserve(
                parallel, actors[actor], item, seats, key=f"race-seat-{actor}"
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["buyer", "other"]))
    assert sorted(results) == [201, 409]


def test_concurrent_last_general_ticket(client, actors, inventory):
    _, item, _ = inventory(False, quantity=1)
    barrier = Barrier(2)

    def attempt(actor):
        with TestClient(app) as parallel:
            barrier.wait(timeout=10)
            return reserve(parallel, actors[actor], item, key=f"race-stock-{actor}").status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ["buyer", "other"])) == [201, 409]


def test_seat_validation_and_cancel(client, actors, inventory):
    event, item, seats = inventory()
    assert reserve(client, actors["buyer"], item).status_code == 422
    assert (
        reserve(client, actors["buyer"], item, [seats[0], seats[0]], quantity=2).status_code == 422
    )
    _, other_item, other_seats = inventory()
    assert reserve(client, actors["buyer"], item, other_seats[:1]).status_code == 422
    order = reserve(client, actors["buyer"], item, seats[:1]).json()["data"]
    assert (
        client.post(f"/reservations/{order['id']}/cancel", headers=actors["other"]).status_code
        == 403
    )
    assert (
        client.post(f"/reservations/{order['id']}/cancel", headers=actors["buyer"]).json()["data"][
            "status"
        ]
        == "cancelled"
    )
    assert reserve(client, actors["other"], item, seats[:1]).status_code == 201


def test_check_in_replay_and_refund_used_ticket(client, actors, inventory):
    _, item, seats = inventory(hours=1)
    order_id, tickets = purchase(client, actors, item, seats[:1])
    token = sign_token(tickets[0]["id"], "ticket", datetime.now(timezone.utc) + timedelta(hours=2))
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401
    assert (
        client.post(
            "/tickets/check-in", headers=actors["buyer"], json={"qr_token": token}
        ).status_code
        == 403
    )
    assert (
        client.post("/tickets/check-in", headers=actors["owner"], json={"qr_token": token}).json()[
            "data"
        ]["status"]
        == "used"
    )
    assert (
        client.post(
            "/tickets/check-in", headers=actors["owner"], json={"qr_token": token}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/admin/reservations/{order_id}/refund",
            headers=actors["admin"],
            json={"reason": "Should fail after entry."},
        ).status_code
        == 409
    )


def test_suspended_account_and_event(client, actors, inventory):
    event, item, seats = inventory()
    user_id = client.get("/auth/me", headers=actors["buyer"]).json()["data"]["id"]
    suspended = client.patch(
        f"/admin/users/{user_id}/status",
        headers=actors["admin"],
        json={"status": "suspended", "reason": "Abuse investigation."},
    )
    assert suspended.status_code == 200
    assert client.get("/auth/me", headers=actors["buyer"]).status_code == 403
    assert (
        client.patch(
            f"/admin/events/{event}/moderation",
            headers=actors["admin"],
            json={"status": "suspended", "reason": "Safety investigation."},
        ).status_code
        == 200
    )
    assert reserve(client, actors["other"], item, seats[:1]).status_code == 409


def test_rate_limit_and_demo_disable(client, actors, inventory, monkeypatch):
    _, item, _ = inventory(False, quantity=10)
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    for i in range(5):
        assert reserve(client, actors["buyer"], item, key=f"rate-key-{i}").status_code == 201
    blocked = reserve(client, actors["buyer"], item, key="rate-key-last")
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
    monkeypatch.setattr(get_settings(), "demo_payments_enabled", False)
    order_id = client.get("/reservations/me", headers=actors["buyer"]).json()["data"][0]["id"]
    assert (
        client.post(
            f"/reservations/{order_id}/pay-demo",
            headers=actors["buyer"],
            json={"outcome": "success"},
        ).status_code
        == 403
    )
