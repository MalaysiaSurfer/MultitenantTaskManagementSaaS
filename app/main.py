import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pydantic import BaseModel, EmailStr, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Membership, MembershipRole, Organization, User
from app.db.session import async_session, engine, get_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with async_session() as session:
        result = await session.execute(text("SELECT 1"))
        logger.info("DB connection ok: %s", result.scalar())
    yield
    await engine.dispose()


class Settings(BaseSettings):
    secret_key: str
    access_token_expire_minutes: int
    refresh_token_expire_days: int

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()

ALGORITHM = "HS256"


class UserBrief(BaseModel):
    id: int
    email: EmailStr


class OrgBrief(BaseModel):
    id: int
    name: str


class RegisterResponse(BaseModel):
    user: UserBrief
    organization: OrgBrief
    role: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    organization_name: str = Field(min_length=1, max_length=100)

    @field_validator("email", mode="before")
    @classmethod
    def convert_to_lower(cls, v: str) -> EmailStr:
        if isinstance(v, str):
            return v.lower()
        return v


class RefreshRequest(BaseModel):
    refresh_token: str


password_hash = PasswordHash.recommended()

DUMMY_HASH = password_hash.hash("dummypassword")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

app = FastAPI(lifespan=lifespan)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)


async def get_user(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def authenticate_user(
    db: AsyncSession,
    email: str,
    password: str,
) -> User | None:

    user = await get_user(db, email)

    if not user:
        await asyncio.to_thread(verify_password, password, DUMMY_HASH)
        return None

    if not await asyncio.to_thread(verify_password, password, user.hashed_password):
        return None

    if not user.is_active:
        return None

    return user


def create_token(
    user_id: int,
    token_type: str,
    expires_delta: timedelta,
) -> str:

    expire = datetime.now(UTC) + expires_delta

    payload = {
        "sub": str(user_id),
        "type": token_type,
        "exp": expire,
        "iat": datetime.now(UTC),
        "jti": str(uuid.uuid4()),
    }

    return jwt.encode(
        payload,
        settings.secret_key,
        algorithm=ALGORITHM,
    )


def decode_token(token: str, expected_type: str) -> int:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub", "type", "iat", "jti"]},
        )
        if payload["type"] != expected_type:
            raise InvalidTokenError("wrong token type")
        return int(payload["sub"])
    except ValueError:
        raise InvalidTokenError("bad subject") from None


async def get_active_user(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    token: Annotated[str, Depends(oauth2_scheme)],
) -> User:
    try:
        user_id = decode_token(token, "access")
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await get_active_user(db, user_id)

    return user


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/auth/register")
async def register(
    data: RegisterRequest, db: Annotated[AsyncSession, Depends(get_db)]
) -> RegisterResponse:
    user = User(
        email=data.email,
        hashed_password=await asyncio.to_thread(password_hash.hash, data.password),
    )

    organization = Organization(
        name=data.organization_name,
    )

    try:
        db.add(user)
        db.add(organization)
        await db.flush()

        membership = Membership(
            user_id=user.id,
            organization_id=organization.id,
            role=MembershipRole.OWNER,
        )

        db.add(membership)
        await db.commit()

    except IntegrityError:
        await db.rollback()

        raise HTTPException(
            status_code=409,
            detail="Email already registered",
        )

    return RegisterResponse(
        user=UserBrief(id=user.id, email=user.email),
        organization=OrgBrief(id=organization.id, name=organization.name),
        role=membership.role,
    )


@app.post("/auth/login")
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


@app.post("/auth/refresh")
async def refresh(
    data: RefreshRequest, db: Annotated[AsyncSession, Depends(get_db)]
) -> TokenPair:
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


@app.get("/auth/me")
async def get_me(
    user: Annotated[User, Depends(get_current_user)],
) -> UserBrief:
    return UserBrief(id=user.id, email=user.email)
