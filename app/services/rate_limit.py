import time

from fastapi import HTTPException, Request
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.database import SessionLocal
from app.models import RateBucket


def limit(key, maximum, seconds=60):
    """An atomic PostgreSQL counter shared by all API worker processes."""
    if not get_settings().rate_limit_enabled:
        return
    now = int(time.time())
    window = now // seconds
    with SessionLocal.begin() as db:
        statement = insert(RateBucket).values(key=key, window=window, count=1)
        count = db.scalar(
            statement.on_conflict_do_update(
                index_elements=[RateBucket.key, RateBucket.window],
                set_={"count": RateBucket.count + 1},
            ).returning(RateBucket.count)
        )
    if count > maximum:
        raise HTTPException(
            429,
            "Too many requests. Try again shortly.",
            headers={"Retry-After": str(seconds - now % seconds)},
        )


def auth_limit(request: Request):
    # Do not trust a caller-supplied X-Forwarded-For header.
    address = request.client.host if request.client else "unknown"
    limit(f"auth:{address}", 20)
