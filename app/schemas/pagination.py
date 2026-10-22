from pydantic import BaseModel, Field

from app.core.constants import INT4_MAX


class PaginationParams(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    cursor: int | None = Field(default=None, ge=1, le=INT4_MAX)


class Page[T](BaseModel):
    items: list[T]
    next_cursor: int | None
