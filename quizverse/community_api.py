"""FastAPI REST routing for the Stage 11 Creator and Community ecosystem."""

from __future__ import annotations

import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, Header, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from quizverse.community.community_service import CommunityService
from quizverse.persistence.player_identity import PlayerIdentityError, PlayerIdentityService
from quizverse.quiz_api import ApiProblem


class CommunityRateLimiter:
    """Sliding-window in-memory rate limiter per player key."""

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


# Request Schemas


class UpdateCreatorProfileRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=32)
    bio: str | None = Field(default=None, max_length=1000)
    specializations: list[str] | None = Field(default=None, max_length=10)
    visibility: Literal["public", "private"] | None = None
    avatar_url: str | None = Field(default=None, max_length=255)
    banner_url: str | None = Field(default=None, max_length=255)


class PublishQuizRequest(BaseModel):
    visibility: Literal["public", "unlisted", "private"] = "public"


class RecordQuizPlayRequest(BaseModel):
    score: int | None = Field(default=None, ge=0)


class SubmitReviewRequest(BaseModel):
    rating: int = Field(ge=1, le=5)
    review_text: str | None = Field(default=None, max_length=1000)


class CreatorResponseRequest(BaseModel):
    response_text: str = Field(min_length=1, max_length=1000)


class CreateCollectionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    visibility: Literal["public", "private", "unlisted"] = "public"
    cover_url: str | None = Field(default=None, max_length=255)


class UpdateCollectionRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    visibility: Literal["public", "private", "unlisted"] | None = None
    cover_url: str | None = Field(default=None, max_length=255)


class AddCollectionQuizRequest(BaseModel):
    quiz_id: str = Field(min_length=1, max_length=64)
    position: int | None = Field(default=None, ge=0)


class ReorderCollectionRequest(BaseModel):
    quiz_ids: list[str] = Field(min_length=1, max_length=100)


class SubmitReportRequest(BaseModel):
    target_type: Literal["quiz", "review", "creator", "question"]
    target_id: str = Field(min_length=1, max_length=64)
    reason: Literal[
        "inappropriate",
        "offensive",
        "incorrect_answer",
        "plagiarism",
        "spam",
        "abusive_review",
        "harassment",
        "impersonation",
        "other",
    ]
    description: str | None = Field(default=None, max_length=2000)


class ResolveReportRequest(BaseModel):
    action: Literal[
        "suspend_quiz",
        "restore_quiz",
        "hide_review",
        "restore_review",
        "dismiss_report",
        "warn_creator",
    ]
    notes: str | None = Field(default=None, max_length=2000)


def register_community_api(
    app: FastAPI,
    community_service: CommunityService,
    player_identities: PlayerIdentityService,
) -> None:
    """Registers /api/community routes on the FastAPI instance."""
    router = APIRouter(prefix="/api/community", tags=["community"])
    rate_limiter = CommunityRateLimiter(limit=90, window_seconds=60.0)

    def _get_player_id(authorization: str | None, *, required: bool = True) -> str | None:
        if not authorization:
            if required:
                raise ApiProblem(401, "unauthorized", "Player authentication token required.")
            return None

        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            if required:
                raise ApiProblem(401, "unauthorized", "Bearer token authorization header expected.")
            return None

        try:
            player_dict = player_identities.authenticate(token.strip())
            return str(player_dict["id"])
        except PlayerIdentityError as err:
            if required:
                raise ApiProblem(401, "invalid_token", err.message)
            return None

    def _check_rate_limit(player_id: str | None) -> None:
        key = player_id or "anonymous"
        allowed, retry_after = rate_limiter.allow(key)
        if not allowed:
            raise ApiProblem(429, "rate_limited", f"Too many requests. Please wait {retry_after} seconds.")

    # -------------------------------------------------------------------------
    # Health
    # -------------------------------------------------------------------------

    @router.get("/health")
    def get_health() -> dict[str, Any]:
        return {
            "status": "online",
            "module": "community_ecosystem",
            "features": [
                "creator_profiles",
                "quiz_publishing",
                "discovery_and_search",
                "verified_reviews",
                "creator_follows",
                "collections",
                "favorites",
                "community_moderation",
            ],
        }

    # -------------------------------------------------------------------------
    # Discovery Feeds & Public Search
    # -------------------------------------------------------------------------

    @router.get("/overview")
    def get_overview() -> dict[str, Any]:
        return community_service.discovery.get_discovery_overview()

    @router.get("/quizzes")
    def search_quizzes(
        q: Annotated[str | None, Query()] = None,
        category: Annotated[str | None, Query()] = None,
        difficulty: Annotated[str | None, Query()] = None,
        min_rating: Annotated[float | None, Query(ge=0.0, le=5.0)] = None,
        sort_by: Annotated[str, Query()] = "recent",
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        return community_service.discovery.search_quizzes(
            query=q,
            category=category,
            difficulty=difficulty,
            min_rating=min_rating,
            sort_by=sort_by,
            limit=limit,
            offset=offset,
        )

    @router.get("/quizzes/featured")
    def get_featured(limit: Annotated[int, Query(ge=1, le=50)] = 10) -> list[dict[str, Any]]:
        return community_service.discovery.get_featured_quizzes(limit=limit)

    @router.get("/quizzes/trending")
    def get_trending(limit: Annotated[int, Query(ge=1, le=50)] = 10) -> list[dict[str, Any]]:
        return community_service.discovery.get_trending_quizzes(limit=limit)

    @router.get("/quizzes/popular")
    def get_popular(limit: Annotated[int, Query(ge=1, le=50)] = 10) -> list[dict[str, Any]]:
        return community_service.discovery.get_popular_quizzes(limit=limit)

    @router.get("/quizzes/recent")
    def get_recent(limit: Annotated[int, Query(ge=1, le=50)] = 10) -> list[dict[str, Any]]:
        return community_service.discovery.get_recent_quizzes(limit=limit)

    @router.get("/quizzes/top-rated")
    def get_top_rated(limit: Annotated[int, Query(ge=1, le=50)] = 10) -> list[dict[str, Any]]:
        return community_service.discovery.get_top_rated_quizzes(limit=limit)

    @router.get("/quizzes/share/{slug}")
    def get_shared_quiz(
        slug: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        viewer_id = _get_player_id(authorization, required=False)
        try:
            return community_service.publications.get_publication_by_slug(slug, viewer_id=viewer_id)
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))

    @router.get("/quizzes/{quiz_id}")
    def get_quiz_publication(
        quiz_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        viewer_id = _get_player_id(authorization, required=False)
        try:
            return community_service.publications.get_publication(quiz_id, viewer_id=viewer_id)
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))

    # -------------------------------------------------------------------------
    # Quiz Publishing & Play Sessions
    # -------------------------------------------------------------------------

    @router.post("/quizzes/{quiz_id}/publish")
    def publish_quiz(
        quiz_id: str,
        body: PublishQuizRequest | None = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        creator_id = _get_player_id(authorization)
        _check_rate_limit(creator_id)
        vis = body.visibility if body else "public"
        try:
            return community_service.publications.publish_quiz(
                creator_id=creator_id,
                quiz_id=quiz_id,
                visibility=vis,
            )
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))
        except ValueError as err:
            raise ApiProblem(422, "validation_error", str(err))

    @router.post("/quizzes/{quiz_id}/unpublish")
    def unpublish_quiz(
        quiz_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        creator_id = _get_player_id(authorization)
        _check_rate_limit(creator_id)
        try:
            return community_service.publications.unpublish_quiz(creator_id=creator_id, quiz_id=quiz_id)
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))
        except Exception as err:
            raise ApiProblem(400, "bad_request", str(err))

    @router.post("/quizzes/{quiz_id}/submit-review")
    def submit_for_review(
        quiz_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        creator_id = _get_player_id(authorization)
        _check_rate_limit(creator_id)
        try:
            return community_service.publications.submit_for_review(creator_id=creator_id, quiz_id=quiz_id)
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))
        except Exception as err:
            raise ApiProblem(400, "bad_request", str(err))

    @router.post("/quizzes/{quiz_id}/archive")
    def archive_quiz(
        quiz_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        creator_id = _get_player_id(authorization)
        _check_rate_limit(creator_id)
        try:
            return community_service.publications.archive_quiz(creator_id=creator_id, quiz_id=quiz_id)
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))
        except Exception as err:
            raise ApiProblem(400, "bad_request", str(err))

    @router.post("/quizzes/{quiz_id}/play")
    def record_quiz_play(
        quiz_id: str,
        body: RecordQuizPlayRequest | None = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        score = body.score if body else None
        return community_service.publications.record_quiz_play(
            player_id=player_id,
            quiz_id=quiz_id,
            score=score,
        )

    # -------------------------------------------------------------------------
    # Creator Profiles & Follow System
    # -------------------------------------------------------------------------

    @router.get("/creators")
    def list_creators(
        specialization: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[dict[str, Any]]:
        return community_service.creators.list_creators(
            limit=limit,
            offset=offset,
            specialization=specialization,
        )

    @router.get("/creators/me")
    def get_my_creator_profile(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        return community_service.creators.get_or_create_creator_profile(player_id)

    @router.put("/creators/me")
    def update_my_creator_profile(
        body: UpdateCreatorProfileRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        try:
            return community_service.creators.update_creator_profile(
                player_id=player_id,
                display_name=body.display_name,
                bio=body.bio,
                specializations=body.specializations,
                visibility=body.visibility,
                avatar_url=body.avatar_url,
                banner_url=body.banner_url,
            )
        except ValueError as err:
            raise ApiProblem(422, "validation_error", str(err))

    @router.get("/creators/following")
    def list_my_following(
        authorization: Annotated[str | None, Header()] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[dict[str, Any]]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        return community_service.creators.list_following(player_id, limit=limit, offset=offset)

    @router.get("/creators/{creator_id}")
    def get_creator(
        creator_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        viewer_id = _get_player_id(authorization, required=False)
        try:
            return community_service.creators.get_creator_profile(creator_id, viewer_id=viewer_id)
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))

    @router.get("/creators/{creator_id}/quizzes")
    def get_creator_quizzes(
        creator_id: str,
        status: Annotated[str | None, Query()] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> list[dict[str, Any]]:
        viewer_id = _get_player_id(authorization, required=False)
        return community_service.publications.list_creator_quizzes(
            creator_id=creator_id,
            viewer_id=viewer_id,
            status=status,
        )

    @router.get("/creators/{creator_id}/analytics")
    def get_creator_analytics(
        creator_id: str,
        time_window: Annotated[str, Query()] = "all_time",
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        viewer_id = _get_player_id(authorization)
        _check_rate_limit(viewer_id)
        if viewer_id != creator_id and not community_service.moderation.is_moderator(viewer_id):
            raise ApiProblem(403, "forbidden", "Creator analytics are private to the creator.")
        try:
            return community_service.creators.get_creator_analytics(creator_id, time_window=time_window)
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))

    @router.post("/creators/follow/{creator_id}")
    def follow_creator(
        creator_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        follower_id = _get_player_id(authorization)
        _check_rate_limit(follower_id)
        try:
            return community_service.creators.follow_creator(follower_id=follower_id, creator_id=creator_id)
        except ValueError as err:
            raise ApiProblem(422, "validation_error", str(err))
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))

    @router.delete("/creators/follow/{creator_id}")
    def unfollow_creator(
        creator_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        follower_id = _get_player_id(authorization)
        _check_rate_limit(follower_id)
        return community_service.creators.unfollow_creator(follower_id=follower_id, creator_id=creator_id)

    @router.get("/creators/{creator_id}/followers")
    def list_creator_followers(
        creator_id: str,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[dict[str, Any]]:
        return community_service.creators.list_followers(creator_id=creator_id, limit=limit, offset=offset)

    # -------------------------------------------------------------------------
    # Ratings & Reviews
    # -------------------------------------------------------------------------

    @router.get("/quizzes/{quiz_id}/reviews")
    def list_quiz_reviews(
        quiz_id: str,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        return community_service.reviews.list_reviews(quiz_id=quiz_id, limit=limit, offset=offset)

    @router.post("/quizzes/{quiz_id}/reviews")
    def submit_review(
        quiz_id: str,
        body: SubmitReviewRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        try:
            return community_service.reviews.submit_review(
                player_id=player_id,
                quiz_id=quiz_id,
                rating=body.rating,
                review_text=body.review_text,
            )
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))
        except ValueError as err:
            raise ApiProblem(422, "validation_error", str(err))

    @router.delete("/reviews/{review_id}")
    def delete_review(
        review_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        try:
            return community_service.reviews.delete_review(player_id=player_id, review_id=review_id)
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))
        except Exception as err:
            raise ApiProblem(400, "bad_request", str(err))

    @router.post("/reviews/{review_id}/respond")
    def creator_respond_to_review(
        review_id: str,
        body: CreatorResponseRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        creator_id = _get_player_id(authorization)
        _check_rate_limit(creator_id)
        try:
            return community_service.reviews.creator_respond(
                creator_id=creator_id,
                review_id=review_id,
                response_text=body.response_text,
            )
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))
        except ValueError as err:
            raise ApiProblem(422, "validation_error", str(err))

    @router.post("/reviews/{review_id}/helpful")
    def mark_review_helpful(
        review_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        try:
            return community_service.reviews.mark_helpful(player_id=player_id, review_id=review_id)
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))
        except ValueError as err:
            raise ApiProblem(422, "validation_error", str(err))

    # -------------------------------------------------------------------------
    # Favorites & Bookmarks
    # -------------------------------------------------------------------------

    @router.get("/favorites")
    def list_favorites(
        authorization: Annotated[str | None, Header()] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[dict[str, Any]]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        return community_service.favorites.list_favorites(player_id, limit=limit, offset=offset)

    @router.post("/favorites/{quiz_id}")
    def add_favorite(
        quiz_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        try:
            return community_service.favorites.add_favorite(player_id=player_id, quiz_id=quiz_id)
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))

    @router.delete("/favorites/{quiz_id}")
    def remove_favorite(
        quiz_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        player_id = _get_player_id(authorization)
        _check_rate_limit(player_id)
        return community_service.favorites.remove_favorite(player_id=player_id, quiz_id=quiz_id)

    # -------------------------------------------------------------------------
    # Playlists & Collections
    # -------------------------------------------------------------------------

    @router.get("/collections")
    def list_collections(
        owner_id: Annotated[str | None, Query()] = None,
        visibility: Annotated[str | None, Query()] = "public",
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[dict[str, Any]]:
        return community_service.collections.list_collections(
            owner_id=owner_id,
            visibility=visibility,
            limit=limit,
            offset=offset,
        )

    @router.post("/collections", status_code=status.HTTP_201_CREATED)
    def create_collection(
        body: CreateCollectionRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        owner_id = _get_player_id(authorization)
        _check_rate_limit(owner_id)
        try:
            return community_service.collections.create_collection(
                owner_id=owner_id,
                title=body.title,
                description=body.description,
                visibility=body.visibility,
                cover_url=body.cover_url,
            )
        except ValueError as err:
            raise ApiProblem(422, "validation_error", str(err))

    @router.get("/collections/{collection_id}")
    def get_collection(
        collection_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        viewer_id = _get_player_id(authorization, required=False)
        try:
            return community_service.collections.get_collection(collection_id, viewer_id=viewer_id)
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))

    @router.put("/collections/{collection_id}")
    def update_collection(
        collection_id: str,
        body: UpdateCollectionRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        owner_id = _get_player_id(authorization)
        _check_rate_limit(owner_id)
        try:
            return community_service.collections.update_collection(
                owner_id=owner_id,
                collection_id=collection_id,
                title=body.title,
                description=body.description,
                visibility=body.visibility,
                cover_url=body.cover_url,
            )
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))
        except ValueError as err:
            raise ApiProblem(422, "validation_error", str(err))

    @router.delete("/collections/{collection_id}")
    def delete_collection(
        collection_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        owner_id = _get_player_id(authorization)
        _check_rate_limit(owner_id)
        try:
            return community_service.collections.delete_collection(owner_id=owner_id, collection_id=collection_id)
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))

    @router.post("/collections/{collection_id}/quizzes")
    def add_quiz_to_collection(
        collection_id: str,
        body: AddCollectionQuizRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        owner_id = _get_player_id(authorization)
        _check_rate_limit(owner_id)
        try:
            return community_service.collections.add_quiz_to_collection(
                owner_id=owner_id,
                collection_id=collection_id,
                quiz_id=body.quiz_id,
                position=body.position,
            )
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))

    @router.post("/collections/{collection_id}/quizzes/{quiz_id}")
    def add_quiz_to_collection_path(
        collection_id: str,
        quiz_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        owner_id = _get_player_id(authorization)
        _check_rate_limit(owner_id)
        try:
            return community_service.collections.add_quiz_to_collection(
                owner_id=owner_id,
                collection_id=collection_id,
                quiz_id=quiz_id,
            )
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))

    @router.delete("/collections/{collection_id}/quizzes/{quiz_id}")
    def remove_quiz_from_collection(
        collection_id: str,
        quiz_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        owner_id = _get_player_id(authorization)
        _check_rate_limit(owner_id)
        try:
            return community_service.collections.remove_quiz_from_collection(
                owner_id=owner_id,
                collection_id=collection_id,
                quiz_id=quiz_id,
            )
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))

    @router.put("/collections/{collection_id}/reorder")
    def reorder_collection(
        collection_id: str,
        body: ReorderCollectionRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        owner_id = _get_player_id(authorization)
        _check_rate_limit(owner_id)
        try:
            return community_service.collections.reorder_collection(
                owner_id=owner_id,
                collection_id=collection_id,
                ordered_quiz_ids=body.quiz_ids,
            )
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))

    # -------------------------------------------------------------------------
    # Moderation & Trust
    # -------------------------------------------------------------------------

    @router.post("/reports", status_code=status.HTTP_201_CREATED)
    def submit_report(
        body: SubmitReportRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        reporter_id = _get_player_id(authorization)
        _check_rate_limit(reporter_id)
        try:
            return community_service.moderation.submit_report(
                reporter_id=reporter_id,
                target_type=body.target_type,
                target_id=body.target_id,
                reason=body.reason,
                description=body.description,
            )
        except ValueError as err:
            raise ApiProblem(422, "validation_error", str(err))

    @router.get("/moderation/reports")
    def list_moderation_reports(
        status: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        moderator_id = _get_player_id(authorization)
        _check_rate_limit(moderator_id)
        try:
            return community_service.moderation.list_reports(
                moderator_id=moderator_id,
                status=status,
                limit=limit,
                offset=offset,
            )
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))

    @router.get("/moderation/reports/{report_id}")
    def get_moderation_report(
        report_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        moderator_id = _get_player_id(authorization)
        _check_rate_limit(moderator_id)
        try:
            return community_service.moderation.get_report(moderator_id=moderator_id, report_id=report_id)
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))

    @router.post("/moderation/reports/{report_id}/resolve")
    def resolve_moderation_report(
        report_id: str,
        body: ResolveReportRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        moderator_id = _get_player_id(authorization)
        _check_rate_limit(moderator_id)
        try:
            return community_service.moderation.resolve_report(
                moderator_id=moderator_id,
                report_id=report_id,
                action=body.action,
                notes=body.notes,
            )
        except LookupError as err:
            raise ApiProblem(404, "not_found", str(err))
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))
        except ValueError as err:
            raise ApiProblem(422, "validation_error", str(err))

    @router.get("/moderation/audit")
    def get_moderation_audit_log(
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
        authorization: Annotated[str | None, Header()] = None,
    ) -> list[dict[str, Any]]:
        moderator_id = _get_player_id(authorization)
        _check_rate_limit(moderator_id)
        try:
            return community_service.moderation.get_audit_log(
                moderator_id=moderator_id,
                limit=limit,
                offset=offset,
            )
        except PermissionError as err:
            raise ApiProblem(403, "forbidden", str(err))

    app.include_router(router)
