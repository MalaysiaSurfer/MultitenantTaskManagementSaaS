from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.db.session import get_db
from app.schemas.organization import OrganizationOut
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
