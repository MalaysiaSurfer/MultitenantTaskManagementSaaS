import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AlreadyMember, NotFoundException
from app.db.base import Invitation, Membership, MembershipRole, User
from app.schemas.invitation import InvitationAccepted, InvitationRequest

logger = logging.getLogger(__name__)


async def send_user_invitation(
    db: AsyncSession,
    user_id: int,
    organization_id: int,
    data: InvitationRequest,
    expires_delta: timedelta,
) -> None:
    already_member = await db.scalar(
        select(Membership.id)
        .join(User, User.id == Membership.user_id)
        .where(User.email == data.email, Membership.organization_id == organization_id)
    )
    if already_member is not None:
        return

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = datetime.now(UTC) + expires_delta

    stmt = insert(Invitation).values(
        organization_id=organization_id,
        email=data.email,
        role=data.role,
        token_hash=token_hash,
        invited_by_id=user_id,
        expires_at=expires_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Invitation.organization_id, Invitation.email],
        index_where=Invitation.accepted_at.is_(None),
        set_={
            "role": data.role,
            "token_hash": token_hash,
            "expires_at": expires_at,
            "invited_by_id": user_id,
        },
    )
    await db.execute(stmt)
    await db.commit()

    if settings.environment != "prod":
        logger.info(
            "Invitation for %s to organization %s, token=%s", data.email, organization_id, token
        )
    else:
        logger.info("Invitation created for organization %s", organization_id)


async def accept_user_invitation(
    db: AsyncSession,
    user_id: int,
    email: str,
    token: str,
) -> InvitationAccepted:
    stmt = await db.execute(
        update(Invitation)
        .where(
            Invitation.token_hash == hashlib.sha256(token.encode("utf-8")).hexdigest(),
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > func.now(),
        )
        .values(accepted_at=func.now())
        .returning(
            Invitation.organization_id,
            Invitation.role,
        )
    )
    result = stmt.one_or_none()

    if result is None:
        raise NotFoundException()

    try:
        membership = Membership(
            user_id=user_id,
            organization_id=result.organization_id,
            role=MembershipRole(result.role),
        )
        db.add(membership)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AlreadyMember from None

    return InvitationAccepted(
        organization_id=result.organization_id,
        role=MembershipRole(result.role),
    )
