from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.constants import INT4_MAX
from app.db.base import TaskStatus


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


class TasksPage(BaseModel):
    items: list[TaskOut]
    next_cursor: int | None


class TaskFilters(BaseModel):
    status: TaskStatus | None = Field(default=None)
    assignee_id: int | None = Field(default=None, ge=1, le=INT4_MAX)
    project_id: int | None = Field(default=None, ge=1, le=INT4_MAX)
    unassigned: bool = Field(default=False)


class CreateTaskRequest(BaseModel):
    project_id: int = Field(ge=1, le=INT4_MAX)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)
    assignee_id: int | None = Field(ge=1, le=INT4_MAX, default=None)
