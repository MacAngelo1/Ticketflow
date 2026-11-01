from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Output(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Register(Input):
    # Password whitespace is meaningful; don't strip it.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    role: Literal["customer", "organizer"] = "customer"


class UserOut(Output):
    id: UUID
    email: EmailStr
    full_name: str
    role: str
    status: str


class TokenOut(Output):
    access_token: str
    token_type: str = "bearer"


def future_date(value):
    if value is not None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Include a timezone, for example 2027-06-20T18:00:00+01:00.")
        if value <= datetime.now(timezone.utc):
            raise ValueError("Event date must be in the future.")
    return value


class EventCreate(Input):
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=5000)
    venue: str = Field(min_length=1, max_length=200)
    starts_at: datetime
    is_seated: bool = False

    @field_validator("starts_at")
    @classmethod
    def validate_date(cls, value):
        return future_date(value)


class EventUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=5000)
    venue: str | None = Field(default=None, min_length=1, max_length=200)
    starts_at: datetime | None = None

    @field_validator("starts_at")
    @classmethod
    def validate_date(cls, value):
        return future_date(value)

    @model_validator(mode="after")
    def non_null(self):
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Supplied fields cannot be null.")
        return self


class EventOut(Output):
    id: int
    organizer_id: UUID
    name: str
    description: str
    venue: str
    starts_at: datetime
    is_seated: bool
    status: str


class TypeCreate(Input):
    name: str = Field(min_length=1, max_length=50)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    quantity: int = Field(ge=1, le=100000)


class TypeOut(Output):
    id: int
    event_id: int
    name: str
    price: Decimal
    quantity: int
    available: int


class SeatsCreate(Input):
    ticket_type_id: int = Field(gt=0)
    labels: list[str] = Field(min_length=1, max_length=500)

    @field_validator("labels")
    @classmethod
    def labels_unique(cls, values):
        labels = [value.strip().upper() for value in values]
        if len(labels) != len(set(labels)) or any(not x or len(x) > 30 for x in labels):
            raise ValueError("Use unique, nonempty seat labels up to 30 characters.")
        return labels


class Reserve(Input):
    ticket_type_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=20)
    seat_ids: list[int] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_seats(self):
        if len(self.seat_ids) != len(set(self.seat_ids)) or any(x <= 0 for x in self.seat_ids):
            raise ValueError("Seat IDs must be positive and unique.")
        return self


class ReservationOut(Output):
    id: UUID
    user_id: UUID
    event_id: int
    ticket_type_id: int
    quantity: int
    seat_ids: list[int]
    total: Decimal
    status: str
    expires_at: datetime
    payment_reference: str | None


class TicketOut(Output):
    id: UUID
    reservation_id: UUID
    number: int
    seat_id: int | None
    status: str
    checked_in_at: datetime | None


class DemoPayment(Input):
    outcome: Literal["success", "failure"] = "success"


class Reason(Input):
    reason: str = Field(min_length=5, max_length=1000)


class Moderate(Reason):
    status: Literal["active", "suspended", "rejected"]


class UserStatus(Reason):
    status: Literal["active", "suspended", "banned"]


class CheckIn(Input):
    qr_token: str = Field(min_length=10, max_length=2000)
