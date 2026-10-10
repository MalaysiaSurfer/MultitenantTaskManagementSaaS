from pydantic import BaseModel

from app.db.base import MembershipRole


class OrganizationOut(BaseModel):
    id: int
    name: str
    role: MembershipRole
