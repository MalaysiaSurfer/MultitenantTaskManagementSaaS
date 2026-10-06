from pydantic import BaseModel, EmailStr, Field, field_validator


class UserBrief(BaseModel):
    id: int
    email: EmailStr


class OrgBrief(BaseModel):
    id: int
    name: str


class RegisterResponse(BaseModel):
    user: UserBrief
    organization: OrgBrief
    role: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    organization_name: str = Field(min_length=1, max_length=100)

    @field_validator("email", mode="before")
    @classmethod
    def convert_to_lower(cls, v: str) -> EmailStr:
        if isinstance(v, str):
            return v.lower()
        return v


class RefreshRequest(BaseModel):
    refresh_token: str
