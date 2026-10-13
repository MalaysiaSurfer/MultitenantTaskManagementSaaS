from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import OrgAdmin, OrgMember, Pagination, ResourceID, TaskFiltersDep
from app.db.session import get_db
from app.schemas.task import CreateTaskRequest, TaskOut, TasksPage, UpdateTaskRequest
from app.services.task import (
    create_project_task,
    delete_organization_task,
    get_organization_task,
    get_organization_tasks,
    update_organization_task,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task(
    membership: OrgAdmin,
    data: CreateTaskRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TaskOut:
    task = await create_project_task(
        db=db,
        organization_id=membership.organization_id,
        data=data,
    )

    return TaskOut(
        id=task.id,
        organization_id=task.organization_id,
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        status=task.status,
        assignee_id=task.assignee_id,
    )


@router.get("")
async def get_tasks(
    membership: OrgMember,
    db: Annotated[AsyncSession, Depends(get_db)],
    pagination: Pagination,
    filters: TaskFiltersDep,
) -> TasksPage:
    page = await get_organization_tasks(
        db=db,
        organization_id=membership.organization_id,
        status=filters.status,
        assignee_id=filters.assignee_id,
        project_id=filters.project_id,
        unassigned=filters.unassigned,
        limit=pagination.limit,
        cursor=pagination.cursor,
    )

    return TasksPage(
        items=page.items,
        next_cursor=page.next_cursor,
    )


@router.get("/{task_id}")
async def get_task(
    task_id: ResourceID,
    membership: OrgMember,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TaskOut:
    task = await get_organization_task(
        db=db,
        organization_id=membership.organization_id,
        task_id=task_id,
    )

    return TaskOut(
        id=task.id,
        organization_id=task.organization_id,
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        status=task.status,
        assignee_id=task.assignee_id,
    )


@router.patch("/{task_id}")
async def update_task(
    task_id: ResourceID,
    membership: OrgAdmin,
    data: UpdateTaskRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TaskOut:
    task = await update_organization_task(
        db=db,
        organization_id=membership.organization_id,
        data=data,
        task_id=task_id,
    )

    return TaskOut(
        id=task.id,
        organization_id=task.organization_id,
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        status=task.status,
        assignee_id=task.assignee_id,
    )


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_task(
    task_id: ResourceID,
    membership: OrgAdmin,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await delete_organization_task(
        db=db,
        organization_id=membership.organization_id,
        task_id=task_id,
    )
