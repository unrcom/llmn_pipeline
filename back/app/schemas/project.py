from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, field_validator


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


def _require_nonblank_name(value: str) -> str:
    if value == "":
        raise ValueError("name must not be empty")
    return value


class ProjectCreateRequest(BaseModel):
    name: str
    description: str | None = None
    query_transform_mode: Literal["passthrough", "llm_rewrite"] = "passthrough"
    retrieval_plan: RetrievalPlan | None = None

    _validate_name = field_validator("name")(_require_nonblank_name)


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


class EmbeddingSettingResponse(BaseModel):
    model_key: str
    threshold: float
    is_default: bool


class ProjectDetailResponse(ProjectResponse):
    embedding_settings: list[EmbeddingSettingResponse] = []


class ProjectPatchRequest(BaseModel):
    # name / query_transform_mode / retrieval_plan は NOT NULL カラムのため、
    # フィールド省略(更新しない)は許すが明示的な null は拒否する。
    # description は NULL 許容カラムのため、null 明示指定(クリア)を許す。
    name: str | None = None
    description: str | None = None
    query_transform_mode: Literal["passthrough", "llm_rewrite"] | None = None
    retrieval_plan: RetrievalPlan | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("name must not be null")
        return _require_nonblank_name(value)

    @field_validator("query_transform_mode", "retrieval_plan")
    @classmethod
    def _reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("must not be null")
        return value
