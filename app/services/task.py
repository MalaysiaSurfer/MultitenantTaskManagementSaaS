import logging
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.db.base import Membership, Project, Task, TaskStatus
from app.schemas.task import CreateTaskRequest

logger = logging.getLogger(__name__)


class OrganizationTask(NamedTuple):
    id: int
    organization_id: int
    project_id: int
    title: str
    description: str
    status: TaskStatus
    assignee_id: int | None


async def create_project_task(
    db: AsyncSession,
    organization_id: int,
    data: CreateTaskRequest,
) -> OrganizationTask:
    result = await db.execute(
        select(Project.id).where(
            Project.organization_id == organization_id,
            Project.id == data.project_id,
        )
    )
    project_id = result.scalar_one_or_none()
    if project_id is None:
        raise NotFoundException()
    if data.assignee_id is not None:
        result = await db.execute(
            select(Membership.id).where(
                Membership.user_id == data.assignee_id,
                Membership.organization_id == organization_id,
            )
        )
        membership_id = result.scalar_one_or_none()
        if membership_id is None:
            raise NotFoundException()

    task = Task(
        organization_id=organization_id,
        project_id=project_id,
        title=data.title,
        description=data.description,
        assignee_id=data.assignee_id,
    )

    try:
        db.add(task)
        await db.commit()
    except IntegrityError as exc:
        logger.warning(
            "IntegrityError while creating task: %s",
            exc,
            exc_info=True,
        )
        await db.rollback()
        raise NotFoundException() from exc

    return OrganizationTask(
        id=task.id,
        organization_id=task.organization_id,
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        status=task.status,
        assignee_id=task.assignee_id,
    )


async def get_organization_tasks(
    db: AsyncSession,
    organization_id: int,
) -> list[OrganizationTask]:
    result = await db.execute(
        select(
            Task.id,
            Task.organization_id,
            Task.project_id,
            Task.title,
            Task.description,
            Task.status,
            Task.assignee_id,
        )
        .where(Task.organization_id == organization_id)
        .order_by(Task.id)
    )
    return [
        OrganizationTask(
            id=row.id,
            organization_id=row.organization_id,
            project_id=row.project_id,
            title=row.title,
            description=row.description,
            status=row.status,
            assignee_id=row.assignee_id,
        )
        for row in result
    ]


async def get_organization_task(
    db: AsyncSession,
    organization_id: int,
    task_id: int,
) -> OrganizationTask:
    result = await db.execute(
        select(
            Task.id,
            Task.organization_id,
            Task.project_id,
            Task.title,
            Task.description,
            Task.status,
            Task.assignee_id,
        ).where(Task.id == task_id, Task.organization_id == organization_id)
    )
    task = result.one_or_none()
    if task is None:
        raise NotFoundException()

    return OrganizationTask(
        id=task.id,
        organization_id=task.organization_id,
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        status=task.status,
        assignee_id=task.assignee_id,
    )
