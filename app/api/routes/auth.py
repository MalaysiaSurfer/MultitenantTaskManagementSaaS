import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from jwt.exceptions import InvalidTokenError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_active_user, get_redis
from app.api.rate_limit import (
    REDIS_UNAVAILABLE_ERRORS,
    clear_rate_limit_attempt,
    complete_login_success,
    login_rate_limit,
    reserve_rate_limit_attempt,
)
from app.core.config import settings
from app.core.exceptions import EmailAlreadyRegistered
from app.core.security import create_token, decode_token_payload, password_hash
from app.db.session import get_db
from app.schemas.auth import (
    OrgBrief,
    PasswordChangeRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenPair,
    UserBrief,
)
from app.services.auth import authenticate_user, register_user

router = APIRouter(prefix="/auth", tags=["auth"])


async def _revoke_all_user_sessions(redis: Redis, user_id: int) -> None:
    key = f"jwt:session-version:{user_id}"
    await redis.incr(key)
    await redis.expire(key, settings.refresh_token_expire_days * 24 * 60 * 60)


async def _get_session_version(redis: Redis, user_id: int) -> int:
    version = await redis.get(f"jwt:session-version:{user_id}")
    return int(version or 0)


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    data: RegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
    redis: Annotated[Redis, Depends(get_redis)],
) -> RegisterResponse:
    await reserve_rate_limit_attempt(
        request, data.email, redis, "register", "Too many registration attempts"
    )
    try:
        user, organization, membership = await register_user(db, data)
    except EmailAlreadyRegistered:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        ) from None

    await clear_rate_limit_attempt(redis, request, data.email, "register")

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

    try:
        session_version = await _get_session_version(redis, user.id)
    except REDIS_UNAVAILABLE_ERRORS:
        # Refresh fails closed while Redis is unavailable. A zero-version token
        # also stays invalid if Redis later reports a revocation generation.
        session_version = 0

    access_token = create_token(
        user_id=user.id,
        token_type="access",
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
        session_version=session_version,
    )

    refresh_token = create_token(
        user_id=user.id,
        token_type="refresh",
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
        session_version=session_version,
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
        session_version = await _get_session_version(redis, user_id)
    except REDIS_UNAVAILABLE_ERRORS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from None
    if is_revoked:
        if is_revoked == "rotated":
            try:
                await _revoke_all_user_sessions(redis, user_id)
            except REDIS_UNAVAILABLE_ERRORS:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Authentication service unavailable",
                ) from None
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if int(payload.get("sv", 0)) < session_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await get_active_user(db, user_id)
    try:
        stored = await redis.set(revoked_key, "rotated", ex=remaining_seconds, nx=True)
    except REDIS_UNAVAILABLE_ERRORS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from None
    if not stored:
        try:
            await _revoke_all_user_sessions(redis, user_id)
        except REDIS_UNAVAILABLE_ERRORS:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service unavailable",
            ) from None
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_token(
        user_id=user_id,
        token_type="access",
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
        session_version=session_version,
    )

    refresh_token = create_token(
        user_id=user.id,
        token_type="refresh",
        expires_delta=timedelta(
            days=settings.refresh_token_expire_days,
        ),
        session_version=session_version,
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    data: RefreshRequest,
    redis: Annotated[Redis, Depends(get_redis)],
) -> None:
    try:
        payload = decode_token_payload(data.refresh_token, "refresh")
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    remaining_seconds = int(payload["exp"] - datetime.now(UTC).timestamp())
    if remaining_seconds <= 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        await redis.set(f"jwt:denylist:{payload['jti']}", "logout", ex=remaining_seconds, nx=True)
    except REDIS_UNAVAILABLE_ERRORS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from None


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    user: CurrentUser,
    redis: Annotated[Redis, Depends(get_redis)],
) -> None:
    try:
        await _revoke_all_user_sessions(redis, user.id)
    except REDIS_UNAVAILABLE_ERRORS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from None


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    data: PasswordChangeRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> None:
    valid, _ = await asyncio.to_thread(
        password_hash.verify_and_update, data.current_password, user.hashed_password
    )
    if not valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    try:
        await _revoke_all_user_sessions(redis, user.id)
    except REDIS_UNAVAILABLE_ERRORS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from None
    user.hashed_password = await asyncio.to_thread(password_hash.hash, data.new_password)
    await db.commit()


@router.get("/me")
async def me(
    user: CurrentUser,
) -> UserBrief:
    return UserBrief(id=user.id, email=user.email)
