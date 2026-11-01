from fastapi import HTTPException
from sqlalchemy import select

from app.models import AuditLog, Event


def event_or_404(db, event_id, lock=False):
    query = select(Event).where(Event.id == event_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    event = db.scalar(query)
    if event is None:
        raise HTTPException(404, "Event not found.")
    return event


def require_owner(event, user):
    if event.organizer_id != user.id and user.role != "admin":
        raise HTTPException(403, "Only this event's organizer or an admin may do that.")


def audit(db, actor, action, target, reason):
    db.add(AuditLog(actor_id=actor.id, action=action, target=str(target), reason=reason))
