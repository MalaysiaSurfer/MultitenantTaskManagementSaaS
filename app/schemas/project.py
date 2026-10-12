from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field


class OrganizationProject(NamedTuple):
    id: int
    organization_id: int
    name: str


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organization_id: int
    name: str


class ProjectsPage(BaseModel):
    items: list[ProjectOut]
    next_cursor: int | None


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
