"""Unified Community & Creator Ecosystem orchestration service."""

from __future__ import annotations

from typing import Any

from quizverse.community.collection_service import CollectionService
from quizverse.community.creator_service import CreatorService
from quizverse.community.discovery_service import DiscoveryService
from quizverse.community.favorite_service import FavoriteService
from quizverse.community.moderation_service import ModerationService
from quizverse.community.publication_service import PublicationService
from quizverse.community.review_service import ReviewService
from quizverse.intelligence.duplicate_detection import DuplicateDetector
from quizverse.intelligence.question_quality import QuestionQualityAnalyzer
from quizverse.persistence.db import Database


class CommunityService:
    """Orchestrates creator identities, publishing, discovery, reviews, collections, and moderation."""

    def __init__(
        self,
        database: Database,
        notification_service: Any = None,
        social_service: Any = None,
        quality_analyzer: QuestionQualityAnalyzer | None = None,
        duplicate_detector: DuplicateDetector | None = None,
    ) -> None:
        self.database = database
        self.notification_service = notification_service
        self.social_service = social_service

        self.creators = CreatorService(
            database=database,
            notification_service=notification_service,
            social_service=social_service,
        )

        self.publications = PublicationService(
            database=database,
            quality_analyzer=quality_analyzer,
            duplicate_detector=duplicate_detector,
            notification_service=notification_service,
        )

        self.discovery = DiscoveryService(database=database)

        self.reviews = ReviewService(
            database=database,
            notification_service=notification_service,
            social_service=social_service,
        )

        self.favorites = FavoriteService(database=database)

        self.collections = CollectionService(database=database)

        self.moderation = ModerationService(
            database=database,
            notification_service=notification_service,
        )
