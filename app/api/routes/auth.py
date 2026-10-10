from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from jwt.exceptions import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_active_user
from app.core.config import settings
from app.core.exceptions import EmailAlreadyRegistered
from app.core.security import create_token, decode_token
from app.db.session import get_db
from app.schemas.auth import (
    OrgBrief,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenPair,
    UserBrief,
)
from app.services.auth import authenticate_user, register_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register")
async def register(
    data: RegisterRequest, db: Annotated[AsyncSession, Depends(get_db)]
) -> RegisterResponse:
    try:
        user, organization, membership = await register_user(db, data)
    except EmailAlreadyRegistered:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    return RegisterResponse(
        user=UserBrief(id=user.id, email=user.email),
        organization=OrgBrief(id=organization.id, name=organization.name),
        role=membership.role,
    )


@router.post("/login")
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenPair:

    user = await authenticate_user(
        db,
        form_data.username.strip().lower(),
        form_data.password,
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_token(
        user_id=user.id,
        token_type="access",
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )

    refresh_token = create_token(
        user_id=user.id,
        token_type="refresh",
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/refresh")
async def refresh(data: RefreshRequest, db: Annotated[AsyncSession, Depends(get_db)]) -> TokenPair:
    try:
        user_id = decode_token(data.refresh_token, "refresh")
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await get_active_user(db, user_id)

    access_token = create_token(
        user_id=user_id,
        token_type="access",
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )

    refresh_token = create_token(
        user_id=user.id,
        token_type="refresh",
        expires_delta=timedelta(
            days=settings.refresh_token_expire_days,
        ),
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.get("/me")
async def me(
    user: CurrentUser,
) -> UserBrief:
    return UserBrief(id=user.id, email=user.email)
