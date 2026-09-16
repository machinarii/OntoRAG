"""Authenticated maintenance endpoints for the optional retrieval index."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ontorag.api.utils_api import get_combined_auth_dependency
from ontorag.retrieval.maintenance import rebuild_index, set_document_metadata


class DocumentRetrievalMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str | None = Field(default=None, min_length=1, max_length=200)
    effective_from: date | None = None
    effective_to: date | None = None
    superseded: bool = False


def create_retrieval_routes(rag, api_key=None):
    router = APIRouter(
        prefix="/retrieval",
        tags=["retrieval"],
        dependencies=[Depends(get_combined_auth_dependency(api_key))],
    )

    @router.post("/rebuild")
    async def rebuild():
        try:
            return await rebuild_index(rag)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.put("/documents/{doc_id}/metadata")
    async def metadata(doc_id: str, body: DocumentRetrievalMetadata):
        try:
            return await set_document_metadata(
                rag, doc_id, body.model_dump(mode="json", exclude_none=True)
            )
        except KeyError as exc:
            raise HTTPException(404, "Document not found") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
