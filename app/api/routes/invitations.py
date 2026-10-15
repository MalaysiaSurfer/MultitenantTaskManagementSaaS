from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.exceptions import AlreadyMember
from app.db.session import get_db
from app.schemas.invitation import AcceptInvitationRequest, InvitationAccepted
from app.services.invitation import accept_user_invitation

router = APIRouter(prefix="/invitations", tags=["invitations"])


@router.post("/accept")
async def accept_invitation(
    data: AcceptInvitationRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InvitationAccepted:

    try:
        return await accept_user_invitation(
            db=db,
            user_id=current_user.id,
            email=current_user.email,
            token=data.token,
        )

    except AlreadyMember:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="already a member of the organization",
        ) from None
