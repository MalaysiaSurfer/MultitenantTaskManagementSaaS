from typing import Annotated, NamedTuple

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.schemas.pagination import Page

ProjectName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class OrganizationProject(NamedTuple):
    id: int
    organization_id: int
    name: str


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organization_id: int
    name: str


ProjectsPage = Page[ProjectOut]


class CreateProjectRequest(BaseModel):
    name: ProjectName


class ProjectUpdate(BaseModel):
    name: ProjectName
