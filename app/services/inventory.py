from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select, update

from app.config import get_settings
from app.models import Reservation, Seat, Ticket, TicketType
from app.services.common import event_or_404


def db_now(db):
    # Read after acquiring the lock: time spent waiting must count toward expiry.
    return db.scalar(select(func.clock_timestamp()))


def occupying(now):
    return or_(
        Reservation.status.in_(["paid", "confirmed"]),
        and_(Reservation.status == "reserved", Reservation.expires_at > now),
    )


def available(db, ticket_type, now):
    taken = db.scalar(
        select(func.coalesce(func.sum(Reservation.quantity), 0)).where(
            Reservation.ticket_type_id == ticket_type.id, occupying(now)
        )
    )
    return ticket_type.quantity - taken


def occupied_seats(db, event_id, now):
    groups = db.scalars(
        select(Reservation.seat_ids).where(Reservation.event_id == event_id, occupying(now))
    ).all()
    return {seat for group in groups for seat in group}


def expire_event(db, event_id, now):
    db.execute(
        update(Reservation)
        .where(
            Reservation.event_id == event_id,
            Reservation.status == "reserved",
            Reservation.expires_at <= now,
        )
        .values(status="expired")
    )


def reserve(db, user, body, key):
    ticket_type = db.get(TicketType, body.ticket_type_id)
    if not ticket_type:
        raise HTTPException(404, "Ticket type not found.")
    # Every inventory mutation locks this same event row first.
    event = event_or_404(db, ticket_type.event_id, lock=True)
    now = db_now(db)
    existing = db.scalar(
        select(Reservation).where(
            Reservation.user_id == user.id, Reservation.idempotency_key == key
        )
    )
    if existing:
        if (
            existing.ticket_type_id != body.ticket_type_id
            or existing.quantity != body.quantity
            or sorted(existing.seat_ids) != sorted(body.seat_ids)
        ):
            raise HTTPException(409, "This idempotency key was used for a different request.")
        if existing.status == "reserved" and existing.expires_at <= now:
            existing.status = "expired"
        db.commit()
        return existing
    if event.status != "active" or event.starts_at <= now:
        raise HTTPException(409, "This event is not open for sales.")
    if body.quantity > get_settings().max_tickets_per_order:
        raise HTTPException(422, "Quantity exceeds the per-order limit.")
    expire_event(db, event.id, now)
    if available(db, ticket_type, now) < body.quantity:
        raise HTTPException(409, "Not enough tickets remain.")
    if event.is_seated:
        if len(body.seat_ids) != body.quantity:
            raise HTTPException(422, "Choose exactly one seat per ticket.")
        seats = db.scalars(
            select(Seat).where(Seat.id.in_(body.seat_ids), Seat.ticket_type_id == ticket_type.id)
        ).all()
        if len(seats) != body.quantity:
            raise HTTPException(422, "Every seat must belong to the selected ticket type.")
        if occupied_seats(db, event.id, now).intersection(body.seat_ids):
            raise HTTPException(409, "One or more seats are already reserved or sold.")
    elif body.seat_ids:
        raise HTTPException(422, "This event does not use assigned seats.")
    reservation = Reservation(
        user_id=user.id,
        event_id=event.id,
        ticket_type_id=ticket_type.id,
        quantity=body.quantity,
        seat_ids=sorted(body.seat_ids),
        total=ticket_type.price * body.quantity,
        idempotency_key=key,
        expires_at=now + timedelta(minutes=get_settings().reservation_minutes),
    )
    db.add(reservation)
    db.commit()
    return reservation


def locked_reservation(db, reservation_id, user, allow_admin=False):
    preliminary = db.get(Reservation, reservation_id)
    if preliminary is None:
        raise HTTPException(404, "Reservation not found.")
    if preliminary.user_id != user.id and not (allow_admin and user.role == "admin"):
        raise HTTPException(403, "This reservation belongs to another user.")
    event = event_or_404(db, preliminary.event_id, lock=True)
    reservation = db.scalar(
        select(Reservation)
        .where(Reservation.id == reservation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = db_now(db)
    if reservation.status == "reserved" and reservation.expires_at <= now:
        reservation.status = "expired"
    return event, reservation, now


def demo_pay(db, reservation_id, user, outcome):
    if not get_settings().demo_payments_enabled:
        raise HTTPException(403, "Demo payments are disabled; no real gateway is configured.")
    event, reservation, now = locked_reservation(db, reservation_id, user)
    if reservation.status in ("paid", "confirmed"):
        db.commit()
        return reservation
    if reservation.status != "reserved":
        db.commit()
        raise HTTPException(409, "Reservation is expired, cancelled, or refunded.")
    if event.status != "active" or event.starts_at <= now:
        raise HTTPException(409, "Event is not open for payment.")
    if outcome == "failure":
        db.commit()
        raise HTTPException(402, "Simulated payment failed. Retry before the hold expires.")
    reservation.status = "paid"
    reservation.payment_reference = f"demo_{uuid4()}"
    db.commit()
    return reservation


def confirm(db, reservation_id, user):
    event, reservation, now = locked_reservation(db, reservation_id, user)
    if reservation.status == "confirmed":
        tickets = db.scalars(
            select(Ticket).where(Ticket.reservation_id == reservation.id).order_by(Ticket.number)
        ).all()
        db.commit()
        return tickets, False
    if reservation.status != "paid":
        db.commit()
        raise HTTPException(409, "A successful payment is required before confirmation.")
    if event.status != "active" or event.starts_at <= now:
        raise HTTPException(409, "Event unavailable; contact an admin for a refund.")
    tickets = [
        Ticket(
            reservation_id=reservation.id,
            number=i + 1,
            seat_id=reservation.seat_ids[i] if reservation.seat_ids else None,
        )
        for i in range(reservation.quantity)
    ]
    db.add_all(tickets)
    reservation.status = "confirmed"
    db.commit()
    return tickets, True
