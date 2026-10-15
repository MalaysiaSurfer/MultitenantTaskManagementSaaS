from pydantic import BaseModel, EmailStr, Field, field_validator

from app.db.base import InvitationRole, MembershipRole


class InvitationRequest(BaseModel):
    email: EmailStr
    role: InvitationRole

    @field_validator("email", mode="before")
    @classmethod
    def convert_to_lower(cls, v: str) -> EmailStr:
        if isinstance(v, str):
            return v.lower()
        return v


class InvitationSent(BaseModel):
    detail: str = "invitation processed"


class AcceptInvitationRequest(BaseModel):
    token: str = Field(min_length=1, max_length=128)


class InvitationAccepted(BaseModel):
    organization_id: int
    role: MembershipRole
