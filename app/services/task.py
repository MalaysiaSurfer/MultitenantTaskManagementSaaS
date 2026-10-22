import logging

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.db.base import Membership, Project, Task, TaskStatus
from app.schemas.pagination import Page
from app.schemas.task import CreateTaskRequest, OrganizationTask, UpdateTaskRequest

logger = logging.getLogger(__name__)


async def _ensure_org_member(db: AsyncSession, organization_id: int, user_id: int) -> None:
    result = await db.execute(
        select(Membership.id).where(
            Membership.user_id == user_id,
            Membership.organization_id == organization_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise NotFoundException()


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
        await _ensure_org_member(db=db, organization_id=organization_id, user_id=data.assignee_id)

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
    status: TaskStatus | None = None,
    assignee_id: int | None = None,
    project_id: int | None = None,
    unassigned: bool = False,
    limit: int = 20,
    cursor: int | None = None,
) -> Page[OrganizationTask]:
    stmt = select(
        Task.id,
        Task.organization_id,
        Task.project_id,
        Task.title,
        Task.description,
        Task.status,
        Task.assignee_id,
    ).where(Task.organization_id == organization_id)
    if cursor is not None:
        stmt = stmt.where(Task.id > cursor)
    if status is not None:
        stmt = stmt.where(Task.status == status)
    if assignee_id is not None:
        stmt = stmt.where(Task.assignee_id == assignee_id)
    if project_id is not None:
        stmt = stmt.where(Task.project_id == project_id)
    if unassigned:
        stmt = stmt.where(Task.assignee_id.is_(None))
    stmt = stmt.order_by(Task.id)
    stmt = stmt.limit(limit + 1)
    result = await db.execute(stmt)
    rows = result.all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        OrganizationTask(
            id=row.id,
            organization_id=row.organization_id,
            project_id=row.project_id,
            title=row.title,
            description=row.description,
            status=row.status,
            assignee_id=row.assignee_id,
        )
        for row in rows
    ]

    return Page(items=items, next_cursor=items[-1].id if has_more else None)


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


async def update_organization_task(
    db: AsyncSession,
    organization_id: int,
    data: UpdateTaskRequest,
    task_id: int,
) -> OrganizationTask:
    update_data = data.model_dump(exclude_unset=True)
    assignee_id = update_data.get("assignee_id")
    if assignee_id is not None:
        await _ensure_org_member(db=db, organization_id=organization_id, user_id=assignee_id)
    result = await db.execute(
        update(Task)
        .where(Task.id == task_id, Task.organization_id == organization_id)
        .values(update_data)
        .returning(
            Task.id,
            Task.organization_id,
            Task.project_id,
            Task.title,
            Task.description,
            Task.status,
            Task.assignee_id,
        )
    )

    task = result.one_or_none()
    if task is None:
        raise NotFoundException()

    await db.commit()

    return OrganizationTask(
        id=task.id,
        organization_id=task.organization_id,
        project_id=task.project_id,
        title=task.title,
        description=task.description,
        status=task.status,
        assignee_id=task.assignee_id,
    )


async def delete_organization_task(
    db: AsyncSession,
    organization_id: int,
    task_id: int,
) -> None:
    result = await db.execute(
        delete(Task).where(Task.id == task_id, Task.organization_id == organization_id)
    )

    if result.rowcount == 0:
        raise NotFoundException()

    await db.commit()
