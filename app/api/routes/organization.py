from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, OrgAdmin, OrgMember, OrgOwner, ResourceID
from app.core.config import settings
from app.core.exceptions import NotFoundException
from app.db.base import InvitationRole, Membership, MembershipRole, User
from app.db.session import get_db
from app.schemas.invitation import InvitationRequest, InvitationSent
from app.schemas.organization import (
    MembershipRoleUpdate,
    OrganizationMemberOut,
    OrganizationOut,
)
from app.services.invitation import send_user_invitation
from app.services.organization import (
    get_organization_members,
    get_user_organizations,
    remove_member,
    update_member_role,
)

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("")
async def get_organizations(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[OrganizationOut]:
    organizations = await get_user_organizations(
        db=db,
        user_id=current_user.id,
    )

    return [
        OrganizationOut(
            id=organization.id,
            name=organization.name,
            role=organization.role,
        )
        for organization in organizations
    ]


@router.post("/invite", status_code=status.HTTP_202_ACCEPTED)
async def send_invitation(
    membership: OrgAdmin,
    data: InvitationRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InvitationSent:
    if membership.role == MembershipRole.ADMIN and data.role != InvitationRole.MEMBER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="insufficient permissions"
        )
    await send_user_invitation(
        db=db,
        user_id=membership.user_id,
        organization_id=membership.organization_id,
        data=data,
        expires_delta=timedelta(days=settings.invitation_expire_days),
    )
    return InvitationSent()


@router.get("/members")
async def list_members(
    membership: OrgMember, db: Annotated[AsyncSession, Depends(get_db)]
) -> list[OrganizationMemberOut]:
    members = await get_organization_members(db, membership.organization_id)
    return [
        OrganizationMemberOut(user_id=user_id, email=email, role=role)
        for user_id, email, role in members
    ]


@router.patch("/members/{user_id}", status_code=status.HTTP_200_OK)
async def change_member_role(
    membership: OrgOwner,
    user_id: ResourceID,
    data: MembershipRoleUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrganizationMemberOut:
    try:
        await update_member_role(
            db, membership.organization_id, membership.user_id, user_id, data.role
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="insufficient permissions"
        ) from None
    result = await db.execute(
        select(Membership.user_id, User.email, Membership.role)
        .join(User)
        .where(
            Membership.organization_id == membership.organization_id,
            Membership.user_id == user_id,
        )
    )
    row = result.one_or_none()
    if row is None:
        raise NotFoundException()
    return OrganizationMemberOut(user_id=row.user_id, email=row.email, role=row.role)


@router.delete("/members/me", status_code=status.HTTP_204_NO_CONTENT)
async def leave_organization(
    user: CurrentUser,
    membership: OrgMember,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    try:
        await remove_member(db, membership.organization_id, user.id, user.id, self_leave=True)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member(
    membership: OrgAdmin,
    user_id: ResourceID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    try:
        await remove_member(
            db,
            membership.organization_id,
            membership.user_id,
            user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="insufficient permissions"
        ) from None
