from typing import Any, Generic, TypeVar

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    message: str
    data: T | None
    status_code: int
    success: bool


def success_response(message: str, data: Any = None, status_code: int = 200):
    return {
        "message": message,
        "data": data,
        "status_code": status_code,
        "success": True,
    }


def error_response(message: str, status_code: int, data: Any = None):
    return {
        "message": message,
        "data": jsonable_encoder(data),
        "status_code": status_code,
        "success": False,
    }
