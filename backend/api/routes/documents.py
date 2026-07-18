"""Document ingestion API routes.

POST /pipelines/{pipeline_id}/documents ingests raw text documents into the
pipeline's configured vector store (resolved from the pipeline record's
retrieval config, falling back to the default :class:`RetrievalConfig`).

The route is intentionally dependency-light: it does NOT require a live
database — when PostgreSQL/pgvector is unavailable the ingestion falls back to
the in-memory store.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.dependencies import get_pipeline_repository
from backend.core.config import RetrievalConfig
from backend.db.repositories import PipelineRepository
from backend.retrieval.ingest import ingest_documents
from backend.retrieval.store import get_vector_store

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/pipelines/{pipeline_id}/documents",
    tags=["documents"],
)


class IngestRequest(BaseModel):
    """Request body for document ingestion."""

    documents: list[str] = Field(..., min_length=1)
    index_name: str = "default"
    metadata: list[dict[str, Any]] | None = None


class IngestResponse(BaseModel):
    """Response for a successful ingestion."""

    count: int
    index_name: str


def _retrieval_config_from_record(record: dict[str, Any]) -> RetrievalConfig:
    """Extract a :class:`RetrievalConfig` from a pipeline record's ``config``."""
    raw = record.get("config", {}) or {}
    retrieval_raw = raw.get("retrieval", {}) if isinstance(raw, dict) else {}
    if isinstance(retrieval_raw, dict) and retrieval_raw:
        return RetrievalConfig(**retrieval_raw)
    return RetrievalConfig()


@router.post("", response_model=IngestResponse, status_code=201)
async def ingest_pipeline_documents(
    pipeline_id: str,
    body: IngestRequest,
    repo: Annotated[PipelineRepository, Depends(get_pipeline_repository)],
) -> IngestResponse:
    """Ingest documents into the pipeline's vector store."""
    record = await repo.get(pipeline_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"Pipeline {pipeline_id} not found"
        )

    config = _retrieval_config_from_record(record)
    store = get_vector_store(config)

    count = await ingest_documents(
        store,
        body.documents,
        metadata=body.metadata,
        index_name=body.index_name,
    )
    return IngestResponse(count=count, index_name=body.index_name)
