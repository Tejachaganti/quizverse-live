"""Read-only category and tag discovery endpoints."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, FastAPI, Path, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from quizverse.persistence.db import Database
from quizverse.persistence.models import Category, Tag
from quizverse.quiz_api import ApiProblem
from quizverse.taxonomy import CATEGORIES

_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_MAX_ACTIVE_CATEGORIES = 500


class CategoryNodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    slug: str
    name_key: str
    display_name: str
    parent_id: str | None
    sort_order: int
    children: list["CategoryNodeResponse"]


class TagResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    slug: str
    name_key: str
    display_name: str


def _category_node(category: Category, children: list[CategoryNodeResponse] | None = None) -> CategoryNodeResponse:
    return CategoryNodeResponse(
        id=category.id,
        slug=category.slug,
        name_key=category.name_key,
        display_name=category.display_name,
        parent_id=category.parent_id,
        sort_order=category.sort_order,
        children=children or [],
    )


def _load_active_categories(database: Database) -> list[Category]:
    with database.session() as session:
        categories = list(
            session.scalars(
                select(Category)
                .where(Category.is_active.is_(True))
                .order_by(Category.sort_order, Category.slug)
                .limit(_MAX_ACTIVE_CATEGORIES + 1)
            )
        )
    if len(categories) > _MAX_ACTIVE_CATEGORIES:
        raise ApiProblem(503, "taxonomy_too_large", "The active taxonomy exceeds the supported response size.")
    return categories


def install_taxonomy_api(app: FastAPI, database: Database) -> None:
    router = APIRouter(prefix="/api")

    @router.get("/categories", response_model=list[CategoryNodeResponse])
    def get_categories() -> list[CategoryNodeResponse]:
        categories = _load_active_categories(database)
        if not categories:
            with database.session() as session:
                taxonomy_rows = session.scalar(select(func.count()).select_from(Category)) or 0
            if taxonomy_rows:
                return []
            by_parent_definition: dict[str | None, list] = {}
            for item in CATEGORIES:
                by_parent_definition.setdefault(
                    f"category:{item.parent_slug}" if item.parent_slug else None, []
                ).append(item)

            def built_in_tree(parent_id: str | None) -> list[CategoryNodeResponse]:
                return [
                    CategoryNodeResponse(
                        id=item.id,
                        slug=item.slug,
                        name_key=item.name_key,
                        display_name=item.display_name,
                        parent_id=f"category:{item.parent_slug}" if item.parent_slug else None,
                        sort_order=item.sort_order,
                        children=built_in_tree(item.id),
                    )
                    for item in sorted(by_parent_definition.get(parent_id, []), key=lambda node: (node.sort_order, node.slug))
                ]

            return built_in_tree(None)
        by_parent: dict[str | None, list[Category]] = {}
        active_ids = {item.id for item in categories}
        for category in categories:
            if category.parent_id is None or category.parent_id in active_ids:
                by_parent.setdefault(category.parent_id, []).append(category)

        def build(parent_id: str | None) -> list[CategoryNodeResponse]:
            return [
                _category_node(category, build(category.id))
                for category in by_parent.get(parent_id, [])
            ]

        return build(None)

    @router.get("/categories/{slug}/topics")
    def get_category_topics(
        slug: Annotated[str, Path(min_length=1, max_length=80, pattern=_SLUG_PATTERN.pattern)],
    ) -> dict[str, object]:
        with database.session() as session:
            category = session.scalar(
                select(Category).where(Category.slug == slug, Category.is_active.is_(True))
            )
            if category is None:
                raise ApiProblem(404, "category_not_found", "The active category was not found.")
        categories = _load_active_categories(database)

        by_parent: dict[str | None, list[Category]] = {}
        active_ids = {item.id for item in categories}
        for item in categories:
            if item.parent_id is None or item.parent_id in active_ids:
                by_parent.setdefault(item.parent_id, []).append(item)

        descendants: list[CategoryNodeResponse] = []

        def visit(parent_id: str) -> None:
            for child in by_parent.get(parent_id, []):
                descendants.append(_category_node(child))
                visit(child.id)

        visit(category.id)
        return {
            "category": _category_node(category),
            "topics": descendants,
            "topics_contract": "All active descendants, depth-first; each item includes its direct parent_id.",
        }

    @router.get("/tags")
    def get_tags(
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
        offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    ) -> dict[str, object]:
        with database.session() as session:
            total = session.scalar(select(func.count()).select_from(Tag).where(Tag.is_active.is_(True))) or 0
            tags = session.scalars(
                select(Tag)
                .where(Tag.is_active.is_(True))
                .order_by(Tag.slug)
                .offset(offset)
                .limit(limit)
            )
            items = [
                TagResponse(id=tag.id, slug=tag.slug, name_key=tag.name_key, display_name=tag.display_name)
                for tag in tags
            ]
        return {"items": items, "limit": limit, "offset": offset, "total": total}

    app.include_router(router)
