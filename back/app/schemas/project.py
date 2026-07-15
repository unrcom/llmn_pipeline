from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class PassSpec(BaseModel):
    name: str
    strategy: Literal["vector", "fulltext"]
    top_k: int
    use_metadata_filter: bool
    enabled: bool


class RetrievalPlan(BaseModel):
    passes: list[PassSpec]


DEFAULT_RETRIEVAL_PLAN = RetrievalPlan(
    passes=[
        PassSpec(name="meta+vec", strategy="vector", top_k=10, use_metadata_filter=True, enabled=True),
        PassSpec(name="vec_only", strategy="vector", top_k=3, use_metadata_filter=False, enabled=True),
        PassSpec(name="fulltext", strategy="fulltext", top_k=5, use_metadata_filter=False, enabled=True),
    ]
)


class ProjectCreateRequest(BaseModel):
    name: str
    description: str | None = None
    query_transform_mode: Literal["passthrough", "llm_rewrite"] = "passthrough"
    retrieval_plan: RetrievalPlan | None = None


class ProjectResponse(BaseModel):
    project_id: UUID
    name: str
    description: str | None
    query_transform_mode: str
    retrieval_plan: RetrievalPlan
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]
