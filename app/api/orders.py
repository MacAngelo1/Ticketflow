from base64 import b64encode
from datetime import timedelta
from io import BytesIO
from uuid import UUID

import qrcode
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Event, Reservation, Ticket, User
from app.responses import ApiResponse, success_response
from app.schemas import CheckIn, DemoPayment, ReservationOut, Reserve, TicketOut
from app.security import current_user, decode_token, organizer, sign_token
from app.services.common import audit, event_or_404, require_owner
from app.services.inventory import confirm, db_now, demo_pay, locked_reservation, reserve
from app.services.notifications import log_confirmation
from app.services.rate_limit import limit

router = APIRouter(tags=["Reservations and tickets"])


@router.post("/reservations/", response_model=ApiResponse[ReservationOut], status_code=201)
def create_reservation(
    body: Reserve,
    idempotency_key: str = Header(min_length=8, max_length=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    limit(f"reserve:{user.id}", 5)
    return success_response(
        "Reservation retrieved or created successfully.",
        reserve(db, user, body, idempotency_key),
        status_code=201,
    )


@router.get("/reservations/me", response_model=ApiResponse[list[ReservationOut]])
def my_orders(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    orders = db.scalars(
        select(Reservation)
        .where(Reservation.user_id == user.id)
        .order_by(Reservation.created_at.desc(), Reservation.id)
        .offset(offset)
        .limit(limit)
    ).all()
    now = db_now(db)
    result = []
    for order in orders:
        view = ReservationOut.model_validate(order)
        if view.status == "reserved" and view.expires_at <= now:
            view.status = "expired"
        result.append(view)
    return success_response("Your reservations retrieved successfully.", result)


@router.post("/reservations/{reservation_id}/pay-demo", response_model=ApiResponse[ReservationOut])
def pay(
    reservation_id: UUID,
    body: DemoPayment,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    limit(f"pay:{user.id}", 10)
    return success_response(
        "Payment processed successfully.", demo_pay(db, reservation_id, user, body.outcome)
    )


@router.post("/reservations/{reservation_id}/confirm", response_model=ApiResponse[list[TicketOut]])
def confirm_order(
    reservation_id: UUID,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    tickets, newly_confirmed = confirm(db, reservation_id, user)
    if newly_confirmed:
        background.add_task(log_confirmation, reservation_id)
    return success_response("Reservation confirmed successfully.", tickets)


@router.post("/reservations/{reservation_id}/cancel", response_model=ApiResponse[ReservationOut])
def cancel_order(
    reservation_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    _, order, _ = locked_reservation(db, reservation_id, user)
    if order.status in ("paid", "confirmed", "refunded"):
        raise HTTPException(409, "Paid orders require an admin refund.")
    if order.status == "reserved":
        order.status = "cancelled"
    db.commit()
    return success_response("Reservation cancellation processed.", order)


@router.get("/tickets/me", response_model=ApiResponse[list[TicketOut]])
def my_tickets(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    return success_response(
        "Your tickets retrieved successfully.",
        db.scalars(
            select(Ticket)
            .join(Reservation)
            .where(Reservation.user_id == user.id)
            .order_by(Ticket.id)
            .offset(offset)
            .limit(limit)
        ).all(),
    )


@router.get("/tickets/{ticket_id}/qr", response_model=ApiResponse)
def ticket_qr(ticket_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found.")
    order = db.get(Reservation, ticket.reservation_id)
    if order.user_id != user.id and user.role != "admin":
        raise HTTPException(403, "This ticket belongs to another customer.")
    event = db.get(Event, order.event_id)
    if ticket.status != "valid" or order.status != "confirmed" or event.status != "active":
        raise HTTPException(409, "Ticket is not currently valid.")
    expires = event.starts_at + timedelta(days=1)
    if db_now(db) >= expires:
        raise HTTPException(409, "Ticket has expired.")
    qr_token = sign_token(ticket.id, "ticket", expires)
    buffer = BytesIO()
    qrcode.make(qr_token).save(buffer, format="PNG")
    return success_response(
        "Ticket QR code generated successfully.",
        {
            "filename": f"ticket-{ticket.id}.png",
            "media_type": "image/png",
            "encoding": "base64",
            "content": b64encode(buffer.getvalue()).decode("ascii"),
        },
    )


@router.post("/tickets/check-in", response_model=ApiResponse[TicketOut])
def check_in(body: CheckIn, user: User = Depends(organizer), db: Session = Depends(get_db)):
    ticket_id = decode_token(body.qr_token, "ticket")
    initial = db.get(Ticket, ticket_id)
    if not initial:
        raise HTTPException(404, "Ticket not found.")
    order = db.get(Reservation, initial.reservation_id)
    event = event_or_404(db, order.event_id, lock=True)
    require_owner(event, user)
    ticket = db.scalar(
        select(Ticket)
        .where(Ticket.id == ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    db.refresh(order)
    if event.status != "active" or order.status != "confirmed" or ticket.status != "valid":
        raise HTTPException(409, "Ticket is invalid, refunded, or already checked in.")
    now = db_now(db)
    if now < event.starts_at - timedelta(hours=2) or now > event.starts_at + timedelta(days=1):
        raise HTTPException(
            409, "Check-in opens two hours before the event and closes one day after."
        )
    ticket.status = "used"
    ticket.checked_in_at = now
    if user.role == "admin":
        audit(db, user, "ticket.check_in", ticket.id, "Admin checked ticket at entry.")
    db.commit()
    return success_response("Ticket checked in successfully.", ticket)
