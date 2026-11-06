"""Explicit, idempotent seeding for the existing reviewed built-in question bank."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from sqlalchemy import select

from quizverse.persistence.db import Database
from quizverse.persistence.models import BuiltinSeed, Quiz, QuizQuestion
from quizverse.persistence.repositories import QuestionRecord, QuizRecord
from quizverse.questions import QUESTIONS

BUILTIN_BANK_ID = "builtin-general-knowledge-v1"


class BuiltinSeedConflict(RuntimeError):
    """A seeded bank changed without a new immutable bank version identifier."""


def _stable_question_id(question: QuestionRecord) -> str:
    payload = json.dumps(
        [question.category, question.prompt, *question.options, question.correct_option_index],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "builtin-q-" + hashlib.sha256(payload).hexdigest()[:32]


def _bank_records() -> list[QuestionRecord]:
    records = []
    for question in QUESTIONS:
        record = QuestionRecord(
            prompt=question.prompt,
            options=question.options,
            correct_option_index=question.answer,
            category="-".join(question.category.lower().split()),
            provenance="built_in_reviewed",
        )
        records.append(replace(record, id=_stable_question_id(record)))
    return records


def seed_builtin_bank(database: Database) -> bool:
    """Seed the current built-in bank once; return whether rows were inserted.

    The stable bank ID is versioned. Changing existing bank content requires a new
    bank ID rather than silently rewriting a quiz that matches may reference.
    """
    questions = _bank_records()
    source_payload = json.dumps(
        [[item.id, item.category, item.prompt, *item.options, item.correct_option_index] for item in questions],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(source_payload).hexdigest()
    with database.sessions.begin() as session:
        prior_seed = session.get(BuiltinSeed, BUILTIN_BANK_ID)
        if prior_seed is not None:
            if prior_seed.source_digest != digest:
                raise BuiltinSeedConflict(
                    "Built-in question content changed; add a new versioned built-in bank ID before seeding."
                )
            prior_quiz = session.get(Quiz, BUILTIN_BANK_ID)
            actual_question_ids = {
                question_id
                for (question_id,) in session.execute(
                    select(QuizQuestion.id).where(QuizQuestion.quiz_id == BUILTIN_BANK_ID)
                )
            }
            expected_question_ids = {question.id for question in questions}
            if prior_quiz is None or actual_question_ids != expected_question_ids:
                raise BuiltinSeedConflict("Seeded built-in bank rows are incomplete or inconsistent.")
            return False
        existing = session.get(Quiz, BUILTIN_BANK_ID)
        if existing is not None:
            raise BuiltinSeedConflict("Built-in quiz ID already exists without a seed record.")

        quiz_record = QuizRecord(
            id=BUILTIN_BANK_ID,
            title="QuizVerse General Knowledge",
            description="The reviewed built-in question bank.",
            category="general-knowledge",
            difficulty="medium",
            visibility="public",
            status="published",
            default_time_limit_seconds=15,
            provenance="built_in_reviewed",
        )
        from quizverse.persistence.repositories import _insert_quiz

        _insert_quiz(session, None, quiz_record, questions)
        session.add(BuiltinSeed(bank_id=BUILTIN_BANK_ID, source_digest=digest))
        return True


def main() -> None:
    """Seed built-in content only after an operator has explicitly run migrations."""
    from dotenv import load_dotenv

    load_dotenv()
    database = Database()
    try:
        inserted = seed_builtin_bank(database)
        print("Built-in question bank seeded." if inserted else "Built-in question bank already current.")
    finally:
        database.close()
