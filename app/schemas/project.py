from pydantic import BaseModel, Field


class ProjectOut(BaseModel):
    id: int
    organization_id: int
    name: str


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
