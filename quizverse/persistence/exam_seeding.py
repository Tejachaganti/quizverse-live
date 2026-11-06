"""Explicit additive seeding for curated exam collection taxonomy mappings."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from sqlalchemy import func, select

from quizverse.exam_collections import EXAM_COLLECTIONS
from quizverse.persistence.db import Database
from quizverse.persistence.models import Category, ExamCollection, ExamCollectionCategory


@dataclass(frozen=True, slots=True)
class ExamSeedReport:
    collections_added: int
    mappings_added: int


def seed_exam_collections(database: Database) -> ExamSeedReport:
    collections_added = 0
    mappings_added = 0
    with database.sessions.begin() as session:
        category_ids = {
            category.slug: category.id
            for category in session.scalars(select(Category).where(Category.is_active.is_(True)))
        }
        expected_slugs = {slug for collection in EXAM_COLLECTIONS for slug in collection.category_slugs}
        missing = expected_slugs - category_ids.keys()
        if missing:
            raise ValueError(
                "Run the taxonomy seed first; active taxonomy categories are missing: "
                + ", ".join(sorted(missing))
            )

        for definition in EXAM_COLLECTIONS:
            collection = session.scalar(select(ExamCollection).where(ExamCollection.slug == definition.slug))
            if collection is None:
                collection = ExamCollection(
                    id=definition.id,
                    slug=definition.slug,
                    name_key=definition.name_key,
                    display_name=definition.display_name,
                    description=definition.description,
                    is_active=True,
                    # No official syllabus version, region, or source is asserted.
                    syllabus_version=None,
                    region=None,
                    source_name=None,
                    source_url=None,
                )
                session.add(collection)
                session.flush()
                collections_added += 1
            elif collection.id != definition.id:
                raise ValueError(f"Exam collection slug {definition.slug!r} has a conflicting stable ID.")

            existing_ids = set(session.scalars(
                select(ExamCollectionCategory.category_id).where(
                    ExamCollectionCategory.collection_id == collection.id
                )
            ))
            next_order = session.scalar(
                select(func.max(ExamCollectionCategory.sort_order)).where(
                    ExamCollectionCategory.collection_id == collection.id
                )
            )
            next_order = 0 if next_order is None else next_order + 1
            for slug in definition.category_slugs:
                category_id = category_ids[slug]
                if category_id in existing_ids:
                    continue
                session.add(ExamCollectionCategory(
                    collection_id=collection.id,
                    category_id=category_id,
                    sort_order=next_order,
                ))
                existing_ids.add(category_id)
                next_order += 1
                mappings_added += 1
        session.flush()
    return ExamSeedReport(collections_added, mappings_added)


def main() -> None:
    load_dotenv()
    database = Database(os.environ.get("DATABASE_URL"))
    try:
        report = seed_exam_collections(database)
    finally:
        database.close()
    print(
        f"Exam collection seed complete: {report.collections_added} collections and "
        f"{report.mappings_added} taxonomy mappings added."
    )


if __name__ == "__main__":
    main()
