"""FastAPI router for AI intelligence, question quality, and personalized learning engine."""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, Query, status
from pydantic import BaseModel, Field

from quizverse.intelligence.intelligence_service import AIIntelligenceService
from quizverse.persistence.player_identity import PlayerIdentityError, PlayerIdentityService
from quizverse.quiz_api import ApiProblem


class IntelligenceRateLimiter:
    """Sliding-window in-memory action rate limiter per player."""

    def __init__(self, limit: int = 60, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self.history: dict[str, list[float]] = {}

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.time()
        bucket = [t for t in self.history.get(key, []) if now - t < self.window]
        if len(bucket) >= self.limit:
            oldest = bucket[0]
            retry_after = max(1, int(self.window - (now - oldest)))
            return False, retry_after
        bucket.append(now)
        self.history[key] = bucket
        return True, 0


class AnalyzeQuestionRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    options: list[str] = Field(min_length=2, max_length=8)
    correct_option_index: int = Field(default=0, ge=0, le=7)
    correct_index: int | None = None
    explanation: str = Field(default="", max_length=2000)
    category: str = Field(default="", max_length=100)
    difficulty: str = Field(default="medium", max_length=32)
    quiz_id: str | None = None
    question_id: str | None = None


class ValidateAnswerRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    options: list[str] = Field(min_length=2, max_length=8)
    correct_option_index: int = Field(default=0, ge=0, le=7)
    correct_index: int | None = None
    explanation: str = Field(default="", max_length=2000)
    format_payload: dict[str, Any] | None = None


class CheckDuplicateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    exclude_id: str | None = None
    category: str | None = None


class GenerateLearningPathRequest(BaseModel):
    regenerate: bool = False


class GenerateReportRequest(BaseModel):
    time_window: str = Field(default="recent", pattern=r"^(recent|7_days|30_days|all_time)$")
    report_type: str = Field(default="comprehensive", max_length=64)


class RecordTopicPerformanceRequest(BaseModel):
    category_id: str = Field(min_length=1, max_length=100)
    category_name: str = Field(min_length=1, max_length=120)
    attempts: int = Field(ge=1, le=1000)
    correct_count: int = Field(ge=0, le=1000)
    difficulty: str = Field(default="medium", max_length=32)
    avg_response_time_ms: int = Field(default=0, ge=0)


def _authenticated_header(authorization: str | None) -> str:
    if authorization is None:
        raise ApiProblem(401, "invalid_player_credential", "A valid player credential is required.")
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token:
        raise ApiProblem(401, "invalid_player_credential", "A valid player credential is required.")
    return token


def register_intelligence_api(
    app: FastAPI,
    intelligence_service: AIIntelligenceService,
    player_identity_service: PlayerIdentityService,
) -> None:
    router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])
    rate_limiter = IntelligenceRateLimiter(limit=100, window_seconds=60.0)

    def _get_player_id(authorization: str | None) -> str:
        token = _authenticated_header(authorization)
        try:
            profile = player_identity_service.authenticate(token)
            return str(profile["id"])
        except PlayerIdentityError:
            raise ApiProblem(401, "invalid_player_credential", "The player credential is invalid or revoked.")

    def _check_rate_limit(player_id: str) -> None:
        allowed, retry_after = rate_limiter.allow(player_id)
        if not allowed:
            raise ApiProblem(
                429,
                "rate_limited",
                f"Rate limit exceeded. Please wait {retry_after} seconds before retrying.",
            )

    # -------------------------------------------------------------------------
    # 1. Overview & General Intelligence
    # -------------------------------------------------------------------------

    @router.get("/overview")
    def get_overview(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        return intelligence_service.get_intelligence_overview(player_id)

    @router.get("/performance")
    def get_performance(
        time_window: Annotated[str, Query()] = "recent",
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        if time_window not in {"recent", "7_days", "30_days", "all_time"}:
            time_window = "recent"
        return intelligence_service.get_player_performance(player_id, time_window=time_window)

    @router.get("/performance/topics")
    def get_performance_topics(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        return intelligence_service.get_player_weaknesses(player_id)

    @router.post("/performance/topics", status_code=status.HTTP_201_CREATED)
    def record_topic_performance(
        body: RecordTopicPerformanceRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        return intelligence_service.record_topic_performance(
            player_id=player_id,
            category_id=body.category_id,
            category_name=body.category_name,
            attempts=body.attempts,
            correct_count=body.correct_count,
            difficulty=body.difficulty,
            avg_response_time_ms=body.avg_response_time_ms,
        )

    @router.get("/weaknesses")
    def get_weaknesses(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        return intelligence_service.get_player_weaknesses(player_id)

    @router.get("/difficulty")
    def get_adaptive_difficulty(
        current_preference: Annotated[str, Query()] = "medium",
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        return intelligence_service.get_adaptive_difficulty(player_id, current_preference=current_preference)

    @router.get("/recommendations")
    def get_recommendations(
        limit: Annotated[int, Query(ge=1, le=20)] = 6,
        authorization: Annotated[str | None, Header()] = None,
    ) -> list[dict[str, Any]]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        return intelligence_service.get_recommendations(player_id, limit=limit)

    # -------------------------------------------------------------------------
    # 2. Personalized Learning Paths
    # -------------------------------------------------------------------------

    @router.get("/learning-paths")
    def get_active_learning_path(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        return intelligence_service.get_or_create_learning_path(player_id, regenerate=False)

    @router.post("/learning-paths/generate", status_code=status.HTTP_201_CREATED)
    def generate_learning_path(
        body: GenerateLearningPathRequest | None = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        regenerate = body.regenerate if body else True
        return intelligence_service.get_or_create_learning_path(player_id, regenerate=regenerate)

    @router.get("/learning-paths/{path_id}")
    def get_learning_path(
        path_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        path = intelligence_service.get_or_create_learning_path(player_id, regenerate=False)
        if path.get("id") != path_id:
            raise ApiProblem(404, "path_not_found", "Learning path not found or not owned by player.")
        return path

    @router.post("/learning-paths/{path_id}/steps/{step_id}/complete")
    def complete_learning_step(
        path_id: str,
        step_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        try:
            return intelligence_service.complete_learning_step(player_id, path_id, step_id)
        except ValueError as err:
            raise ApiProblem(404, "step_not_found", str(err))

    # -------------------------------------------------------------------------
    # 3. AI Learning Reports
    # -------------------------------------------------------------------------

    @router.get("/reports")
    def list_reports(
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
        authorization: Annotated[str | None, Header()] = None,
    ) -> list[dict[str, Any]]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        return intelligence_service.list_learning_reports(player_id, limit=limit)

    @router.post("/reports/generate", status_code=status.HTTP_201_CREATED)
    async def generate_report(
        body: GenerateReportRequest | None = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        token = _authenticated_header(authorization)
        try:
            profile = player_identity_service.authenticate(token)
            player_id = str(profile["id"])
            player_name = str(profile.get("display_name", "Player"))
        except PlayerIdentityError:
            raise ApiProblem(401, "invalid_player_credential", "The player credential is invalid or revoked.")

        _check_rate_limit(player_id)
        time_window = body.time_window if body else "recent"
        report_type = body.report_type if body else "comprehensive"
        return await intelligence_service.generate_learning_report(
            player_id=player_id,
            player_name=player_name,
            time_window=time_window,
            report_type=report_type,
        )

    @router.get("/reports/{report_id}")
    def get_report(
        report_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        report = intelligence_service.get_learning_report(player_id, report_id)
        if not report:
            raise ApiProblem(404, "report_not_found", "Learning report not found.")
        return report

    # -------------------------------------------------------------------------
    # 4. System Health & Models
    # -------------------------------------------------------------------------

    @router.get("/health")
    def get_health() -> dict[str, Any]:
        return {
            "status": "ok",
            "capabilities": [
                "question_quality_scoring",
                "answer_validation",
                "duplicate_detection",
                "adaptive_difficulty",
                "weakness_detection",
                "learning_paths",
                "recommendations",
                "performance_analytics",
                "learning_reports",
            ],
        }

    @router.get("/models")
    def get_models() -> dict[str, Any]:
        return {
            "active_provider": "deterministic_fallback",
            "fallback_available": True,
            "external_ai_configured": False,
        }

    @router.get("/bank/summary")
    def get_bank_summary() -> dict[str, Any]:
        from quizverse.questions import QUESTIONS
        return {
            "total_questions": len(QUESTIONS),
            "source": "curated_in_memory_and_persisted",
        }

    # -------------------------------------------------------------------------
    # 5. Question Intelligence & Creator Tools
    # -------------------------------------------------------------------------

    @router.post("/questions/analyze")
    def analyze_question(
        body: AnalyzeQuestionRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        if authorization:
            player_id = _get_player_id(authorization)
            _check_rate_limit(player_id)
        idx = body.correct_index if body.correct_index is not None else body.correct_option_index
        return intelligence_service.analyze_question(
            prompt=body.prompt,
            options=body.options,
            correct_option_index=idx,
            explanation=body.explanation,
            category=body.category,
            difficulty=body.difficulty,
            quiz_id=body.quiz_id,
            question_id=body.question_id,
        )

    @router.post("/questions/validate")
    @router.post("/questions/validate-answer")
    def validate_answer(
        body: ValidateAnswerRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        if authorization:
            player_id = _get_player_id(authorization)
            _check_rate_limit(player_id)
        idx = body.correct_index if body.correct_index is not None else body.correct_option_index
        return intelligence_service.validate_answer(
            prompt=body.prompt,
            options=body.options,
            correct_option_index=idx,
            explanation=body.explanation,
            format_payload=body.format_payload,
        )

    @router.post("/questions/check-duplicates")
    @router.post("/questions/check-duplicate")
    def check_duplicates(
        body: CheckDuplicateRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        if authorization:
            player_id = _get_player_id(authorization)
            _check_rate_limit(player_id)
        return intelligence_service.check_duplicates(
            prompt=body.prompt,
            exclude_id=body.exclude_id,
            category=body.category,
        )

    app.include_router(router)
