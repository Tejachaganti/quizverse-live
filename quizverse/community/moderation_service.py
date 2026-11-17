"""Community moderation queue, abuse reports, suspension, and audit logging."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    CommunityReport,
    CreatorProfile,
    ModerationAction,
    PlayerProfile,
    PlayerRole,
    Quiz,
    QuizPublication,
    QuizReview,
)


class ModerationService:
    """Manages community trust and safety, abuse reporting, and moderator actions."""

    VALID_REASONS = {
        "inappropriate",
        "offensive",
        "incorrect_answer",
        "plagiarism",
        "spam",
        "abusive_review",
        "harassment",
        "impersonation",
        "other",
    }

    VALID_TARGET_TYPES = {"quiz", "review", "creator", "question"}

    VALID_ACTIONS = {
        "suspend_quiz",
        "restore_quiz",
        "hide_review",
        "restore_review",
        "dismiss_report",
        "warn_creator",
    }

    def __init__(self, database: Database, notification_service: Any = None) -> None:
        self._database = database
        self._notification_service = notification_service

    def is_moderator(self, player_id: str) -> bool:
        """Verifies if player has the assigned moderator role in player_roles."""
        with self._database.session() as session:
            role = session.scalar(
                select(PlayerRole.id).where(
                    PlayerRole.player_id == player_id,
                    PlayerRole.role.in_(["moderator", "admin"]),
                ).limit(1)
            )
            return role is not None

    def grant_moderator_role(self, player_id: str, granted_by: str = "system") -> None:
        """Grants moderator capabilities to a player profile."""
        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            existing = session.scalar(
                select(PlayerRole).where(PlayerRole.player_id == player_id, PlayerRole.role == "moderator")
            )
            if not existing:
                session.add(
                    PlayerRole(
                        id=secrets.token_hex(16),
                        player_id=player_id,
                        role="moderator",
                        granted_by=granted_by,
                        created_at=now,
                    )
                )

    def submit_report(
        self,
        reporter_id: str,
        target_type: str,
        target_id: str,
        reason: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Submits a persistent moderation report with deduplication and privacy safeguards."""
        if target_type not in self.VALID_TARGET_TYPES:
            raise ValueError(f"Target type must be one of {self.VALID_TARGET_TYPES}")
        if reason not in self.VALID_REASONS:
            raise ValueError(f"Reason must be one of {self.VALID_REASONS}")

        clean_desc = description.strip() if description else None
        if clean_desc and len(clean_desc) > 2000:
            raise ValueError("Description must not exceed 2000 characters.")

        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            # Check for existing duplicate open report by same reporter
            existing = session.scalar(
                select(CommunityReport).where(
                    CommunityReport.reporter_id == reporter_id,
                    CommunityReport.target_type == target_type,
                    CommunityReport.target_id == target_id,
                    CommunityReport.status.in_(["open", "under_review"]),
                )
            )
            if existing:
                return {
                    "status": "already_reported",
                    "report_id": existing.id,
                    "message": "You already have an open report pending for this item.",
                }

            report = CommunityReport(
                id=secrets.token_hex(16),
                reporter_id=reporter_id,
                target_type=target_type,
                target_id=target_id,
                reason=reason,
                description=clean_desc,
                status="open",
                assigned_moderator_id=None,
                moderator_notes=None,
                resolution=None,
                created_at=now,
                updated_at=now,
                resolved_at=None,
            )
            session.add(report)
            session.flush()

            return {
                "status": "submitted",
                "report_id": report.id,
                "message": "Report received and queued for moderator review.",
            }

    def list_reports(
        self,
        moderator_id: str,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Lists reports in the moderation queue, restricted to authorized moderators."""
        if not self.is_moderator(moderator_id):
            raise PermissionError("Forbidden: Moderator privileges required.")

        bounded_limit = max(1, min(limit, 50))
        with self._database.session() as session:
            query = select(CommunityReport).order_by(CommunityReport.created_at.desc())
            if status:
                query = query.where(CommunityReport.status == status)

            total = session.scalar(select(func.count(CommunityReport.id))) or 0
            reports = session.scalars(query.offset(offset).limit(bounded_limit)).all()

            return {
                "total": total,
                "limit": bounded_limit,
                "offset": offset,
                "items": [self._serialize_report(r) for r in reports],
            }

    def get_report(self, moderator_id: str, report_id: str) -> dict[str, Any]:
        """Inspects full details of a report for an authorized moderator."""
        if not self.is_moderator(moderator_id):
            raise PermissionError("Forbidden: Moderator privileges required.")

        with self._database.session() as session:
            report = session.scalar(select(CommunityReport).where(CommunityReport.id == report_id))
            if not report:
                raise LookupError("Report not found.")
            return self._serialize_report(report)

    def resolve_report(
        self,
        moderator_id: str,
        report_id: str,
        action: str,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Resolves report, applies content suspension/restoration, and creates audit log."""
        if not self.is_moderator(moderator_id):
            raise PermissionError("Forbidden: Moderator privileges required.")

        if action not in self.VALID_ACTIONS:
            raise ValueError(f"Action must be one of {self.VALID_ACTIONS}")

        now = datetime.now(UTC)
        with self._database.sessions.begin() as session:
            report = session.scalar(select(CommunityReport).where(CommunityReport.id == report_id))
            if not report:
                raise LookupError("Report not found.")

            # Update report status
            new_status = "dismissed" if action == "dismiss_report" else "action_taken"
            report.status = new_status
            report.assigned_moderator_id = moderator_id
            report.moderator_notes = notes
            report.resolution = action
            report.resolved_at = now
            report.updated_at = now
            session.add(report)

            # Record audit action
            audit_action = ModerationAction(
                id=secrets.token_hex(16),
                report_id=report_id,
                moderator_id=moderator_id,
                action_type=action,
                target_type=report.target_type,
                target_id=report.target_id,
                notes=notes,
                created_at=now,
            )
            session.add(audit_action)

            # Apply state mutation to target content
            affected_creator_id: str | None = None
            quiz_title: str = "Quiz"

            if action == "suspend_quiz" and report.target_type in {"quiz", "question"}:
                quiz_id = report.target_id
                pub = session.scalar(select(QuizPublication).where(QuizPublication.quiz_id == quiz_id))
                if pub:
                    pub.status = "suspended"
                    pub.suspension_reason = notes or "Suspended by moderator review."
                    pub.updated_at = now
                    session.add(pub)
                    affected_creator_id = pub.creator_id

                quiz = session.scalar(select(Quiz).where(Quiz.id == quiz_id))
                if quiz:
                    quiz.visibility = "private"
                    quiz_title = quiz.title
                    session.add(quiz)

            elif action == "restore_quiz" and report.target_type in {"quiz", "question"}:
                quiz_id = report.target_id
                pub = session.scalar(select(QuizPublication).where(QuizPublication.quiz_id == quiz_id))
                if pub:
                    pub.status = "published"
                    pub.suspension_reason = None
                    pub.updated_at = now
                    session.add(pub)
                    affected_creator_id = pub.creator_id

                quiz = session.scalar(select(Quiz).where(Quiz.id == quiz_id))
                if quiz:
                    quiz.visibility = "public"
                    quiz_title = quiz.title
                    session.add(quiz)

            elif action == "hide_review" and report.target_type == "review":
                review = session.scalar(select(QuizReview).where(QuizReview.id == report.target_id))
                if review:
                    review.is_hidden = True
                    review.updated_at = now
                    session.add(review)

            elif action == "restore_review" and report.target_type == "review":
                review = session.scalar(select(QuizReview).where(QuizReview.id == report.target_id))
                if review:
                    review.is_hidden = False
                    review.updated_at = now
                    session.add(review)

        # Dispatch creator notification if content was moderated
        if affected_creator_id and self._notification_service is not None:
            notif_type = "quiz_suspended" if action == "suspend_quiz" else "quiz_restored"
            title = "Quiz Suspended by Moderation" if action == "suspend_quiz" else "Quiz Restored"
            msg = (
                f"Your quiz '{quiz_title}' has been suspended pending review: {notes or 'Content policy violation.'}"
                if action == "suspend_quiz"
                else f"Your quiz '{quiz_title}' has been reviewed and restored to the public community."
            )
            self._notification_service.create_notification(
                recipient_id=affected_creator_id,
                actor_id=moderator_id,
                notification_type=notif_type,
                title=title,
                body=msg,
                target_type="quiz",
                target_id=report.target_id,
                deduplication_key=f"mod:{report.target_id}:{now.timestamp()}",
            )

        return {
            "status": "resolved",
            "report_id": report_id,
            "action": action,
            "resolved_at": now.isoformat(),
        }

    def get_audit_log(self, moderator_id: str, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        """Retrieves audit trail of moderator actions."""
        if not self.is_moderator(moderator_id):
            raise PermissionError("Forbidden: Moderator privileges required.")

        bounded_limit = max(1, min(limit, 50))
        with self._database.session() as session:
            actions = session.scalars(
                select(ModerationAction)
                .options(selectinload(ModerationAction.moderator))
                .order_by(ModerationAction.created_at.desc())
                .offset(offset)
                .limit(bounded_limit)
            ).all()

            return [
                {
                    "action_id": a.id,
                    "report_id": a.report_id,
                    "moderator_id": a.moderator_id,
                    "moderator_name": a.moderator.display_name if a.moderator else "Moderator",
                    "action_type": a.action_type,
                    "action": a.action_type,
                    "target_type": a.target_type,
                    "target_id": a.target_id,
                    "notes": a.notes,
                    "created_at": a.created_at.isoformat(),
                }
                for a in actions
            ]

    def _serialize_report(self, r: CommunityReport) -> dict[str, Any]:
        return {
            "id": r.id,
            "reporter_id": r.reporter_id,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "reason": r.reason,
            "description": r.description,
            "status": r.status,
            "assigned_moderator_id": r.assigned_moderator_id,
            "moderator_notes": r.moderator_notes,
            "resolution": r.resolution,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        }
