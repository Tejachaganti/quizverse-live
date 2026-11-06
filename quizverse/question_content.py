"""Validated future-facing question payloads and content suitability helpers."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

QuestionFormat = Literal["multiple_choice", "open_ended", "puzzle", "sequence"]
LearningLevel = Literal[
    "little_explorers", "young_learners", "junior_champions", "teen_challenge", "college_arena", "expert_arena"
]
ReviewStatus = Literal["unreviewed", "reviewed", "needs_review", "expired"]
MAX_FORMAT_PAYLOAD_BYTES = 20_000
_LANGUAGE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,49}$")


class StrictContentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class OpenEndedPayload(StrictContentModel):
    canonical_answer: str = Field(min_length=1, max_length=500)
    accepted_aliases: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("accepted_aliases")
    @classmethod
    def aliases_are_distinct(cls, values: list[str]) -> list[str]:
        normalized = [normalize_open_answer(value) for value in values]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("Accepted aliases must be non-empty and distinct.")
        return values

    @model_validator(mode="after")
    def canonical_not_repeated(self) -> "OpenEndedPayload":
        answers = [self.canonical_answer, *self.accepted_aliases]
        if len({normalize_open_answer(answer) for answer in answers}) != len(answers):
            raise ValueError("The canonical answer and aliases must be distinct.")
        return self


class PuzzlePayload(StrictContentModel):
    puzzle_type: str = Field(min_length=1, max_length=50)
    solution: str = Field(min_length=1, max_length=4_000)
    room_metadata: dict[str, str] = Field(default_factory=dict, max_length=20)

    @field_validator("puzzle_type")
    @classmethod
    def puzzle_type_is_slug(cls, value: str) -> str:
        if not _SLUG_PATTERN.fullmatch(value):
            raise ValueError("Puzzle type must be a lowercase slug.")
        return value

    @field_validator("room_metadata")
    @classmethod
    def room_metadata_is_bounded(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key or len(key) > 50 or len(item) > 500 for key, item in value.items()):
            raise ValueError("Puzzle room metadata keys and values exceed their limits.")
        return value


class SequencePayload(StrictContentModel):
    items: list[str] = Field(min_length=2, max_length=20)
    correct_order: list[int] = Field(min_length=2, max_length=20)

    @field_validator("items")
    @classmethod
    def items_are_valid(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 300 for value in values):
            raise ValueError("Sequence items must be non-empty and at most 300 characters.")
        return values

    @model_validator(mode="after")
    def order_is_a_permutation(self) -> "SequencePayload":
        if len(self.correct_order) != len(self.items) or set(self.correct_order) != set(range(len(self.items))):
            raise ValueError("Correct order must contain every item index exactly once.")
        return self


def validate_format_payload(question_format: str, payload: object) -> dict[str, object] | None:
    if question_format == "multiple_choice":
        if payload is not None:
            raise ValueError("Multiple-choice questions use the existing options and answer-index fields.")
        return None
    model: type[BaseModel]
    if question_format == "open_ended":
        model = OpenEndedPayload
    elif question_format == "puzzle":
        model = PuzzlePayload
    elif question_format == "sequence":
        model = SequencePayload
    else:
        raise ValueError("Question format is not supported.")
    if not isinstance(payload, dict):
        raise ValueError("This question format requires a structured format_payload object.")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("Question format payload must contain JSON-compatible values.") from error
    if len(encoded) > MAX_FORMAT_PAYLOAD_BYTES:
        raise ValueError("Question format payload exceeds 20,000 bytes.")
    return model.model_validate(payload).model_dump(mode="json")


class QuestionMetadata(StrictContentModel):
    language: str = Field(default="en", min_length=2, max_length=35)
    learning_levels: list[LearningLevel] = Field(default_factory=list, max_length=6)
    source_name: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=1000)
    source_published_at: date | None = None
    verified_at: date | None = None
    review_due_at: date | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)
    category_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("language")
    @classmethod
    def language_tag_is_valid(cls, value: str) -> str:
        if not _LANGUAGE_PATTERN.fullmatch(value):
            raise ValueError("Language must be a valid BCP 47 style language tag.")
        return value.lower()

    @field_validator("learning_levels")
    @classmethod
    def levels_are_distinct(cls, values: list[LearningLevel]) -> list[LearningLevel]:
        if len(values) != len(set(values)):
            raise ValueError("Learning-level suitability labels must be unique.")
        return values

    @field_validator("source_url")
    @classmethod
    def source_url_is_http(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"https?://[^\s]{1,992}", value, flags=re.IGNORECASE):
            raise ValueError("Source URL must use HTTP or HTTPS.")
        return value

    @field_validator("tags")
    @classmethod
    def tags_are_unique_slugs(cls, values: list[str]) -> list[str]:
        if any(not _SLUG_PATTERN.fullmatch(value) for value in values) or len(values) != len(set(values)):
            raise ValueError("Tags must be unique lowercase slugs.")
        return values

    @model_validator(mode="after")
    def review_dates_are_ordered(self) -> "QuestionMetadata":
        if self.review_due_at is not None and self.verified_at is not None and self.review_due_at < self.verified_at:
            raise ValueError("Review due date cannot precede the verification date.")
        return self


def validate_question_metadata(**values: object) -> dict[str, object]:
    return QuestionMetadata.model_validate(values).model_dump(mode="python")


def normalize_open_answer(value: str) -> str:
    """Normalize Unicode, case, and whitespace without discarding punctuation."""
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def open_answer_matches(payload: dict[str, object], candidate: str) -> bool:
    expected = normalize_open_answer(str(payload.get("canonical_answer", "")))
    aliases = payload.get("accepted_aliases", [])
    accepted = {expected, *(normalize_open_answer(str(item)) for item in aliases if isinstance(item, str))}
    return bool(expected) and normalize_open_answer(candidate) in accepted


def is_suitable_for_level(review_status: str, learning_levels: list[str], level: str) -> bool:
    """Unknown content is never suitable for the youngest preset by default."""
    return level in learning_levels and (level != "little_explorers" or review_status == "reviewed")
