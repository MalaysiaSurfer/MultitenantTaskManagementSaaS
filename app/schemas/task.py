from typing import Annotated, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.core.constants import INT4_MAX
from app.db.base import TaskStatus
from app.schemas.pagination import Page

TaskTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
TaskDescription = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]


class OrganizationTask(NamedTuple):
    id: int
    organization_id: int
    project_id: int
    title: str
    description: str
    status: TaskStatus
    assignee_id: int | None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organization_id: int
    project_id: int
    title: str
    description: str
    status: TaskStatus
    assignee_id: int | None


TasksPage = Page[TaskOut]


class TaskFilters(BaseModel):
    status: TaskStatus | None = Field(default=None)
    assignee_id: int | None = Field(default=None, ge=1, le=INT4_MAX)
    project_id: int | None = Field(default=None, ge=1, le=INT4_MAX)
    unassigned: bool = Field(default=False)


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(ge=1, le=INT4_MAX)
    title: TaskTitle
    description: TaskDescription
    assignee_id: int | None = Field(ge=1, le=INT4_MAX, default=None)


class UpdateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: TaskTitle | None = None
    description: TaskDescription | None = None
    status: TaskStatus | None = None
    assignee_id: int | None = Field(default=None, ge=1, le=INT4_MAX)

    @model_validator(mode="after")
    def check_fields(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        for name in ("title", "description", "status"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self
