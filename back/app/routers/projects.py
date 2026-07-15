from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key
from app.db import get_db_session
from app.models.project import Project
from app.schemas.project import (
    DEFAULT_RETRIEVAL_PLAN,
    ProjectCreateRequest,
    ProjectListResponse,
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
