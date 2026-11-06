"""Strict Pydantic contracts for all client-originated WebSocket events."""

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class EventModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event: str


class CreateEvent(EventModel):
    event: Literal["create"]
    name: str = Field(min_length=1, max_length=18)
    mode: Literal["classic", "speed_challenge", "team_battle", "elimination", "solo_practice"] = "classic"


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


class RematchEvent(EventModel):
    event: Literal["rematch"]


class LobbyEvent(EventModel):
    event: Literal["lobby"]


ClientEvent: TypeAlias = Annotated[
    CreateEvent | JoinEvent | ReconnectEvent | StartEvent | AnswerEvent | RematchEvent | LobbyEvent,
    Field(discriminator="event"),
]
EVENT_ADAPTER = TypeAdapter(ClientEvent)
