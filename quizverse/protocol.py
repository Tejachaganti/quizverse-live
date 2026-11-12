"""Strict Pydantic contracts for all client-originated WebSocket events."""

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from quizverse.taxonomy import CATEGORIES


class EventModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event: str


class CreateEvent(EventModel):
    event: Literal["create"]
    name: str = Field(min_length=1, max_length=18)
    mode: Literal["classic", "speed_challenge", "team_battle", "elimination", "solo_practice", "quiz_royale", "power_play"] = "classic"
    question_count: int | None = Field(default=None, ge=1, le=20)
    difficulty: Literal["easy", "medium", "hard", "mixed"] = "medium"
    language: str = Field(default="en", min_length=2, max_length=35, pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    learning_preset: Literal["little_explorers", "young_learners", "junior_champions", "teen_challenge", "college_arena", "expert_arena"] = "teen_challenge"
    categories: list[str] = Field(default_factory=list, max_length=49)
    topic_preferences: dict[str, Literal["increase", "normal", "decrease", "exclude"]] = Field(default_factory=dict, max_length=49)
    content_scope: Literal["built_in", "public_library", "selected_quizzes"] = "built_in"
    quiz_ids: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(default_factory=list, max_length=20)
    library_capability: str | None = Field(default=None, min_length=40, max_length=128)
    exam_collection_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def valid_selection(self):
        known = {item.id for item in CATEGORIES}
        if any(category not in known for category in self.categories) or any(category not in known for category in self.topic_preferences):
            raise ValueError("Category filters must use active taxonomy category IDs.")
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("Category filters cannot contain duplicates.")
        if self.content_scope == "built_in" and (self.quiz_ids or self.library_capability):
            raise ValueError("Built-in content does not accept quiz IDs or library credentials.")
        if self.content_scope == "public_library" and self.library_capability:
            raise ValueError("Public library selection does not use private library credentials.")
        if self.content_scope == "selected_quizzes" and not self.quiz_ids:
            raise ValueError("Select at least one quiz for selected quiz content.")
        if len(set(self.quiz_ids)) != len(self.quiz_ids):
            raise ValueError("Selected quiz IDs cannot contain duplicates.")
        return self


class JoinEvent(EventModel):
    event: Literal["join"]
    name: str = Field(min_length=1, max_length=18)
    code: str = Field(min_length=6, max_length=6)


class ReconnectEvent(EventModel):
    event: Literal["reconnect"]
    token: str = Field(min_length=40, max_length=128)


class StartEvent(EventModel):
    event: Literal["start"]


class AnswerEvent(EventModel):
    event: Literal["answer"]
    choice: int = Field(ge=0, le=3)


class AbilityEvent(EventModel):
    event: Literal["ability"]
    ability: Literal["fifty_fifty", "double_points", "shield"]
    request_id: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class RematchEvent(EventModel):
    event: Literal["rematch"]


class LobbyEvent(EventModel):
    event: Literal["lobby"]


ClientEvent: TypeAlias = Annotated[
    CreateEvent | JoinEvent | ReconnectEvent | StartEvent | AnswerEvent | AbilityEvent | RematchEvent | LobbyEvent,
    Field(discriminator="event"),
]
EVENT_ADAPTER = TypeAdapter(ClientEvent)
