import hashlib
import logging
import secrets
from datetime import timedelta

from sqlalchemy import Interval, bindparam, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AlreadyMember, NotFoundException
from app.db.base import Invitation, InvitationRole, Membership, MembershipRole, User
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
    expires_at = func.now() + bindparam("invitation_expiration", expires_delta, type_=Interval())

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
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    invitation = await db.execute(
        select(
            Invitation.id,
            Invitation.organization_id,
            Invitation.role,
            Membership.role.label("inviter_role"),
        )
        .join(
            Membership,
            (Membership.organization_id == Invitation.organization_id)
            & (Membership.user_id == Invitation.invited_by_id),
        )
        .where(
            Invitation.token_hash == token_hash,
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > func.now(),
        )
        .with_for_update(of=Membership)
    )
    invitation = invitation.one_or_none()
    if invitation is None:
        raise NotFoundException()

    inviter_is_authorized = invitation.inviter_role == MembershipRole.OWNER or (
        invitation.role == InvitationRole.MEMBER and invitation.inviter_role == MembershipRole.ADMIN
    )
    if not inviter_is_authorized:
        raise NotFoundException()

    accepted = await db.execute(
        update(Invitation)
        .where(
            Invitation.id == invitation.id,
            Invitation.token_hash == token_hash,
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > func.now(),
        )
        .values(accepted_at=func.now())
        .returning(Invitation.organization_id, Invitation.role)
    )
    result = accepted.one_or_none()

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
