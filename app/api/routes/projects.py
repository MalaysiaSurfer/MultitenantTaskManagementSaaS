from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import OrgAdmin, OrgMember, OrgOwner, Pagination, ResourceID
from app.core.exceptions import ProjectAlreadyExists
from app.db.session import get_db
from app.schemas.project import CreateProjectRequest, ProjectOut, ProjectsPage
from app.services.project import (
    create_organization_project,
    delete_organization_project,
    get_organization_project,
    get_organization_projects,
    update_organization_project,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    membership: OrgAdmin,
    data: CreateProjectRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectOut:
    try:
        project = await create_organization_project(
            db=db,
            organization_id=membership.organization_id,
            data=data,
        )
    except ProjectAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Project already exists",
        ) from None
    return ProjectOut(
        id=project.id,
        organization_id=project.organization_id,
        name=project.name,
    )


@router.get("")
async def get_projects(
    membership: OrgMember,
    db: Annotated[AsyncSession, Depends(get_db)],
    pagination: Pagination,
) -> ProjectsPage:
    page = await get_organization_projects(
        db=db,
        organization_id=membership.organization_id,
        limit=pagination.limit,
        cursor=pagination.cursor,
    )

    return ProjectsPage(
        items=page.items,
        next_cursor=page.next_cursor,
    )


@router.get("/{project_id}")
async def get_project(
    project_id: ResourceID,
    membership: OrgMember,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectOut:
    project = await get_organization_project(
        db=db,
        organization_id=membership.organization_id,
        project_id=project_id,
    )

    return ProjectOut(
        id=project.id,
        organization_id=project.organization_id,
        name=project.name,
    )


@router.patch("/{project_id}")
async def update_project(
    project_id: ResourceID,
    membership: OrgAdmin,
    data: CreateProjectRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectOut:
    try:
        project = await update_organization_project(
            db=db,
            organization_id=membership.organization_id,
            data=data,
            project_id=project_id,
        )
    except ProjectAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Project already exists",
        ) from None

    return ProjectOut(
        id=project.id,
        organization_id=project.organization_id,
        name=project.name,
    )


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_project(
    project_id: ResourceID,
    membership: OrgOwner,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await delete_organization_project(
        db=db,
        organization_id=membership.organization_id,
        project_id=project_id,
    )
