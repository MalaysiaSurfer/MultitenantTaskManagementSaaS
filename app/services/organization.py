from typing import NamedTuple

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InsufficientPermissions, LastOwnerError, NotFoundException
from app.db.base import Membership, MembershipRole, Organization, User
from app.schemas.pagination import Page


class UserOrganization(NamedTuple):
    id: int
    name: str
    role: MembershipRole


class OrganizationMember(NamedTuple):
    user_id: int
    email: str
    role: MembershipRole


async def create_organization(db: AsyncSession, user_id: int, name: str) -> Organization:
    organization = Organization(name=name)
    db.add(organization)
    await db.flush()
    db.add(
        Membership(
            user_id=user_id,
            organization_id=organization.id,
            role=MembershipRole.OWNER,
        )
    )
    await db.commit()
    return organization


async def rename_organization(
    db: AsyncSession, organization_id: int, user_id: int, name: str
) -> Organization:
    await db.execute(
        select(Organization.id).where(Organization.id == organization_id).with_for_update()
    )
    actor_role = await db.scalar(
        select(Membership.role).where(
            Membership.organization_id == organization_id,
            Membership.user_id == user_id,
        )
    )
    if actor_role != MembershipRole.OWNER:
        raise InsufficientPermissions
    result = await db.execute(
        update(Organization)
        .where(Organization.id == organization_id)
        .values(name=name)
        .returning(Organization.id, Organization.name)
    )
    organization = result.one_or_none()
    if organization is None:
        raise NotFoundException()
    await db.commit()
    return Organization(id=organization.id, name=organization.name)


async def delete_organization(db: AsyncSession, organization_id: int, user_id: int) -> None:
    await db.execute(
        select(Organization.id).where(Organization.id == organization_id).with_for_update()
    )
    actor_role = await db.scalar(
        select(Membership.role).where(
            Membership.organization_id == organization_id,
            Membership.user_id == user_id,
        )
    )
    if actor_role != MembershipRole.OWNER:
        raise InsufficientPermissions
    result = await db.execute(delete(Organization).where(Organization.id == organization_id))
    if result.rowcount == 0:
        raise NotFoundException()
    await db.commit()


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
    db: AsyncSession,
    organization_id: int,
    limit: int = 20,
    cursor: int | None = None,
) -> Page[OrganizationMember]:
    stmt = (
        select(Membership.user_id, User.email, Membership.role)
        .join(User)
        .where(Membership.organization_id == organization_id)
        .order_by(Membership.user_id)
        .limit(limit + 1)
    )
    if cursor is not None:
        stmt = stmt.where(Membership.user_id > cursor)
    result = await db.execute(stmt)
    rows = result.all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [OrganizationMember(row.user_id, row.email, row.role) for row in rows]
    return Page(items=items, next_cursor=items[-1].user_id if has_more else None)


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
        raise InsufficientPermissions
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
            raise LastOwnerError
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
        raise InsufficientPermissions
    result = await db.execute(
        select(Membership).where(
            Membership.organization_id == organization_id, Membership.user_id == user_id
        )
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise NotFoundException()
    if actor_role == MembershipRole.ADMIN and target.role != MembershipRole.MEMBER:
        raise InsufficientPermissions
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
            raise LastOwnerError
    await db.delete(target)
    await db.commit()
