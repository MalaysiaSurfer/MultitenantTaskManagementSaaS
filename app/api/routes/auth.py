from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from jwt.exceptions import InvalidTokenError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_active_user
from app.api.rate_limit import (
    REDIS_UNAVAILABLE_ERRORS,
    complete_login_success,
    get_redis,
    login_rate_limit,
)
from app.core.config import settings
from app.core.exceptions import EmailAlreadyRegistered
from app.core.security import create_token, decode_token_payload
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


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    data: RegisterRequest, db: Annotated[AsyncSession, Depends(get_db)]
) -> RegisterResponse:
    try:
        user, organization, membership = await register_user(db, data)
    except EmailAlreadyRegistered:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        ) from None

    return RegisterResponse(
        user=UserBrief(id=user.id, email=user.email),
        organization=OrgBrief(id=organization.id, name=organization.name),
        role=membership.role,
    )


@router.post("/login")
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
    redis: Annotated[Redis, Depends(get_redis)],
    _rate_limit: Annotated[None, Depends(login_rate_limit)],
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

    await complete_login_success(redis, request, form_data.username)

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
async def refresh(
    data: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> TokenPair:
    try:
        payload = decode_token_payload(data.refresh_token, "refresh")
        user_id = int(payload["sub"])
    except (InvalidTokenError, ValueError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    revoked_key = f"jwt:denylist:{payload['jti']}"
    remaining_seconds = int(payload["exp"] - datetime.now(UTC).timestamp())
    if remaining_seconds <= 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        is_revoked = await redis.get(revoked_key)
    except REDIS_UNAVAILABLE_ERRORS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from None
    if is_revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await get_active_user(db, user_id)
    try:
        stored = await redis.set(revoked_key, "1", ex=remaining_seconds, nx=True)
    except REDIS_UNAVAILABLE_ERRORS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from None
    if not stored:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

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
