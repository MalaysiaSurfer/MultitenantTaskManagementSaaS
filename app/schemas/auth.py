from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.db.base import MembershipRole

OrganizationName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


class UserBrief(BaseModel):
    id: int
    email: EmailStr


class OrgBrief(BaseModel):
    id: int
    name: str


class RegisterResponse(BaseModel):
    user: UserBrief
    organization: OrgBrief
    role: MembershipRole


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    organization_name: OrganizationName

    @field_validator("email", mode="before")
    @classmethod
    def convert_to_lower(cls, v: str) -> EmailStr:
        if isinstance(v, str):
            return v.lower()
        return v


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
