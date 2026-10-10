from pydantic import BaseModel, Field

from app.core.constants import INT4_MAX
from app.db.base import TaskStatus


class TaskOut(BaseModel):
    id: int
    organization_id: int
    project_id: int
    title: str
    description: str
    status: TaskStatus
    assignee_id: int | None


class CreateTaskRequest(BaseModel):
    project_id: int = Field(ge=1, le=INT4_MAX)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)
    assignee_id: int | None = Field(ge=1, le=INT4_MAX, default=None)
