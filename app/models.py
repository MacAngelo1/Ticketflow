import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('customer','organizer','admin')"),
        CheckConstraint("status IN ('active','suspended','banned')"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    full_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="customer")
    status: Mapped[str] = mapped_column(String(20), default="active")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint("status IN ('pending','active','suspended','rejected','cancelled')"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organizer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    venue: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    is_seated: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)


class TicketType(Base):
    __tablename__ = "ticket_types"
    __table_args__ = (
        UniqueConstraint("event_id", "name"),
        CheckConstraint("price >= 0"),
        CheckConstraint("quantity > 0"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    quantity: Mapped[int] = mapped_column(Integer)


class Seat(Base):
    __tablename__ = "seats"
    __table_args__ = (UniqueConstraint("event_id", "label"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    ticket_type_id: Mapped[int] = mapped_column(ForeignKey("ticket_types.id"), index=True)
    label: Mapped[str] = mapped_column(String(30))


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        CheckConstraint("quantity > 0"),
        CheckConstraint(
            "status IN ('reserved','paid','confirmed','expired','cancelled','refunded')"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    ticket_type_id: Mapped[int] = mapped_column(ForeignKey("ticket_types.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    seat_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(20), default="reserved", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payment_reference: Mapped[str | None] = mapped_column(String(100), unique=True)


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("reservation_id", "number"),
        CheckConstraint("status IN ('valid','used','refunded')"),
        Index(
            "unique_live_seat",
            "seat_id",
            unique=True,
            postgresql_where=text("status IN ('valid', 'used')"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    reservation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reservations.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    seat_id: Mapped[int | None] = mapped_column(ForeignKey("seats.id"))
    status: Mapped[str] = mapped_column(String(20), default="valid")
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RateBucket(Base):
    __tablename__ = "rate_buckets"
    key: Mapped[str] = mapped_column(String(150), primary_key=True)
    window: Mapped[int] = mapped_column(Integer, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=1)
