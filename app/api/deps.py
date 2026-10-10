from typing import Annotated

from fastapi import Depends, Header, HTTPException, Path, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import INT4_MAX
from app.core.exceptions import NotFoundException
from app.core.security import decode_token
from app.db.base import Membership, MembershipRole, User
from app.db.session import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


ROLE_RANKS: dict[MembershipRole, int] = {
    MembershipRole.MEMBER: 1,
    MembershipRole.ADMIN: 2,
    MembershipRole.OWNER: 3,
}


def require_role(min_role: MembershipRole):
    async def dependency(
        membership: Annotated[
            Membership,
            Depends(get_current_membership),
        ],
    ) -> Membership:
        if ROLE_RANKS[membership.role] < ROLE_RANKS[min_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient permissions",
            )
        return membership

    return dependency


async def get_active_user(db: AsyncSession, user_id: int) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    token: Annotated[str, Depends(oauth2_scheme)],
) -> User:
    try:
        user_id = decode_token(token, "access")
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await get_active_user(db, user_id)

    return user


async def get_current_membership(
    organization_id: Annotated[int, Header(alias="X-Organization-ID", ge=1, le=INT4_MAX)],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Membership:
    result = await db.execute(
        select(Membership).where(
            Membership.user_id == current_user.id,
            Membership.organization_id == organization_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise NotFoundException()
    return membership


CurrentUser = Annotated[User, Depends(get_current_user)]

OrgMember = Annotated[Membership, Depends(require_role(MembershipRole.MEMBER))]

OrgAdmin = Annotated[Membership, Depends(require_role(MembershipRole.ADMIN))]

OrgOwner = Annotated[Membership, Depends(require_role(MembershipRole.OWNER))]

ResourceID = Annotated[
    int,
    Path(ge=1, le=INT4_MAX),
]
