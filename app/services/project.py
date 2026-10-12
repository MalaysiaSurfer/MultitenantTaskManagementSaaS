from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ProjectAlreadyExists
from app.db.base import Project
from app.schemas.pagination import Page
from app.schemas.project import CreateProjectRequest, OrganizationProject


async def create_organization_project(
    db: AsyncSession,
    organization_id: int,
    data: CreateProjectRequest,
) -> OrganizationProject:
    project = Project(organization_id=organization_id, name=data.name)
    try:
        db.add(project)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ProjectAlreadyExists from None

    return OrganizationProject(
        id=project.id,
        organization_id=project.organization_id,
        name=project.name,
    )


async def get_organization_projects(
    db: AsyncSession,
    organization_id: int,
    limit: int = 20,
    cursor: int | None = None,
) -> Page:
    stmt = (
        select(
            Project.id,
            Project.organization_id,
            Project.name,
        )
        .where(Project.organization_id == organization_id)
        .order_by(Project.id)
        .limit(limit + 1)
    )
    if cursor is not None:
        stmt = stmt.where(Project.id > cursor)
    result = await db.execute(stmt)
    rows = result.all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        OrganizationProject(
            id=row.id,
            organization_id=row.organization_id,
            name=row.name,
        )
        for row in rows
    ]

    return Page(items=items, next_cursor=result[-1].id if has_more else None)


async def get_organization_project(
    db: AsyncSession,
    organization_id: int,
    project_id: int,
) -> OrganizationProject:
    result = await db.execute(
        select(
            Project.id,
            Project.organization_id,
            Project.name,
        ).where(Project.id == project_id, Project.organization_id == organization_id)
    )
    project = result.one_or_none()
    if project is None:
        raise NotFoundException()

    return OrganizationProject(
        id=project.id,
        organization_id=project.organization_id,
        name=project.name,
    )


async def delete_organization_project(
    db: AsyncSession,
    organization_id: int,
    project_id: int,
) -> None:
    result = await db.execute(
        delete(Project).where(Project.id == project_id, Project.organization_id == organization_id)
    )

    if result.rowcount == 0:
        raise NotFoundException()

    await db.commit()
