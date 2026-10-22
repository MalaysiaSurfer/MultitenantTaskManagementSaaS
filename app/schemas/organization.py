from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.db.base import MembershipRole


class OrganizationOut(BaseModel):
    id: int
    name: str
    role: MembershipRole


class OrganizationMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    email: str
    role: MembershipRole


class MembershipRoleUpdate(BaseModel):
    role: Literal[MembershipRole.ADMIN, MembershipRole.MEMBER]
