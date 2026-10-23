from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.db.base import MembershipRole
from app.schemas.pagination import Page

OrganizationName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


class OrganizationOut(BaseModel):
    id: int
    name: str
    role: MembershipRole


class OrganizationCreate(BaseModel):
    name: OrganizationName


class OrganizationUpdate(BaseModel):
    name: OrganizationName


class OrganizationMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    email: str
    role: MembershipRole


class MembershipRoleUpdate(BaseModel):
    role: Literal[MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.MEMBER]


MembersPage = Page[OrganizationMemberOut]
