from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AuditLog, Event, Reservation, Ticket, User
from app.responses import ApiResponse, success_response
from app.schemas import AuditOut, EventOut, Moderate, Reason, ReservationOut, UserOut, UserStatus
from app.security import admin
from app.services.common import audit, event_or_404
from app.services.inventory import db_now, locked_reservation

router = APIRouter(prefix="/admin", tags=["Admin dashboard (API)"])


@router.get("/users", response_model=ApiResponse[list[UserOut]])
def users(
    actor: User = Depends(admin),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    return success_response(
        "Users retrieved successfully.",
        db.scalars(select(User).order_by(User.email).offset(offset).limit(limit)).all(),
    )


@router.patch("/users/{user_id}/status", response_model=ApiResponse[UserOut])
def set_status(
    user_id: UUID, body: UserStatus, actor: User = Depends(admin), db: Session = Depends(get_db)
):
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if not user:
        raise HTTPException(404, "User not found.")
    if user.role == "admin":
        raise HTTPException(403, "This endpoint cannot suspend admin accounts.")
    user.status = body.status
    audit(db, actor, "user.status", user.id, f"{body.status}: {body.reason}")
    db.commit()
    return success_response("User status updated successfully.", user)


@router.get("/events", response_model=ApiResponse[list[EventOut]])
def events(
    actor: User = Depends(admin),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    return success_response(
        "Events retrieved successfully.",
        db.scalars(select(Event).order_by(Event.id).offset(offset).limit(limit)).all(),
    )


@router.patch("/events/{event_id}/moderation", response_model=ApiResponse[EventOut])
def moderate(
    event_id: int, body: Moderate, actor: User = Depends(admin), db: Session = Depends(get_db)
):
    event = event_or_404(db, event_id, lock=True)
    allowed = {
        "pending": {"active", "rejected"},
        "active": {"suspended"},
        "suspended": {"active", "rejected"},
        "rejected": {"active"},
    }
    if body.status not in allowed.get(event.status, set()):
        raise HTTPException(409, "Invalid moderation transition.")
    if body.status == "active" and event.starts_at <= db_now(db):
        raise HTTPException(409, "Cannot activate an event that has started.")
    event.status = body.status
    audit(db, actor, "event.moderate", event.id, f"{body.status}: {body.reason}")
    db.commit()
    return success_response("Event moderation updated successfully.", event)


@router.get("/reservations", response_model=ApiResponse[list[ReservationOut]])
def reservations(
    actor: User = Depends(admin),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    orders = db.scalars(
        select(Reservation)
        .order_by(Reservation.created_at.desc(), Reservation.id)
        .offset(offset)
        .limit(limit)
    ).all()
    now = db_now(db)
    views = [ReservationOut.model_validate(order) for order in orders]
    for view in views:
        if view.status == "reserved" and view.expires_at <= now:
            view.status = "expired"
    return success_response("Reservations retrieved successfully.", views)


@router.post("/reservations/{reservation_id}/refund", response_model=ApiResponse[ReservationOut])
def refund(
    reservation_id: UUID, body: Reason, actor: User = Depends(admin), db: Session = Depends(get_db)
):
    _, order, _ = locked_reservation(db, reservation_id, actor, allow_admin=True)
    if order.status == "refunded":
        db.commit()
        return success_response("Reservation refunded successfully.", order)
    if order.status not in ("paid", "confirmed"):
        raise HTTPException(409, "Only paid or confirmed orders can be refunded.")
    tickets = db.scalars(select(Ticket).where(Ticket.reservation_id == order.id)).all()
    if any((ticket.status == "used" for ticket in tickets)):
        raise HTTPException(409, "A checked-in order cannot be refunded.")
    for ticket in tickets:
        ticket.status = "refunded"
    order.status = "refunded"
    audit(db, actor, "order.refund", order.id, body.reason)
    db.commit()
    return success_response("Reservation refunded successfully.", order)


@router.get("/reports", response_model=ApiResponse)
def reports(actor: User = Depends(admin), db: Session = Depends(get_db)):
    result = {"currency": "NGN", "payment_mode": "demo", "orders": {}}
    for status, count, quantity, total in db.execute(
        select(
            Reservation.status,
            func.count(),
            func.sum(Reservation.quantity),
            func.sum(Reservation.total),
        )
        .where(Reservation.status.in_(["paid", "confirmed", "refunded"]))
        .group_by(Reservation.status)
    ):
        result["orders"][status] = {"orders": count, "tickets": quantity, "amount": total}
    result["net_sales"] = sum(
        (value["amount"] for key, value in result["orders"].items() if key != "refunded")
    )
    result["users"] = db.scalar(select(func.count()).select_from(User))
    result["events"] = db.scalar(select(func.count()).select_from(Event))
    return success_response("Platform report retrieved successfully.", result)


@router.get("/audit-logs", response_model=ApiResponse[list[AuditOut]])
def logs(
    actor: User = Depends(admin),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    return success_response(
        "Audit logs retrieved successfully.",
        db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).offset(offset).limit(limit)).all(),
    )


@router.get("/settings", response_model=ApiResponse)
def settings(actor: User = Depends(admin)):
    config = get_settings()
    return success_response(
        "Settings retrieved successfully.",
        {
            "reservation_minutes": config.reservation_minutes,
            "max_tickets_per_order": config.max_tickets_per_order,
            "demo_payments_enabled": config.demo_payments_enabled,
            "rate_limit_enabled": config.rate_limit_enabled,
            "currency": "NGN",
        },
    )
