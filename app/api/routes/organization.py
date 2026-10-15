from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, OrgAdmin
from app.core.config import settings
from app.db.base import InvitationRole, MembershipRole
from app.db.session import get_db
from app.schemas.invitation import InvitationRequest, InvitationSent
from app.schemas.organization import OrganizationOut
from app.services.invitation import send_user_invitation
from app.services.organization import get_user_organizations

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
