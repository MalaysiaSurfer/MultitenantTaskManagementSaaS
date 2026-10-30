import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from typing import NamedTuple

from sqlalchemy import Interval, bindparam, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AlreadyMember, NotFoundException
from app.db.base import Invitation, InvitationRole, Membership, MembershipRole, Organization, User
from app.schemas.invitation import InvitationAccepted, InvitationRequest
from app.schemas.pagination import Page
from app.services.mailer import MAIL_DELIVERY_ERRORS, mail_delivery_enabled, send_email

logger = logging.getLogger(__name__)


class InvitationDelivery(NamedTuple):
    email: str
    organization_id: int
    organization_name: str
    token: str


class PendingInvitation(NamedTuple):
    id: int
    email: str
    role: InvitationRole
    invited_by_id: int
    expires_at: datetime
    created_at: datetime


async def send_user_invitation(
    db: AsyncSession,
    user_id: int,
    organization_id: int,
    data: InvitationRequest,
    expires_delta: timedelta,
) -> InvitationDelivery | None:
    already_member = await db.scalar(
        select(Membership.id)
        .join(User, User.id == Membership.user_id)
        .where(User.email == data.email, Membership.organization_id == organization_id)
    )
    if already_member is not None:
        return None

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
    organization_name = await db.scalar(
        select(Organization.name).where(Organization.id == organization_id)
    )
    await db.commit()
    logger.info("Invitation created for organization %s", organization_id)
    return InvitationDelivery(data.email, organization_id, organization_name, token)


def _invitation_email_body(delivery: InvitationDelivery) -> str:
    if settings.invitation_accept_url is not None:
        action = "Accept it here: " + settings.invitation_accept_url.format(token=delivery.token)
    else:
        action = (
            "Sign in or register with this email and accept the invitation with token:\n"
            + delivery.token
        )
    return (
        f"You have been invited to join {delivery.organization_name}.\n\n"
        f"{action}\n\n"
        f"The invitation expires in {settings.invitation_expire_days} days."
    )


async def deliver_invitation(delivery: InvitationDelivery) -> None:
    if not mail_delivery_enabled():
        if settings.environment != "prod":
            logger.info(
                "Invitation for %s to organization %s, token=%s",
                delivery.email,
                delivery.organization_id,
                delivery.token,
            )
        else:
            logger.warning(
                "SMTP is not configured; invitation for organization %s was not delivered",
                delivery.organization_id,
            )
        return
    try:
        await send_email(
            delivery.email,
            f"Invitation to {delivery.organization_name}",
            _invitation_email_body(delivery),
        )
    except MAIL_DELIVERY_ERRORS:
        logger.error(
            "Could not deliver invitation for organization %s",
            delivery.organization_id,
            exc_info=True,
        )
        return
    logger.info("Invitation delivered for organization %s", delivery.organization_id)


async def get_pending_invitations(
    db: AsyncSession,
    organization_id: int,
    limit: int = 20,
    cursor: int | None = None,
) -> Page[PendingInvitation]:
    stmt = (
        select(
            Invitation.id,
            Invitation.email,
            Invitation.role,
            Invitation.invited_by_id,
            Invitation.expires_at,
            Invitation.created_at,
        )
        .where(
            Invitation.organization_id == organization_id,
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > func.now(),
        )
        .order_by(Invitation.id)
        .limit(limit + 1)
    )
    if cursor is not None:
        stmt = stmt.where(Invitation.id > cursor)
    rows = (await db.execute(stmt)).all()
    has_more = len(rows) > limit
    items = [PendingInvitation(*row) for row in rows[:limit]]
    return Page(items=items, next_cursor=items[-1].id if has_more else None)


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
