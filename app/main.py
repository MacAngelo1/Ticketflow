import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from app.api import admin, auth, events, orders
from app.responses import ApiResponse, error_response, success_response

logger = logging.getLogger(__name__)
app = FastAPI(
    title="TicketFlow",
    version="0.1.0",
    description="Event ticketing API. Payments and refunds are DEMO ONLY; no money moves.",
    responses={
        code: {"model": ApiResponse} for code in (400, 401, 402, 403, 404, 405, 409, 422, 429, 500)
    },
)
app.include_router(auth.router)
app.include_router(events.router)
app.include_router(orders.router)
app.include_router(admin.router)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(str(exc.detail), exc.status_code),
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Omit submitted input and exception contexts, which may contain passwords.
    errors = [
        {"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=error_response("Request validation failed.", 422, {"errors": errors}),
    )


@app.exception_handler(IntegrityError)
async def conflict(request: Request, exc: IntegrityError):
    return JSONResponse(
        status_code=409,
        content=error_response("Conflicting or duplicate data. Refresh and retry.", 409),
    )


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    logger.error("Unhandled API error", exc_info=(type(exc), exc, exc.__traceback__))
    return JSONResponse(status_code=500, content=error_response("Internal server error.", 500))


@app.get("/health", tags=["Health"], response_model=ApiResponse[dict])
def health():
    return success_response(
        "Service is running.",
        {
            "status": "ok",
            "service": "TicketFlow",
            "payment_mode": "demo",
        },
    )
