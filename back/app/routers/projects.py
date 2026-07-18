from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key
from app.db import get_db_session
from app.exceptions import AppError
from app.models.project import Project
from app.schemas.project import (
    DEFAULT_RETRIEVAL_PLAN,
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectListResponse,
    ProjectPatchRequest,
    ProjectResponse,
)

router = APIRouter(prefix="/projects", tags=["projects"], dependencies=[Depends(verify_api_key)])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def _to_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        project_id=project.project_id,
        name=project.name,
        description=project.description,
        query_transform_mode=project.query_transform_mode,
        retrieval_plan=project.retrieval_plan,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _to_detail_response(project: Project) -> ProjectDetailResponse:
    return ProjectDetailResponse(
        **_to_response(project).model_dump(),
        embedding_settings=[],  # embedding-settings は依頼 1-3 で実装
    )


async def _get_project_or_404(db: AsyncSession, project_id: UUID) -> Project:
    project = await db.get(Project, project_id)
    if project is None:
        raise AppError(code="project_not_found", message="Project not found", status_code=404)
    return project


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreateRequest, db: DbSession) -> ProjectResponse:
    retrieval_plan = body.retrieval_plan or DEFAULT_RETRIEVAL_PLAN
    project = Project(
        name=body.name,
        description=body.description,
        query_transform_mode=body.query_transform_mode,
        retrieval_plan=retrieval_plan.model_dump(),
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return _to_response(project)


@router.get("", response_model=ProjectListResponse)
async def list_projects(db: DbSession) -> ProjectListResponse:
    result = await db.execute(select(Project).order_by(Project.created_at))
    projects = result.scalars().all()
    return ProjectListResponse(projects=[_to_response(project) for project in projects])


@router.get("/{project_id}", response_model=ProjectDetailResponse)
async def get_project(project_id: UUID, db: DbSession) -> ProjectDetailResponse:
    project = await _get_project_or_404(db, project_id)
    return _to_detail_response(project)


@router.patch("/{project_id}", response_model=ProjectDetailResponse)
async def patch_project(
    project_id: UUID, body: ProjectPatchRequest, db: DbSession
) -> ProjectDetailResponse:
    project = await _get_project_or_404(db, project_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    project.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(project)
    return _to_detail_response(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: UUID, db: DbSession) -> Response:
    project = await _get_project_or_404(db, project_id)
    await db.delete(project)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
