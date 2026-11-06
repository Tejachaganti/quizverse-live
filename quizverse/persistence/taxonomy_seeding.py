"""Explicit, additive seeding for the question taxonomy and safe legacy mapping."""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass

from dotenv import load_dotenv
from sqlalchemy import select

from quizverse.persistence.db import Database
from quizverse.persistence.models import Category, Quiz, QuizQuestion, Tag
from quizverse.taxonomy import CATEGORIES, TAGS, legacy_category_slug, validate_category_definitions


@dataclass(frozen=True, slots=True)
class TaxonomySeedReport:
    categories_added: int
    tags_added: int
    mapped_values: dict[str, int]
    unmapped_values: dict[str, int]


def seed_taxonomy(database: Database) -> TaxonomySeedReport:
    """Add missing definitions without overwriting metadata or deleting history."""
    validate_category_definitions()
    categories_added = 0
    tags_added = 0
    with database.sessions.begin() as session:
        category_ids: dict[str, str] = {}
        for definition in CATEGORIES:
            existing = session.scalar(select(Category).where(Category.slug == definition.slug))
            if existing is not None:
                if existing.id != definition.id:
                    raise ValueError(f"Category slug {definition.slug!r} is already attached to another stable ID.")
                category_ids[definition.slug] = existing.id
                continue
            parent_id = category_ids.get(definition.parent_slug) if definition.parent_slug else None
            if definition.parent_slug and parent_id is None:
                raise ValueError(f"Category {definition.slug!r} was defined before its parent.")
            session.add(
                Category(
                    id=definition.id,
                    slug=definition.slug,
                    name_key=definition.name_key,
                    display_name=definition.display_name,
                    parent_id=parent_id,
                    is_active=True,
                    sort_order=definition.sort_order,
                )
            )
            session.flush()
            category_ids[definition.slug] = definition.id
            categories_added += 1

        for definition in TAGS:
            existing = session.scalar(select(Tag).where(Tag.slug == definition.slug))
            if existing is not None:
                if existing.id != definition.id:
                    raise ValueError(f"Tag slug {definition.slug!r} is already attached to another stable ID.")
                continue
            session.add(
                Tag(
                    id=definition.id,
                    slug=definition.slug,
                    name_key=definition.name_key,
                    display_name=definition.display_name,
                    is_active=True,
                )
            )
            tags_added += 1
        session.flush()

        mapped, unmapped = _apply_legacy_mappings(session, category_ids)

        return TaxonomySeedReport(
            categories_added=categories_added,
            tags_added=tags_added,
            mapped_values=dict(sorted(mapped.items())),
            unmapped_values=dict(sorted(unmapped.items())),
        )


def _apply_legacy_mappings(session, category_ids: dict[str, str]) -> tuple[Counter[str], Counter[str]]:
    mapped: Counter[str] = Counter()
    unmapped: Counter[str] = Counter()
    for model in (Quiz, QuizQuestion):
        for row in session.scalars(select(model).where(model.category.is_not(None))):
            target_slug = legacy_category_slug(row.category, row.provenance)
            if row.category_id is not None:
                target = next((slug for slug, stable_id in category_ids.items() if stable_id == row.category_id), None)
                (mapped if target else unmapped)[row.category] += 1
            elif target_slug is not None and target_slug in category_ids:
                row.category_id = category_ids[target_slug]
                mapped[row.category] += 1
            else:
                unmapped[row.category] += 1
    return mapped, unmapped


def main() -> None:
    load_dotenv()
    database = Database(os.environ.get("DATABASE_URL"))
    try:
        report = seed_taxonomy(database)
    finally:
        database.close()
    print(f"Taxonomy seed complete: {report.categories_added} categories and {report.tags_added} tags added.")
    print("Legacy category values mapped:")
    for value, count in report.mapped_values.items():
        print(f"  {value}: {count}")
    print("Legacy category values preserved without mapping:")
    for value, count in report.unmapped_values.items():
        print(f"  {value}: {count}")


if __name__ == "__main__":
    main()
