"""Read-only discovery for curated exam collections."""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated

from fastapi import APIRouter, FastAPI, Path
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from quizverse.persistence.db import Database
from quizverse.persistence.models import ExamCollection, ExamCollectionCategory
from quizverse.quiz_api import ApiProblem

_COLLECTION_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


class ExamCategoryMappingResponse(BaseModel):
    category_id: str
    slug: str
    name_key: str
    display_name: str
    sort_order: int


class ExamCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    slug: str
    name_key: str
    display_name: str
    description: str | None
    syllabus_version: str | None
    region: str | None
    source_name: str | None
    source_url: str | None
    source_published_at: date | None
    reviewed_at: date | None
    review_due_at: date | None
    categories: tuple[ExamCategoryMappingResponse, ...]


def _response(collection: ExamCollection) -> ExamCollectionResponse:
    mappings = sorted(
        (
            mapping for mapping in collection.taxonomy_mappings
            if mapping.category.is_active
        ),
        key=lambda item: (item.sort_order, item.category.slug),
    )
    return ExamCollectionResponse(
        id=collection.id,
        slug=collection.slug,
        name_key=collection.name_key,
        display_name=collection.display_name,
        description=collection.description,
        syllabus_version=collection.syllabus_version,
        region=collection.region,
        source_name=collection.source_name,
        source_url=collection.source_url,
        source_published_at=collection.source_published_at,
        reviewed_at=collection.reviewed_at,
        review_due_at=collection.review_due_at,
        categories=tuple(
            ExamCategoryMappingResponse(
                category_id=item.category.id,
                slug=item.category.slug,
                name_key=item.category.name_key,
                display_name=item.category.display_name,
                sort_order=item.sort_order,
            )
            for item in mappings
        ),
    )


def install_exam_collection_api(app: FastAPI, database: Database) -> None:
    router = APIRouter(prefix="/api")

    @router.get("/exam-collections", response_model=list[ExamCollectionResponse])
    def list_exam_collections() -> list[ExamCollectionResponse]:
        with database.session() as session:
            collections = session.scalars(
                select(ExamCollection)
                .options(
                    selectinload(ExamCollection.taxonomy_mappings)
                    .selectinload(ExamCollectionCategory.category)
                )
                .where(ExamCollection.is_active.is_(True))
                .order_by(ExamCollection.display_name, ExamCollection.slug)
                .limit(100)
            )
            return [_response(item) for item in collections]

    @router.get("/exam-collections/{slug}", response_model=ExamCollectionResponse)
    def get_exam_collection(
        slug: Annotated[str, Path(min_length=1, max_length=80, pattern=_COLLECTION_SLUG.pattern)],
    ) -> ExamCollectionResponse:
        with database.session() as session:
            collection = session.scalar(
                select(ExamCollection)
                .options(
                    selectinload(ExamCollection.taxonomy_mappings)
                    .selectinload(ExamCollectionCategory.category)
                )
                .where(ExamCollection.slug == slug, ExamCollection.is_active.is_(True))
            )
            if collection is None:
                raise ApiProblem(404, "exam_collection_not_found", "The active exam collection was not found.")
            return _response(collection)

    app.include_router(router)
