import asyncio

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import DUMMY_HASH, password_hash, verify_password
from app.db.base import Membership, MembershipRole, Organization, User
from app.schemas.auth import RegisterRequest


class EmailAlreadyRegistered(Exception):
    pass


async def register_user(
    db: AsyncSession, data: RegisterRequest
) -> tuple[User, Organization, Membership]:
    hashed = await asyncio.to_thread(password_hash.hash, data.password)

    user = User(email=data.email, hashed_password=hashed)
    organization = Organization(name=data.organization_name)

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
        raise EmailAlreadyRegistered from None

    return user, organization, membership


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
