from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User

password_hasher = PasswordHash.recommended()
oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/token")
ALGORITHM = "HS256"


def unauthorized(message="Invalid or expired token."):
    return HTTPException(401, message, headers={"WWW-Authenticate": "Bearer"})


def sign_token(subject, purpose, expires):
    return jwt.encode(
        {
            "sub": str(subject),
            "purpose": purpose,
            "iat": datetime.now(timezone.utc),
            "exp": expires,
        },
        get_settings().secret_key,
        algorithm=ALGORITHM,
    )


def access_token(user_id):
    return sign_token(
        user_id,
        "access",
        datetime.now(timezone.utc) + timedelta(minutes=get_settings().access_token_minutes),
    )


def decode_token(token, purpose):
    try:
        data = jwt.decode(
            token,
            get_settings().secret_key,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "purpose", "iat", "exp"]},
        )
        if data["purpose"] != purpose:
            raise unauthorized("Wrong token purpose.")
        return UUID(data["sub"])
    except jwt.ExpiredSignatureError:
        raise unauthorized("Token expired.")
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise unauthorized()


def current_user(token: str = Depends(oauth2), db: Session = Depends(get_db)):
    user = db.get(User, decode_token(token, "access"))
    if not user:
        raise unauthorized()
    if user.status != "active":
        raise HTTPException(403, "Account is suspended or banned.")
    return user


def organizer(user: User = Depends(current_user)):
    if user.role not in ("organizer", "admin"):
        raise HTTPException(403, "Organizer access required.")
    return user


def admin(user: User = Depends(current_user)):
    if user.role != "admin":
        raise HTTPException(403, "Admin access required.")
    return user
