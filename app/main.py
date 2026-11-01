from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api import admin, auth, events, orders

app = FastAPI(
    title="TicketFlow",
    version="0.1.0",
    description="Event ticketing API. Payments and refunds are DEMO ONLY; no money moves.",
)
app.include_router(auth.router)
app.include_router(events.router)
app.include_router(orders.router)
app.include_router(admin.router)


@app.exception_handler(IntegrityError)
async def conflict(request: Request, exc: IntegrityError):
    # The request session is closed/rolled back by get_db. Don't leak SQL or credentials.
    return JSONResponse(
        status_code=409, content={"detail": "Conflicting or duplicate data. Refresh and retry."}
    )


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok", "service": "TicketFlow", "payment_mode": "demo"}
