from typing import NamedTuple

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ProjectAlreadyExists
from app.db.base import Project
from app.schemas.project import CreateProjectRequest


class OrganizationProject(NamedTuple):
    id: int
    organization_id: int
    name: str


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
) -> list[OrganizationProject]:
    result = await db.execute(
        select(
            Project.id,
            Project.organization_id,
            Project.name,
        )
        .where(Project.organization_id == organization_id)
        .order_by(Project.id)
    )
    return [
        OrganizationProject(
            id=row.id,
            organization_id=row.organization_id,
            name=row.name,
        )
        for row in result
    ]


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
