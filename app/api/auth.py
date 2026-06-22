from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

from app.auth.bearer import TeamIdentity, TeamRole, require_auth
from app.auth.jwt_utils import create_access_token
from app.storage.db import get_db
from app.storage.models import User

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str | None
    team_name: str
    role: str
    is_active: bool


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain, hashed)


@router.post("/login", response_model=LoginResponse, summary="Login with email and password")
async def login(body: LoginRequest) -> LoginResponse:
    async with get_db() as session:
        result = await session.execute(select(User).where(User.email == body.email))
        user: User | None = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    token = create_access_token(
        user_id=str(user.id),
        email=user.email,
        team_name=user.team_name,
        role=user.role,
    )
    logger.info("user_logged_in", email=user.email, role=user.role)
    return LoginResponse(
        access_token=token,
        user=UserOut(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            team_name=user.team_name,
            role=user.role,
            is_active=user.is_active,
        ),
    )


@router.get("/me", response_model=UserOut, summary="Get current user info")
async def me(identity: TeamIdentity = Depends(require_auth)) -> UserOut:
    from app.auth.jwt_utils import decode_access_token

    payload = decode_access_token(identity.token)
    if payload:
        return UserOut(
            id=payload.get("sub", ""),
            email=payload.get("email", ""),
            full_name=None,
            team_name=identity.team_name,
            role=identity.role.value,
            is_active=True,
        )
    # DEV_TOKEN fallback — no DB user record
    return UserOut(
        id="",
        email=f"{identity.team_name}@dev.local",
        full_name=None,
        team_name=identity.team_name,
        role=identity.role.value,
        is_active=True,
    )
