from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.db.base import Membership, MembershipRole, Organization, User


class UserOrganization(NamedTuple):
    id: int
    name: str
    role: MembershipRole


async def get_user_organizations(
    db: AsyncSession,
    user_id: int,
) -> list[UserOrganization]:
    result = await db.execute(
        select(
            Organization.id,
            Organization.name,
            Membership.role,
        )
        .join(
            Membership,
            Membership.organization_id == Organization.id,
        )
        .where(Membership.user_id == user_id)
        .order_by(Organization.id)
    )
    return [
        UserOrganization(
            id=row.id,
            name=row.name,
            role=row.role,
        )
        for row in result
    ]


async def get_organization_members(
    db: AsyncSession, organization_id: int
) -> list[tuple[int, str, MembershipRole]]:
    result = await db.execute(
        select(Membership.user_id, User.email, Membership.role)
        .join(User)
        .where(Membership.organization_id == organization_id)
        .order_by(Membership.user_id)
    )
    return [(row.user_id, row.email, row.role) for row in result]


async def update_member_role(
    db: AsyncSession,
    organization_id: int,
    actor_user_id: int,
    user_id: int,
    role: MembershipRole,
) -> None:
    await db.execute(
        select(Organization.id).where(Organization.id == organization_id).with_for_update()
    )
    actor_role = await db.scalar(
        select(Membership.role).where(
            Membership.organization_id == organization_id,
            Membership.user_id == actor_user_id,
        )
    )
    if actor_role != MembershipRole.OWNER:
        raise PermissionError("insufficient permissions")
    result = await db.execute(
        select(Membership).where(
            Membership.organization_id == organization_id, Membership.user_id == user_id
        )
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise NotFoundException()
    if target.role == MembershipRole.OWNER and role != MembershipRole.OWNER:
        owners = await db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.organization_id == organization_id,
                Membership.role == MembershipRole.OWNER,
                Membership.user_id != user_id,
            )
        )
        if not owners:
            raise ValueError("Organization must have at least one owner")
    target.role = role
    await db.commit()


async def remove_member(
    db: AsyncSession,
    organization_id: int,
    actor_user_id: int,
    user_id: int,
    self_leave: bool = False,
) -> None:
    await db.execute(
        select(Organization.id).where(Organization.id == organization_id).with_for_update()
    )
    actor_role = await db.scalar(
        select(Membership.role).where(
            Membership.organization_id == organization_id,
            Membership.user_id == actor_user_id,
        )
    )
    if actor_role is None:
        raise NotFoundException()
    if not self_leave and actor_role not in {MembershipRole.ADMIN, MembershipRole.OWNER}:
        raise PermissionError("insufficient permissions")
    result = await db.execute(
        select(Membership).where(
            Membership.organization_id == organization_id, Membership.user_id == user_id
        )
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise NotFoundException()
    if actor_role == MembershipRole.ADMIN and target.role != MembershipRole.MEMBER:
        raise PermissionError("insufficient permissions")
    if target.role == MembershipRole.OWNER:
        owners = await db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.organization_id == organization_id,
                Membership.role == MembershipRole.OWNER,
            )
        )
        if owners <= 1:
            raise ValueError("Organization must have at least one owner")
    await db.delete(target)
    await db.commit()
