from __future__ import annotations

from collections import Counter

from quizverse.intelligence.duplicate_detection import DuplicateCandidate, DuplicateDetector
from quizverse.persistence.repositories import QuestionRecord, validate_question_record
from quizverse.question_intelligence import SelectionFilters, select_questions
from quizverse.questions import QUESTIONS
from quizverse.taxonomy import CATEGORIES


def _root_slug(slug: str) -> str:
    definitions = {item.slug: item for item in CATEGORIES}
    while definitions[slug].parent_slug is not None:
        slug = definitions[slug].parent_slug
    return slug


def test_builtin_bank_has_reviewed_inventory_across_official_categories_and_difficulties():
    assert len(QUESTIONS) == 82
    category_ids = {item.id for item in CATEGORIES}
    roots = Counter(_root_slug(question.category_id.removeprefix("category:")) for question in QUESTIONS)
    difficulties = Counter(question.difficulty for question in QUESTIONS)

    assert set(roots) == {item.slug for item in CATEGORIES if item.parent_slug is None}
    assert all(count >= 5 for count in roots.values())
    assert set(difficulties) == {"easy", "medium", "hard"}
    assert all(question.category_id in category_ids for question in QUESTIONS)


def test_every_builtin_question_has_valid_options_answer_explanation_and_topic():
    ids: set[str] = set()
    for question in QUESTIONS:
        assert question.id not in ids
        ids.add(question.id)
        assert question.topic.strip()
        assert question.explanation and question.explanation.strip()
        assert 0 <= question.answer < 4
        assert len(set(option.casefold() for option in question.options)) == 4
        validate_question_record(QuestionRecord(
            prompt=question.prompt,
            options=question.options,
            correct_option_index=question.answer,
            explanation=question.explanation,
            category=question.category,
            category_id=question.category_id,
            difficulty=question.difficulty,
            provenance="built_in_reviewed",
        ))


def test_existing_duplicate_detector_finds_no_duplicate_pairs_in_builtin_bank():
    detector = DuplicateDetector()
    candidates = [DuplicateCandidate(question.id, question.prompt) for question in QUESTIONS]
    duplicates = [
        question.prompt
        for question in QUESTIONS
        if detector.check_duplicate(question.prompt, candidates, exclude_id=question.id).duplicate_status
        != "unique"
    ]
    assert duplicates == []


def test_category_filtered_selection_returns_only_questions_in_requested_root():
    parent_by_id = {
        item.id: f"category:{item.parent_slug}" if item.parent_slug else None
        for item in CATEGORIES
    }
    roots = [item for item in CATEGORIES if item.parent_slug is None]
    for root in roots:
        selected = select_questions(QUESTIONS, SelectionFilters(
            count=1,
            difficulty="mixed",
            categories=frozenset({root.id}),
            parent_by_id=parent_by_id,
        ))
        assert len(selected) == 1
        assert _root_slug(selected[0].category_id.removeprefix("category:")) == root.slug
