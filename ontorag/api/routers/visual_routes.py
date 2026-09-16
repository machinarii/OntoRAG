"""Authenticated figure search and reserved visual-index maintenance."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator

from ontorag.api.utils_api import get_combined_auth_dependency
from ontorag.base import QueryParam
from ontorag.visual.protocol import validate_references


class VisualSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=4096)
    references: list[dict] | None = Field(
        default=None, min_length=1, max_length=4, repr=False
    )
    top_k: int = Field(default=40, ge=1, le=100)
    rerank: bool = True
    document_version: str | None = Field(default=None, min_length=1, max_length=200)
    as_of: str | None = None
    exclude_superseded: bool = False

    @field_validator("references")
    @classmethod
    def valid_references(cls, value):
        if value is not None:
            validate_references(value)
        return value

    @model_validator(mode="after")
    def valid_request(self):
        self.to_param()  # SDK and HTTP use identical date/mode validation.
        if not self.query.strip() and not self.references:
            raise ValueError("Provide a text query or reference images")
        return self

    def to_param(self):
        return QueryParam(
            mode="naive",
            enable_visual=True,
            visual_references=self.references,
            visual_top_k=self.top_k,
            visual_rerank=self.rerank,
            document_version=self.document_version,
            as_of=self.as_of,
            exclude_superseded=self.exclude_superseded,
        )


def create_visual_routes(rag, api_key=None):
    router = APIRouter(
        prefix="/visual",
        tags=["visual"],
        dependencies=[Depends(get_combined_auth_dependency(api_key))],
    )

    @router.post("/search")
    async def search(body: VisualSearchRequest):
        if getattr(rag, "_visual_runtime", None) is None:
            raise HTTPException(503, "Visual search is not enabled")
        try:
            chunks = await rag.avisual_search(body.query, body.to_param())
            return {"chunks": chunks}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.post("/rebuild")
    async def rebuild():
        if getattr(rag, "_visual_runtime", None) is None:
            raise HTTPException(503, "Visual search is not enabled")
        try:
            return await rag.arebuild_visual_index()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
