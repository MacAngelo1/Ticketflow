from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Event, Reservation, Seat, TicketType, User
from app.schemas import EventCreate, EventOut, EventUpdate, SeatsCreate, TypeCreate, TypeOut
from app.security import organizer
from app.services.common import audit, event_or_404, require_owner
from app.services.inventory import available, db_now, occupied_seats, occupying

router = APIRouter(prefix="/events", tags=["Events and inventory"])


@router.get("/", response_model=list[EventOut])
def browse(
    search: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = select(Event).where(Event.status == "active", Event.starts_at > func.now())
    if search:
        query = query.where(Event.name.icontains(search, autoescape=True))
    return db.scalars(query.order_by(Event.starts_at, Event.id).offset(offset).limit(limit)).all()


@router.get("/mine", response_model=list[EventOut])
def mine(
    user: User = Depends(organizer),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    return db.scalars(
        select(Event)
        .where(Event.organizer_id == user.id)
        .order_by(Event.id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.post("/", response_model=EventOut, status_code=201)
def create(body: EventCreate, user: User = Depends(organizer), db: Session = Depends(get_db)):
    event = Event(**body.model_dump(), organizer_id=user.id)
    db.add(event)
    if user.role == "admin":
        audit(db, user, "event.create", body.name, "Created by admin; awaiting approval.")
    db.commit()
    return event


@router.get("/{event_id}", response_model=EventOut)
def detail(event_id: int, db: Session = Depends(get_db)):
    event = event_or_404(db, event_id)
    if event.status != "active":
        raise HTTPException(404, "Active event not found. Owners can use /events/mine.")
    return event


@router.put("/{event_id}", response_model=EventOut)
def edit(
    event_id: int, body: EventUpdate, user: User = Depends(organizer), db: Session = Depends(get_db)
):
    event = event_or_404(db, event_id, lock=True)
    require_owner(event, user)
    if event.status == "cancelled":
        raise HTTPException(409, "Cancelled events cannot be edited.")
    if db.scalar(
        select(Reservation.id)
        .where(Reservation.event_id == event.id, occupying(db_now(db)))
        .limit(1)
    ):
        raise HTTPException(
            409, "Cannot edit event details while live reservations or purchases exist."
        )
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(event, key, value)
    event.status = "pending"
    if user.role == "admin":
        audit(db, user, "event.edit", event.id, "Details changed; reapproval required.")
    db.commit()
    return event


@router.delete("/{event_id}", response_model=EventOut)
def cancel(event_id: int, user: User = Depends(organizer), db: Session = Depends(get_db)):
    event = event_or_404(db, event_id, lock=True)
    require_owner(event, user)
    event.status = "cancelled"
    if user.role == "admin":
        audit(db, user, "event.cancel", event.id, "Cancelled through event endpoint.")
    db.commit()
    return event


@router.post("/{event_id}/ticket-types", status_code=201)
def create_type(
    event_id: int, body: TypeCreate, user: User = Depends(organizer), db: Session = Depends(get_db)
):
    event = event_or_404(db, event_id, lock=True)
    require_owner(event, user)
    if event.status not in ("pending", "active") or event.starts_at <= db_now(db):
        raise HTTPException(409, "Cannot add inventory to this event.")
    item = TicketType(event_id=event.id, **body.model_dump())
    db.add(item)
    if user.role == "admin":
        audit(db, user, "inventory.create", event.id, f"Added ticket type {body.name}.")
    db.commit()
    return {"id": item.id, "event_id": event.id, **body.model_dump()}


@router.get("/{event_id}/ticket-types", response_model=list[TypeOut])
def types(event_id: int, db: Session = Depends(get_db)):
    event = event_or_404(db, event_id)
    if event.status != "active":
        raise HTTPException(404, "Active event not found.")
    now = db_now(db)
    items = db.scalars(
        select(TicketType).where(TicketType.event_id == event.id).order_by(TicketType.id)
    ).all()
    return [
        {
            "id": item.id,
            "event_id": event.id,
            "name": item.name,
            "price": item.price,
            "quantity": item.quantity,
            "available": available(db, item, now),
        }
        for item in items
    ]


@router.post("/{event_id}/seats", status_code=201)
def create_seats(
    event_id: int, body: SeatsCreate, user: User = Depends(organizer), db: Session = Depends(get_db)
):
    event = event_or_404(db, event_id, lock=True)
    require_owner(event, user)
    if (
        not event.is_seated
        or event.status not in ("pending", "active")
        or event.starts_at <= db_now(db)
    ):
        raise HTTPException(409, "Event is not accepting seat definitions.")
    item = db.get(TicketType, body.ticket_type_id)
    if not item or item.event_id != event.id:
        raise HTTPException(422, "Ticket type must belong to this event.")
    count = db.scalar(select(func.count()).select_from(Seat).where(Seat.ticket_type_id == item.id))
    if count + len(body.labels) > item.quantity:
        raise HTTPException(422, "Seats cannot exceed ticket-type quantity.")
    seats = [Seat(event_id=event.id, ticket_type_id=item.id, label=label) for label in body.labels]
    db.add_all(seats)
    if user.role == "admin":
        audit(db, user, "seats.create", event.id, f"Added {len(seats)} seats.")
    db.commit()
    return [{"id": seat.id, "label": seat.label, "ticket_type_id": item.id} for seat in seats]


@router.get("/{event_id}/seats")
def list_seats(event_id: int, db: Session = Depends(get_db)):
    event = event_or_404(db, event_id)
    if event.status != "active":
        raise HTTPException(404, "Active event not found.")
    taken = occupied_seats(db, event.id, db_now(db))
    seats = db.scalars(select(Seat).where(Seat.event_id == event.id).order_by(Seat.label)).all()
    return [
        {
            "id": seat.id,
            "label": seat.label,
            "ticket_type_id": seat.ticket_type_id,
            "available": seat.id not in taken,
        }
        for seat in seats
    ]


@router.get("/{event_id}/sales")
def sales(event_id: int, user: User = Depends(organizer), db: Session = Depends(get_db)):
    event = event_or_404(db, event_id)
    require_owner(event, user)
    qty, amount = db.execute(
        select(
            func.coalesce(func.sum(Reservation.quantity), 0),
            func.coalesce(func.sum(Reservation.total), 0),
        ).where(Reservation.event_id == event.id, Reservation.status.in_(["paid", "confirmed"]))
    ).one()
    return {"event_id": event.id, "paid_tickets": qty, "net_sales": amount, "currency": "NGN"}
