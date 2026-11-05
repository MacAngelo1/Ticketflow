from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.responses import ApiResponse, success_response
from app.schemas import Register, TokenOut, UserOut
from app.security import access_token, current_user, password_hasher, unauthorized
from app.services.rate_limit import auth_limit

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=ApiResponse[UserOut],
    status_code=201,
    dependencies=[Depends(auth_limit)],
)
def register(body: Register, db: Session = Depends(get_db)):
    user = User(
        email=str(body.email).lower(),
        full_name=body.full_name,
        password_hash=password_hasher.hash(body.password),
        role=body.role,
    )
    db.add(user)
    db.commit()
    return success_response("User registered successfully.", user, status_code=201)


@router.post("/token", response_model=ApiResponse[TokenOut], dependencies=[Depends(auth_limit)])
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == form.username.strip().lower()))
    if not user or not password_hasher.verify(form.password, user.password_hash):
        raise unauthorized("Invalid email or password.")
    if user.status != "active":
        raise unauthorized("Account is not active.")
    return success_response(
        "Login successful.", {"access_token": access_token(user.id), "token_type": "bearer"}
    )


@router.get("/me", response_model=ApiResponse[UserOut])
def me(user: User = Depends(current_user)):
    return success_response("Profile retrieved successfully.", user)
