"""Validated HTTP API for the persistent quiz library; no game routes live here."""

from __future__ import annotations

import asyncio
import json
import re
import math
import threading
import time
from collections import deque
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, Header, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.exc import SQLAlchemyError
from quizverse.ai_generation import (
    GenerationError,
    GenerationRequest,
    GenerationService,
    extract_upload,
    parse_generation_form,
)

from quizverse.persistence.authorization import (
    AuthorQuestionView,
    AuthorQuizView,
    PlayerQuestionView,
    PlayerQuizView,
    QuizAccessService,
)
from quizverse.persistence.capabilities import CapabilityScope
from quizverse.persistence.repositories import QuestionRecord, QuizRecord, QuizVersionConflict
from quizverse.question_content import LearningLevel, QuestionFormat, validate_format_payload, validate_question_metadata

_CATEGORY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,49}$")
_MAX_QUESTION_CONTENT_BYTES = 256 * 1024


def _bounded_question_content(questions: list[QuestionInput] | None) -> None:
    if questions is None:
        return
    encoded = json.dumps([question.model_dump(mode="json") for question in questions], separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_QUESTION_CONTENT_BYTES:
        raise ValueError("Question content must fit within 256 KB per quiz update.")


class ApiProblem(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}


class OwnerCreationRateLimiter:
    """Bounded process-local rate limiter for anonymous owner creation."""

    def __init__(
        self,
        *,
        limit: int = 5,
        window_seconds: float = 60.0,
        max_clients: int = 4096,
        clock=time.monotonic,
    ) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._max_clients = max_clients
        self._clock = clock
        self._attempts: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, client_key: str) -> tuple[bool, int]:
        now = self._clock()
        with self._lock:
            stale_clients = [
                key
                for key, attempts in self._attempts.items()
                if not attempts or now - attempts[-1] >= self._window_seconds
            ]
            for key in stale_clients:
                self._attempts.pop(key, None)

            attempts = self._attempts.get(client_key)
            if attempts is None:
                if len(self._attempts) >= self._max_clients:
                    oldest_key = min(self._attempts, key=lambda key: self._attempts[key][-1])
                    self._attempts.pop(oldest_key, None)
                attempts = deque()
                self._attempts[client_key] = attempts

            while attempts and now - attempts[0] >= self._window_seconds:
                attempts.popleft()
            if len(attempts) >= self._limit:
                retry_after = max(1, math.ceil(self._window_seconds - (now - attempts[0])))
                return False, retry_after

            attempts.append(now)
            return True, 0


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class QuestionInput(StrictRequest):
    prompt: str = Field(min_length=1, max_length=500)
    options: list[str] | None = Field(default=None, min_length=4, max_length=4)
    correct_option_index: int | None = Field(default=None, ge=0, le=3)
    explanation: str | None = Field(default=None, max_length=1000)
    category: str | None = Field(default=None, min_length=1, max_length=50)
    category_id: str | None = Field(default=None, min_length=1, max_length=128)
    tags: list[str] = Field(default_factory=list, max_length=20)
    difficulty: Literal["easy", "medium", "hard"] | None = None
    time_limit_seconds: int | None = Field(default=None, ge=5, le=120)
    format: QuestionFormat = "multiple_choice"
    format_payload: dict[str, Any] | None = None
    language: str = Field(default="en", min_length=2, max_length=35)
    learning_levels: list[LearningLevel] = Field(default_factory=list, max_length=6)
    source_name: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=1000)
    source_published_at: date | None = None
    verified_at: date | None = None
    review_due_at: date | None = None

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value: str) -> str:
        return value.lower()

    @model_validator(mode="after")
    def validate_content(self) -> "QuestionInput":
        try:
            validate_format_payload(self.format, self.format_payload)
            validate_question_metadata(
                language=self.language,
                learning_levels=self.learning_levels,
                source_name=self.source_name,
                source_url=self.source_url,
                source_published_at=self.source_published_at,
                verified_at=self.verified_at,
                review_due_at=self.review_due_at,
                tags=self.tags,
                category_id=self.category_id,
            )
        except (TypeError, ValueError) as error:
            raise ValueError(str(error)) from error
        return self

    def to_record(self) -> QuestionRecord:
        return QuestionRecord(
            prompt=self.prompt,
            options=tuple(self.options) if self.options is not None else None,
            correct_option_index=self.correct_option_index,
            explanation=self.explanation,
            category=self.category,
            category_id=self.category_id,
            tags=tuple(self.tags),
            difficulty=self.difficulty,
            time_limit_seconds=self.time_limit_seconds,
            question_format=self.format,
            format_payload=self.format_payload,
            language=self.language,
            learning_levels=tuple(self.learning_levels),
            source_name=self.source_name,
            source_url=self.source_url,
            source_published_at=self.source_published_at,
            verified_at=self.verified_at,
            review_due_at=self.review_due_at,
        )


class QuizCreateInput(StrictRequest):
    title: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    category: str = Field(min_length=1, max_length=50)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    visibility: Literal["private", "public"] = "private"
    default_time_limit_seconds: int = Field(default=15, ge=5, le=120)
    questions: list[QuestionInput] = Field(default_factory=list, max_length=200)
    generation_ticket: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_question_content_size(self) -> "QuizCreateInput":
        _bounded_question_content(self.questions)
        return self

    @model_validator(mode="after")
    def validate_category(self) -> "QuizCreateInput":
        if not _CATEGORY_PATTERN.fullmatch(self.category.strip().lower().replace(" ", "-")):
            raise ValueError("Category must use letters, numbers, hyphens, or underscores.")
        return self

    def to_record(self) -> QuizRecord:
        return QuizRecord(
            title=self.title,
            description=self.description,
            category=self.category,
            difficulty=self.difficulty,
            visibility=self.visibility,
            status="draft",
            default_time_limit_seconds=self.default_time_limit_seconds,
            provenance="user_authored",
        )


class QuizUpdateInput(StrictRequest):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, min_length=1, max_length=50)
    difficulty: Literal["easy", "medium", "hard"] | None = None
    visibility: Literal["private", "public"] | None = None
    default_time_limit_seconds: int | None = Field(default=None, ge=5, le=120)
    questions: list[QuestionInput] | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_update(self) -> "QuizUpdateInput":
        fields = self.model_fields_set - {"expected_version"}
        if not fields:
            raise ValueError("Provide at least one field to update.")
        for field_name in fields - {"description"}:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null.")
        if "questions" in fields and self.questions is None:
            raise ValueError("questions must be a list when provided.")
        if "questions" in fields:
            _bounded_question_content(self.questions)
        if self.category is not None and not _CATEGORY_PATTERN.fullmatch(
            self.category.strip().lower().replace(" ", "-")
        ):
            raise ValueError("Category must use letters, numbers, hyphens, or underscores.")
        return self

    def changes(self) -> dict[str, object]:
        names = {
            "title", "description", "category", "difficulty", "visibility", "default_time_limit_seconds"
        }
        return {name: getattr(self, name) for name in self.model_fields_set & names}

    def question_records(self) -> list[QuestionRecord] | None:
        if "questions" not in self.model_fields_set:
            return None
        assert self.questions is not None
        return [question.to_record() for question in self.questions]


class ShareInput(StrictRequest):
    expires_in_seconds: int | None = Field(default=None, ge=1, le=31_536_000)


class OwnerCapabilityResponse(BaseModel):
    owner_id: str
    capability: str


class ShareResponse(BaseModel):
    grant_id: str
    scope: CapabilityScope
    capability: str


class PlayerQuestionResponse(BaseModel):
    id: str
    position: int
    prompt: str
    options: tuple[str, str, str, str]
    category: str
    difficulty: str
    time_limit_seconds: int


class PlayerQuizResponse(BaseModel):
    id: str
    title: str
    description: str | None
    category: str
    difficulty: str
    default_time_limit_seconds: int
    questions: tuple[PlayerQuestionResponse, ...]


class AuthorQuestionResponse(BaseModel):
    id: str
    position: int
    prompt: str
    options: tuple[str, str, str, str] | None
    correct_option_index: int | None
    explanation: str | None
    format: QuestionFormat
    format_payload: dict[str, Any] | None
    provenance: str
    category: str | None
    category_id: str | None
    difficulty: str | None
    time_limit_seconds: int | None
    tags: tuple[str, ...]
    language: str
    learning_levels: tuple[str, ...]
    review_status: str
    source_name: str | None
    source_url: str | None
    source_published_at: date | None
    verified_at: date | None
    review_due_at: date | None


class AuthorQuizResponse(BaseModel):
    id: str
    title: str
    description: str | None
    category: str
    difficulty: str
    visibility: str
    status: str
    default_time_limit_seconds: int
    provenance: str
    version: int
    questions: tuple[AuthorQuestionResponse, ...]


class PublicQuizListResponse(BaseModel):
    quizzes: tuple[PlayerQuizResponse, ...]


class OwnerQuizListResponse(BaseModel):
    quizzes: tuple[AuthorQuizResponse, ...]


def _required_capability(authorization: str | None) -> str:
    if authorization is None:
        raise ApiProblem(401, "capability_required", "A quiz owner capability is required.")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not 40 <= len(token.strip()) <= 128:
        raise ApiProblem(401, "invalid_capability", "The capability is invalid or revoked.")
    return token.strip()


def _author_not_found() -> ApiProblem:
    return ApiProblem(404, "quiz_not_found", "The quiz was not found or is not available to this capability.")


def _author_response(value: AuthorQuizView) -> AuthorQuizResponse:
    return AuthorQuizResponse.model_validate(value, from_attributes=True)


def _player_response(value: PlayerQuizView) -> PlayerQuizResponse:
    return PlayerQuizResponse.model_validate(value, from_attributes=True)


def install_quiz_api(app: FastAPI, access: QuizAccessService, generation: GenerationService | None = None) -> None:
    """Install quiz routes and sanitized errors without touching WebSocket routes."""
    router = APIRouter(prefix="/api")
    owner_creation_limiter = OwnerCreationRateLimiter()
    generation_service = generation or GenerationService()
    if generation_service.database is None:
        generation_service.database = access._database
    app.state.generation_service = generation_service

    @app.middleware("http")
    async def prevent_quiz_api_caching(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/api" or request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Vary"] = "Authorization"
        return response

    @app.exception_handler(ApiProblem)
    async def api_problem_handler(_request: Request, error: ApiProblem) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message}},
            headers={"Cache-Control": "no-store", **error.headers},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, error: RequestValidationError) -> JSONResponse:
        # Exclude input/context: rejected payloads may contain bearer capabilities or answer keys.
        details = [
            {"location": list(item.get("loc", ())), "message": item.get("msg", "Invalid value."), "type": item.get("type", "value_error")}
            for item in error.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_request", "message": "Request validation failed.", "details": details}},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error_handler(_request: Request, _error: SQLAlchemyError) -> JSONResponse:
        # Do not return SQL, bound parameters, connection URLs, or database content.
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "persistence_unavailable", "message": "Quiz storage is temporarily unavailable."}},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(GenerationError)
    async def generation_error_handler(_request: Request, error: GenerationError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status,
            content={"error": {"code": error.code, "message": error.message}},
            headers={"Cache-Control": "no-store"},
        )

    async def authorized_generation_owner(authorization: str | None) -> str:
        capability = _required_capability(authorization)
        if await asyncio.to_thread(access.list_owned_quizzes, capability) is None:
            raise ApiProblem(401, "invalid_capability", "The capability is invalid or revoked.")
        return capability

    @router.get("/ai/status")
    def ai_generation_status() -> dict[str, bool]:
        return {"enabled": generation_service.enabled}

    @router.post("/ai/generations")
    async def generate_topic_questions(
        body: GenerationRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        capability = await authorized_generation_owner(authorization)
        if body.method != "topic":
            raise ApiProblem(422, "invalid_generation_request", "Topic generation requires method 'topic'.")
        result = await generation_service.generate(body, None, capability)
        return {"questions": result["questions"], "generation_ticket": result["generation_ticket"]}

    @router.post("/ai/generations/document")
    async def generate_document_questions(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        capability = await authorized_generation_owner(authorization)
        body, upload, raw = await parse_generation_form(request)
        text = await asyncio.to_thread(extract_upload, upload, raw)
        try:
            result = await generation_service.generate(body, text, capability)
        finally:
            await upload.close()
            text = ""
        return {"questions": result["questions"], "generation_ticket": result["generation_ticket"]}

    @router.post("/quiz-owners", response_model=OwnerCapabilityResponse, status_code=201)
    def create_owner(request: Request, response: Response) -> OwnerCapabilityResponse:
        client_key = request.client.host if request.client is not None else "unknown"
        allowed, retry_after = owner_creation_limiter.allow(client_key)
        if not allowed:
            raise ApiProblem(
                429,
                "owner_creation_rate_limited",
                "Too many owner capabilities were created from this client. Try again shortly.",
                headers={"Retry-After": str(retry_after)},
            )
        issued = access.create_owner_capability()
        response.headers["Cache-Control"] = "no-store"
        return OwnerCapabilityResponse(owner_id=issued.owner_id, capability=issued.token)

    @router.get("/quizzes", response_model=PublicQuizListResponse)
    def list_public_quizzes(
        category: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
        difficulty: Literal["easy", "medium", "hard"] | None = None,
        q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        question_format: Literal["multiple_choice", "open_ended", "puzzle"] = "multiple_choice",
    ) -> PublicQuizListResponse:
        try:
            quizzes = access.list_public_quizzes(
                category=category, difficulty=difficulty, search=q, limit=limit,
                question_format=question_format,
            )
        except ValueError as error:
            raise ApiProblem(422, "invalid_filter", str(error)) from error
        return PublicQuizListResponse(quizzes=tuple(_player_response(quiz) for quiz in quizzes))

    @router.get("/quizzes/mine", response_model=OwnerQuizListResponse)
    def list_owned_quizzes(
        authorization: Annotated[str | None, Header()] = None,
    ) -> OwnerQuizListResponse:
        capability = _required_capability(authorization)
        quizzes = access.list_owned_quizzes(capability)
        if quizzes is None:
            raise ApiProblem(401, "invalid_capability", "The capability is invalid or revoked.")
        return OwnerQuizListResponse(quizzes=tuple(_author_response(quiz) for quiz in quizzes))

    @router.post("/quizzes", response_model=AuthorQuizResponse, status_code=201)
    def create_quiz(
        body: QuizCreateInput,
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> AuthorQuizResponse:
        capability = _required_capability(authorization)
        if body.generation_ticket is not None and not generation_service.ticket_valid(body.generation_ticket, capability):
            raise ApiProblem(422, "invalid_generation_ticket", "This generated draft marker is invalid or expired. Generate the questions again.")
        try:
            quiz = access.create_quiz(
                capability,
                body.to_record(),
                [question.to_record() for question in body.questions],
                ai_generated=generation_service.ticket_valid(body.generation_ticket, capability),
            )
        except ValueError as error:
            raise ApiProblem(422, "invalid_quiz", str(error)) from error
        if quiz is None:
            raise ApiProblem(401, "invalid_capability", "The capability is invalid or revoked.")
        response.headers["Cache-Control"] = "no-store"
        return _author_response(quiz)

    @router.get(
        "/quizzes/{quiz_id}", response_model=AuthorQuizResponse | PlayerQuizResponse
    )
    def get_quiz(
        quiz_id: Annotated[str, Path(min_length=1, max_length=64)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> AuthorQuizResponse | PlayerQuizResponse:
        capability = None
        if authorization is not None:
            capability = _required_capability(authorization)
        result = access.get_authorized_quiz(quiz_id, capability=capability)
        if result is None:
            raise _author_not_found()
        if isinstance(result, AuthorQuizView):
            return _author_response(result)
        return _player_response(result)

    @router.patch("/quizzes/{quiz_id}", response_model=AuthorQuizResponse)
    def update_quiz(
        body: QuizUpdateInput,
        quiz_id: Annotated[str, Path(min_length=1, max_length=64)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> AuthorQuizResponse:
        capability = _required_capability(authorization)
        try:
            result = access.update_quiz(
                capability,
                quiz_id,
                expected_version=body.expected_version,
                changes=body.changes(),
                questions=body.question_records(),
            )
        except QuizVersionConflict as error:
            raise ApiProblem(409, "version_conflict", str(error)) from error
        except ValueError as error:
            raise ApiProblem(422, "invalid_quiz", str(error)) from error
        if result is None:
            raise _author_not_found()
        return _author_response(result)

    @router.post("/quizzes/{quiz_id}/publish", response_model=AuthorQuizResponse)
    def publish_quiz(
        quiz_id: Annotated[str, Path(min_length=1, max_length=64)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> AuthorQuizResponse:
        return change_publication(quiz_id, authorization, published=True)

    @router.post("/quizzes/{quiz_id}/unpublish", response_model=AuthorQuizResponse)
    def unpublish_quiz(
        quiz_id: Annotated[str, Path(min_length=1, max_length=64)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> AuthorQuizResponse:
        return change_publication(quiz_id, authorization, published=False)

    def change_publication(
        quiz_id: str, authorization: str | None, *, published: bool
    ) -> AuthorQuizResponse:
        capability = _required_capability(authorization)
        try:
            result = access.set_quiz_published(capability, quiz_id, published=published)
        except ValueError as error:
            raise ApiProblem(422, "quiz_not_publishable", str(error)) from error
        if result is None:
            raise _author_not_found()
        return _author_response(result)

    @router.post("/quizzes/{quiz_id}/duplicate", response_model=AuthorQuizResponse, status_code=201)
    def duplicate_quiz(
        quiz_id: Annotated[str, Path(min_length=1, max_length=64)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> AuthorQuizResponse:
        capability = _required_capability(authorization)
        result = access.duplicate_public_quiz(capability, quiz_id)
        if result is None:
            raise _author_not_found()
        return _author_response(result)

    @router.delete("/quizzes/{quiz_id}", status_code=204)
    def delete_quiz(
        quiz_id: Annotated[str, Path(min_length=1, max_length=64)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        capability = _required_capability(authorization)
        if not access.delete_quiz(capability, quiz_id):
            raise _author_not_found()
        return Response(status_code=204)

    @router.post("/quizzes/{quiz_id}/shares", response_model=ShareResponse, status_code=201)
    def create_share(
        body: ShareInput,
        quiz_id: Annotated[str, Path(min_length=1, max_length=64)],
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> ShareResponse:
        capability = _required_capability(authorization)
        try:
            issued = access.create_share(
                capability,
                quiz_id,
                editable=False,
                expires_in_seconds=body.expires_in_seconds,
            )
        except PermissionError as error:
            raise ApiProblem(409, "quiz_not_shareable", str(error)) from error
        if issued is None:
            raise _author_not_found()
        response.headers["Cache-Control"] = "no-store"
        return ShareResponse(
            grant_id=issued.grant_id,
            scope=CapabilityScope.QUIZ_READ,
            capability=issued.token,
        )

    @router.delete("/quizzes/{quiz_id}/shares/{grant_id}", status_code=204)
    def revoke_share(
        quiz_id: Annotated[str, Path(min_length=1, max_length=64)],
        grant_id: Annotated[str, Path(min_length=1, max_length=32)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        capability = _required_capability(authorization)
        if not access.revoke_share(capability, quiz_id, grant_id):
            raise _author_not_found()
        return Response(status_code=204)

    app.include_router(router)
