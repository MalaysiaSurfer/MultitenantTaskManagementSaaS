from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Membership, MembershipRole, Organization


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
