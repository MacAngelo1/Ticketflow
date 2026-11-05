import logging

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Event, Reservation, Ticket, User

logger = logging.getLogger("uvicorn.error")


def log_confirmation(reservation_id):
    """Console email substitute: no email service or email credentials are used."""
    with SessionLocal() as db:
        order = db.get(Reservation, reservation_id)
        if order is None or order.status != "confirmed":
            return
        user = db.get(User, order.user_id)
        event = db.get(Event, order.event_id)
        tickets = db.scalars(select(Ticket).where(Ticket.reservation_id == order.id)).all()
        links = "\n".join(
            f"  GET /tickets/{ticket.id}/qr (authenticated JSON containing base64 PNG)"
            for ticket in tickets
        )
        logger.info(
            "\n[DEMO EMAIL - CONSOLE ONLY]\nTo: %s\nSubject: Tickets for %s\n"
            "Reservation: %s\nTotal: NGN %s\nYour QR downloads:\n%s\n",
            user.email,
            event.name,
            order.id,
            order.total,
            links,
        )
