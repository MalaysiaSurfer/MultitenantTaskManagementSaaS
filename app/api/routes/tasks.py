from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import OrgMember, ResourceID
from app.db.session import get_db
from app.schemas.task import CreateTaskRequest, TaskOut
from app.services.task import (
    create_project_task,
    get_organization_task,
    get_organization_tasks,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task(
    membership: OrgMember,
    data: CreateTaskRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TaskOut:
    task = await create_project_task(db, membership.organization_id, data)

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
) -> list[TaskOut]:
    tasks = await get_organization_tasks(
        db=db,
        organization_id=membership.organization_id,
    )

    return [
        TaskOut(
            id=task.id,
            organization_id=task.organization_id,
            project_id=task.project_id,
            title=task.title,
            description=task.description,
            status=task.status,
            assignee_id=task.assignee_id,
        )
        for task in tasks
    ]


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
