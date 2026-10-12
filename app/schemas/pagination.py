from pydantic import BaseModel, Field

from app.core.constants import INT4_MAX
from app.schemas.project import OrganizationProject
from app.schemas.task import OrganizationTask


class PaginationParams(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    cursor: int | None = Field(default=None, ge=1, le=INT4_MAX)


class Page(BaseModel):
    items: list[OrganizationProject] | list[OrganizationTask]
    next_cursor: int | None
